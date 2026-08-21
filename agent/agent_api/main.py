"""agent_api.main — FastAPI-приложение агента (exit node).

Слушает на туннеле (по умолчанию 0.0.0.0:9101). Хост общается с агентом ТОЛЬКО
через этот API поверх AmneziaWG. Трафик самих сканов идёт с агента напрямую в
целевую сеть; на хост возвращаются лишь результаты.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import config, console, deadman, exploit_runner, roles, updater
from .runner import registry

try:
    import psutil
except Exception:  # noqa: BLE001 — psutil опционален
    psutil = None

config.ensure_dirs()
app = FastAPI(title="pintest-agent", version=config.VERSION)
_BOOT = time.time()


# ------------------------------- модели ------------------------------------
class ChunkIn(BaseModel):
    job_id: str
    chunk_id: str
    targets: List[str]
    opts: Dict = {}


class UpdateIn(BaseModel):
    bundle_b64: str
    version: str = ""


class ExploitCheckIn(BaseModel):
    target: str
    cve: str
    port: int = 0


class ExploitCaptureIn(BaseModel):
    target: str
    cve: str
    port: int = 0
    confirm: bool = False       # человек подтвердил закрепление в UI


class PivotIn(BaseModel):
    target: str
    cve: str
    subnet: str
    ports: List[int] = []


class PivotExploitIn(BaseModel):
    pivot_host: str
    pivot_cve: str
    hidden_target: str
    hidden_cve: str
    port: int = 0


class ShellExecIn(BaseModel):
    target: str
    cve: str
    cmd: str


class ConsoleOpenIn(BaseModel):
    cols: int = 120
    rows: int = 30


class ConsoleInputIn(BaseModel):
    data: str = ""


class ConsoleSizeIn(BaseModel):
    cols: int = 120
    rows: int = 30


# ------------------------------ хелперы ------------------------------------
def _awg_status() -> Dict:
    """Статус туннеля: поднят ли интерфейс + был ли handshake."""
    up = subprocess.run(["ip", "link", "show", config.AWG_IFACE],
                        capture_output=True).returncode == 0
    handshake = False
    peer = ""
    if shutil.which("awg"):
        r = subprocess.run(["awg", "show", config.AWG_IFACE], capture_output=True, text=True)
        out = r.stdout
        handshake = "latest handshake" in out
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("peer:"):
                peer = line.split(":", 1)[1].strip()
    return {"iface": config.AWG_IFACE, "up": up, "handshake": handshake, "peer": peer}


def _metrics() -> Dict:
    if not psutil:
        return {"cpu": 0.0, "mem": 0.0}
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "mem": psutil.virtual_memory().percent,
    }


# ------------------------------ эндпоинты ----------------------------------
@app.get("/health")
def health():
    """Хартбит: метрики + статус туннеля + активные чанки. Хост опрашивает часто."""
    return {
        "ok": True,
        "name": config.AGENT_NAME,
        "hostname": socket.gethostname(),
        "version": config.VERSION,
        "uptime": round(time.time() - _BOOT, 1),
        "metrics": _metrics(),
        "awg": _awg_status(),
        "deadman": deadman.status(),
        "work": registry.summary(),
        "roles": roles.state(),
        "destroyed": config.TOMBSTONE.exists(),
    }


@app.get("/awg")
def awg():
    return _awg_status()


@app.post("/chunk")
def take_chunk(body: ChunkIn):
    """Принять чанк и начать скан. Возврат — сразу, работа идёт в фоне."""
    if config.TOMBSTONE.exists():
        raise HTTPException(410, "агент самоуничтожен")
    if not body.targets:
        raise HTTPException(400, "пустой список целей")
    run = registry.start_chunk(body.job_id, body.chunk_id, body.targets, body.opts)
    return {"accepted": True, **run.progress()}


@app.get("/chunk/{job_id}/{chunk_id}")
def chunk_status(job_id: str, chunk_id: str):
    run = registry.get(job_id, chunk_id)
    if not run:
        raise HTTPException(404, "чанк не найден")
    return run.progress()


@app.get("/chunk/{job_id}/{chunk_id}/results")
def chunk_results(job_id: str, chunk_id: str, since: int = 0):
    """Новые строки results.jsonl начиная с индекса since — хост их сольёт в отчёт."""
    run = registry.get(job_id, chunk_id)
    if not run:
        raise HTTPException(404, "чанк не найден")
    return run.results_since(since)


@app.post("/chunk/{job_id}/{chunk_id}/cancel")
def chunk_cancel(job_id: str, chunk_id: str):
    run = registry.get(job_id, chunk_id)
    if not run:
        raise HTTPException(404, "чанк не найден")
    run.cancel()
    return {"cancelled": True}


# ---- роли и «донастройка» -------------------------------------------------
@app.get("/roles")
def get_roles():
    return roles.state()


@app.post("/role/{role}")
def assign_role(role: str):
    """Назначить роль ноде и выполнить донастройку (для exploiter — установка модулей)."""
    try:
        return roles.assign(role)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/role/{role}")
def revoke_role(role: str):
    try:
        return roles.revoke(role)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---- эксплуатация: две раздельные фазы ------------------------------------
@app.post("/exploit/check")
def exploit_check(body: ExploitCheckIn):
    """Фаза 1 — безопасная проверка легитимности CVE (без закрепления)."""
    return exploit_runner.check(body.target, body.cve, body.port)


@app.post("/exploit/capture")
def exploit_capture(body: ExploitCaptureIn):
    """Фаза 2 — закрепление. Требует роли exploiter и confirm=True (подтверждение)."""
    res = exploit_runner.capture(body.target, body.cve, body.port, confirm=body.confirm)
    if not res.get("ok") and res.get("needs_confirmation"):
        raise HTTPException(412, res["error"])   # 412 Precondition Failed — нет подтверждения
    return res


@app.post("/update")
def update(body: UpdateIn):
    """Применить бандл обновления, присланный хостом, и перезапуститься."""
    try:
        res = updater.apply_bundle(body.bundle_b64, body.version)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"не удалось применить обновление: {e}")
    updater.restart_soon()
    return res


# ---- pivot: разведка скрытой сети через захваченный узел -------------------
@app.post("/exploit/shell")
def exploit_shell(body: ShellExecIn):
    """Команда на захваченной цели через её foothold (консоль цели). Нужна роль exploiter."""
    from . import roles as _roles
    if not _roles.has_role("exploiter"):
        return {"ok": False, "error": "нет роли exploiter", "output": ""}
    return exploit_runner.shell_exec_target(body.target, body.cve, body.cmd)


@app.post("/pivot")
def pivot(body: PivotIn):
    """Развед-скан скрытой подсети через плацдарм (реальный pivot). Требует роли exploiter."""
    return exploit_runner.pivot(body.target, body.cve, body.subnet, body.ports or None)


# ---- интерактивная консоль (PTY) ------------------------------------------
@app.post("/console")
def console_open(body: ConsoleOpenIn):
    """Открыть новую shell-сессию (bash под pty). Вернёт sid."""
    return console.open_session(body.cols, body.rows)


@app.post("/pivot/exploit")
def pivot_exploit(body: PivotExploitIn):
    """Эксплуатация СКРЫТОЙ цели через захваченный плацдарм (цепочка pivot)."""
    return exploit_runner.pivot_exploit(body.pivot_host, body.pivot_cve,
                                        body.hidden_target, body.hidden_cve, body.port)


@app.get("/console")
def console_list():
    return console.list_sessions()


@app.post("/console/{sid}/input")
def console_input(sid: str, body: ConsoleInputIn):
    """Отправить сырые байты клавиш в сессию."""
    return console.write_session(sid, body.data)


@app.get("/console/{sid}/output")
def console_output(sid: str, since: int = 0):
    """Забрать новый вывод начиная со смещения since."""
    return console.read_session(sid, since)


@app.post("/console/{sid}/resize")
def console_resize(sid: str, body: ConsoleSizeIn):
    return console.resize_session(sid, body.cols, body.rows)


@app.delete("/console/{sid}")
def console_close(sid: str):
    return console.close_session(sid)


@app.post("/destroy")
def destroy():
    """Ручной триггер самоуничтожения (для демонстрации в лабе)."""
    registry.cancel_all()
    deadman.self_destruct("ручной триггер с хоста")
    return JSONResponse({"destroying": True})


@app.on_event("startup")
def _startup():
    deadman.start()
