"""backend.main — FastAPI control plane: все роуты + WebSocket живого статуса.

Вебка (frontend-контейнер) ходит сюда за /api/*. Доступ подразумевается через
админский VPN-конфиг; поверх — простая сессия (admin/admin по умолчанию).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Dict, List, Optional

from fastapi import (Depends, FastAPI, File, HTTPException, Request, Response,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import (admin_vpn, agents, backup, config, console, db, diff, exploitation, loot,
               orchestrator, reports, targets, topology, updates, users, vpn)

app = FastAPI(title="pintest-host", version=config.VERSION)


# ------------------------------ авторизация --------------------------------
def _token(user: str) -> str:
    sig = hmac.new(config.SESSION_SECRET.encode(), user.encode(), hashlib.sha256).hexdigest()
    return f"{user}:{sig}"


def _valid(tok: str) -> bool:
    if not tok or ":" not in tok:
        return False
    user, _ = tok.split(":", 1)
    return hmac.compare_digest(tok, _token(user))


def require_auth(request: Request):
    tok = request.cookies.get("pintest_session", "")
    if not _valid(tok):
        raise HTTPException(401, "нужна авторизация")
    return True


def current_login(request: Request) -> str:
    tok = request.cookies.get("pintest_session", "")
    if not _valid(tok):
        raise HTTPException(401, "нужна авторизация")
    return tok.split(":", 1)[0]


class LoginIn(BaseModel):
    user: str
    password: str


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    if users.verify(body.user, body.password):
        response.set_cookie("pintest_session", _token(body.user), httponly=True, samesite="lax")
        return {"ok": True, "user": body.user}
    raise HTTPException(401, "неверные логин/пароль")


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("pintest_session")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    tok = request.cookies.get("pintest_session", "")
    login = tok.split(":", 1)[0] if _valid(tok) else None
    return {"authenticated": _valid(tok), "login": login}


# ------------------------ настройки / учётные записи -----------------------
class CredIn(BaseModel):
    login: str = ""
    password: str = ""


class UserIn(BaseModel):
    login: str
    password: str


@app.post("/api/settings/credentials")
def api_change_creds(body: CredIn, request: Request, response: Response):
    cur = current_login(request)
    try:
        new = users.change_credentials(cur, body.login, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    response.set_cookie("pintest_session", _token(new), httponly=True, samesite="lax")
    return {"ok": True, "login": new}


@app.get("/api/users")
def api_users(_: bool = Depends(require_auth)):
    return users.list_users()


@app.post("/api/users")
def api_user_create(body: UserIn, _: bool = Depends(require_auth)):
    try:
        users.create(body.login, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/users/{login}")
def api_user_delete(login: str, _: bool = Depends(require_auth)):
    try:
        users.delete(login)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ------------------------------ обзор --------------------------------------
@app.get("/api/overview")
def overview(_: bool = Depends(require_auth)):
    ags = agents.list_agents()
    by_status: Dict[str, int] = {}
    for a in ags:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    jobs = reports.list_jobs()
    fcount = db.one("SELECT count(*) c FROM findings")["c"]
    ccount = db.one("SELECT count(*) c FROM captures WHERE phase='capture' AND success=true")["c"]
    return {
        "agents": {"total": len(ags), "by_status": by_status},
        "jobs": {"total": len(jobs),
                 "running": sum(1 for j in jobs if j["status"] == "running")},
        "findings": fcount, "captured": ccount,
        "vpn": vpn.status(), "version": config.VERSION,
    }


# ------------------------------ агенты -------------------------------------
class AgentIn(BaseModel):
    name: str
    ssh_host: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str
    full_deploy: bool = False        # True — хост сам развернёт агента на ноде (без git); False — только вброс туннеля


@app.get("/api/agents")
def api_agents(_: bool = Depends(require_auth)):
    return agents.list_agents()


@app.post("/api/agents")
async def api_add_agent(body: AgentIn, _: bool = Depends(require_auth)):
    # provisioning блокирующий (paramiko) -> в threadpool
    return await run_in_threadpool(
        agents.add_agent, body.name, body.ssh_host, body.ssh_port, body.ssh_user, body.ssh_password,
        body.full_deploy)


@app.delete("/api/agents/{aid}")
def api_del_agent(aid: str, _: bool = Depends(require_auth)):
    agents.remove_agent(aid)
    return {"ok": True}


@app.post("/api/agents/{aid}/role/{role}")
async def api_assign_role(aid: str, role: str, _: bool = Depends(require_auth)):
    try:
        return await run_in_threadpool(agents.assign_role, aid, role)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.delete("/api/agents/{aid}/role/{role}")
async def api_revoke_role(aid: str, role: str, _: bool = Depends(require_auth)):
    return await run_in_threadpool(agents.revoke_role, aid, role)


@app.post("/api/agents/{aid}/destroy")
def api_destroy(aid: str, _: bool = Depends(require_auth)):
    return agents.destroy(aid)


class AgentUpdateIn(BaseModel):
    transport: str = "api"        # api|ssh
    ssh_password: str = ""
    version: str = ""


@app.post("/api/agents/{aid}/update")
async def api_agent_update(aid: str, body: AgentUpdateIn, _: bool = Depends(require_auth)):
    if body.transport == "ssh":
        return await run_in_threadpool(updates.push_agent_ssh, aid, body.ssh_password, body.version)
    return await run_in_threadpool(updates.push_agent_api, aid, body.version)


# ------------------------------ цели ---------------------------------------
class TargetsIn(BaseModel):
    raw: str


@app.post("/api/targets")
def api_targets(body: TargetsIn, _: bool = Depends(require_auth)):
    return targets.ingest(body.raw)


@app.get("/api/targets")
def api_targets_get(_: bool = Depends(require_auth)):
    return targets.latest() or {"targets": [], "count": 0}


@app.get("/api/topology")
def api_topology(_: bool = Depends(require_auth)):
    """Целевой слой топологии (хост/агенты фронт берёт из /api/live)."""
    return topology.build()


@app.get("/api/graph")
def api_graph(_: bool = Depends(require_auth)):
    """Полный граф сети: узлы + рёбра (control/scan/exploit/reroute) + достижимость."""
    return topology.graph()


# ------------------------------ джобы --------------------------------------
class JobIn(BaseModel):
    opts: Dict = {}
    mode: str = "parallel"        # parallel|sequential
    diff_against: Optional[str] = None


@app.post("/api/jobs")
async def api_start_job(body: JobIn, _: bool = Depends(require_auth)):
    canon = targets.latest()
    if not canon or not canon.get("targets"):
        raise HTTPException(400, "сначала загрузи список целей")
    jid = orchestrator.start_job(body.opts, body.mode, canon, body.diff_against)
    return {"job_id": jid}


@app.get("/api/jobs")
def api_jobs(_: bool = Depends(require_auth)):
    return reports.list_jobs()


@app.get("/api/jobs/{jid}")
def api_job(jid: str, _: bool = Depends(require_auth)):
    j = reports.job(jid)
    if not j:
        raise HTTPException(404, "джоба не найдена")
    return j


@app.post("/api/jobs/{jid}/stop")
def api_job_stop(jid: str, _: bool = Depends(require_auth)):
    r = orchestrator.JOBS.get(jid)
    if r:
        r.stop()
    return {"ok": True}


@app.get("/api/jobs/{jid}/report")
def api_job_report(jid: str, _: bool = Depends(require_auth)):
    p = reports.artifact(jid, "report.html")
    if not p:
        raise HTTPException(404, "отчёт ещё не готов")
    return FileResponse(p, media_type="text/html")


@app.get("/api/jobs/{jid}/artifact/{name}")
def api_job_artifact(jid: str, name: str, _: bool = Depends(require_auth)):
    p = reports.artifact(jid, name)
    if not p:
        raise HTTPException(404, "нет такого артефакта")
    return FileResponse(p, filename=f"{jid}_{name}")


# ------------------------------ DIFF ---------------------------------------
@app.get("/api/diff")
def api_diff(a: str, b: str, _: bool = Depends(require_auth)):
    return diff.compare(a, b)


# --------------------------- эксплуатация ----------------------------------
@app.get("/api/jobs/{jid}/exploitable")
def api_exploitable(jid: str, _: bool = Depends(require_auth)):
    return exploitation.exploitable(jid)


@app.get("/api/exploiters")
def api_exploiters(_: bool = Depends(require_auth)):
    return exploitation.exploiter_agents()


class ExploitIn(BaseModel):
    agent_id: str
    host: str
    cve: str
    port: int = 0
    confirm: bool = False


@app.post("/api/exploit/check")
async def api_exploit_check(body: ExploitIn, _: bool = Depends(require_auth)):
    return await run_in_threadpool(exploitation.run_check, body.agent_id, body.host, body.cve, body.port)


@app.post("/api/exploit/capture")
async def api_exploit_capture(body: ExploitIn, _: bool = Depends(require_auth)):
    """Закрепление — только с confirm=True (иначе отказ, гейт подтверждения)."""
    return await run_in_threadpool(
        exploitation.run_capture, body.agent_id, body.host, body.cve, body.port, body.confirm)


class AutoExploitIn(BaseModel):
    agent_id: str
    host: Dict                  # находка скана: {"ip","ports","cves"}
    confirm: bool = False
    min_cvss: float = 7.0
    max_targets: int = 25


@app.post("/api/exploit/auto")
async def api_exploit_auto(body: AutoExploitIn, _: bool = Depends(require_auth)):
    """Real-time автоэксплуатация хоста: движок сам подбирает CVE, проверяет и (с confirm) берёт."""
    return await run_in_threadpool(exploitation.run_auto, body.agent_id, body.host,
                                   body.confirm, body.min_cvss, body.max_targets)


class PivotAutoIn(BaseModel):
    agent_id: str
    pivot_host: str
    pivot_cve: str
    subnet: str
    confirm: bool = False


@app.post("/api/pivot/auto")
async def api_pivot_auto(body: PivotAutoIn, _: bool = Depends(require_auth)):
    """Self-spreading: через захваченный плацдарм скан скрытой сети + авто-захват найденного."""
    return await run_in_threadpool(exploitation.run_pivot_auto, body.agent_id,
                                   body.pivot_host, body.pivot_cve, body.subnet, body.confirm)


class AutoIpIn(BaseModel):
    agent_id: str
    ip: str
    confirm: bool = False
    min_cvss: float = 7.0


@app.post("/api/exploit/auto_ip")
async def api_exploit_auto_ip(body: AutoIpIn, _: bool = Depends(require_auth)):
    """Авто-эксплуатация цели по IP (находка из последней джобы) — для действия «по клику на графе»."""
    return await run_in_threadpool(exploitation.run_auto_ip, body.agent_id, body.ip,
                                   body.confirm, body.min_cvss)


@app.get("/api/captures")
def api_captures(_: bool = Depends(require_auth)):
    return exploitation.captures()


@app.get("/api/console/targets")
def api_console_targets(_: bool = Depends(require_auth)):
    """Захваченные цели, к которым можно открыть консоль (через их foothold)."""
    return exploitation.captured_targets()


class TargetShellIn(BaseModel):
    agent_id: str
    target: str
    cve: str
    cmd: str


@app.post("/api/console/target/exec")
async def api_target_shell(body: TargetShellIn, _: bool = Depends(require_auth)):
    """Команда на захваченной цели через её foothold (командная консоль цели)."""
    try:
        return await run_in_threadpool(exploitation.target_shell, body.agent_id, body.target, body.cve, body.cmd)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


# ------------------------------ pivot --------------------------------------
class PivotIn(BaseModel):
    agent_id: str
    host: str
    cve: str
    subnet: str = "10.66.0"


@app.post("/api/pivot/scan")
async def api_pivot_scan(body: PivotIn, _: bool = Depends(require_auth)):
    """Развед-скан скрытой сети через захваченный узел (реальный pivot)."""
    try:
        return await run_in_threadpool(exploitation.pivot_scan, body.agent_id, body.host, body.cve, body.subnet)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.get("/api/pivot/hosts")
def api_pivot_hosts(_: bool = Depends(require_auth)):
    return exploitation.pivot_hosts()


class PivotExploitIn(BaseModel):
    agent_id: str
    pivot_host: str
    pivot_cve: str
    hidden_target: str
    hidden_cve: str
    port: int = 0


@app.post("/api/pivot/exploit")
async def api_pivot_exploit(body: PivotExploitIn, _: bool = Depends(require_auth)):
    """Эксплуатация скрытой цели через захваченный плацдарм (цепочка pivot)."""
    try:
        return await run_in_threadpool(
            exploitation.pivot_exploit, body.agent_id, body.pivot_host, body.pivot_cve,
            body.hidden_target, body.hidden_cve, body.port)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


# ------------------------------ лут ----------------------------------------
@app.get("/api/loot")
def api_loot(_: bool = Depends(require_auth)):
    return loot.summary()


@app.get("/api/loot/report.md")
def api_loot_md(_: bool = Depends(require_auth)):
    return PlainTextResponse(loot.report_md(), media_type="text/markdown; charset=utf-8")


@app.get("/api/loot/report.json")
def api_loot_json(_: bool = Depends(require_auth)):
    return JSONResponse(loot.items())


@app.get("/api/loot/report.html")
def api_loot_html(_: bool = Depends(require_auth)):
    from fastapi.responses import HTMLResponse
    return HTMLResponse(loot.report_html())


# ------------------------------ консоль ------------------------------------
class ConsoleOpenIn(BaseModel):
    cols: int = 120
    rows: int = 30


class ConsoleInputIn(BaseModel):
    data: str = ""


class ConsoleSizeIn(BaseModel):
    cols: int = 120
    rows: int = 30


@app.post("/api/console/{aid}")
async def api_console_open(aid: str, body: ConsoleOpenIn, _: bool = Depends(require_auth)):
    try:
        return await run_in_threadpool(console.open, aid, body.cols, body.rows)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.post("/api/console/{aid}/{sid}/input")
async def api_console_input(aid: str, sid: str, body: ConsoleInputIn, _: bool = Depends(require_auth)):
    return await run_in_threadpool(console.write, aid, sid, body.data)


@app.get("/api/console/{aid}/{sid}/output")
async def api_console_output(aid: str, sid: str, since: int = 0, _: bool = Depends(require_auth)):
    return await run_in_threadpool(console.read, aid, sid, since)


@app.post("/api/console/{aid}/{sid}/resize")
async def api_console_resize(aid: str, sid: str, body: ConsoleSizeIn, _: bool = Depends(require_auth)):
    return await run_in_threadpool(console.resize, aid, sid, body.cols, body.rows)


@app.delete("/api/console/{aid}/{sid}")
async def api_console_close(aid: str, sid: str, _: bool = Depends(require_auth)):
    return await run_in_threadpool(console.close, aid, sid)


# ------------------------------ VPN ----------------------------------------
class AdminVpnIn(BaseModel):
    name: str = ""


@app.post("/api/vpn/admin")
def api_vpn_admin_create(body: AdminVpnIn, _: bool = Depends(require_auth)):
    """Сгенерировать админский VPN-конфиг (ключи + пир + .conf для скачивания)."""
    try:
        return admin_vpn.create(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/vpn/admin")
def api_vpn_admin_list(_: bool = Depends(require_auth)):
    return admin_vpn.list_()


@app.get("/api/vpn/admin/{name}/conf")
def api_vpn_admin_conf(name: str, _: bool = Depends(require_auth)):
    c = admin_vpn.conf(name)
    if c is None:
        raise HTTPException(404, "конфиг не найден")
    return PlainTextResponse(c, headers={"Content-Disposition": f'attachment; filename="{name}.conf"'})


@app.delete("/api/vpn/admin/{name}")
def api_vpn_admin_delete(name: str, _: bool = Depends(require_auth)):
    try:
        return admin_vpn.delete(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/vpn")
def api_vpn(_: bool = Depends(require_auth)):
    return vpn.status()


# ------------------------------ бэкапы -------------------------------------
@app.get("/api/backups")
def api_backups(_: bool = Depends(require_auth)):
    return backup.list_backups()


@app.post("/api/backups")
async def api_backup_create(_: bool = Depends(require_auth)):
    return await run_in_threadpool(backup.create, "manual")


@app.get("/api/backups/{name}/download")
def api_backup_download(name: str, _: bool = Depends(require_auth)):
    try:
        p = backup.path_of(name)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "бэкап не найден")
    return FileResponse(p, filename=p.name, media_type="application/gzip")


@app.post("/api/backups/upload")
async def api_backup_upload(file: UploadFile = File(...), _: bool = Depends(require_auth)):
    raw = await file.read()
    try:
        return await run_in_threadpool(backup.save_uploaded, raw, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/backups/{name}")
async def api_backup_delete(name: str, _: bool = Depends(require_auth)):
    try:
        return await run_in_threadpool(backup.delete, name)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "бэкап не найден")


@app.post("/api/backups/{name}/restore")
async def api_backup_restore(name: str, _: bool = Depends(require_auth)):
    try:
        return await run_in_threadpool(backup.restore, name)
    except FileNotFoundError:
        raise HTTPException(404, "бэкап не найден")
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------- обновление хоста ------------------------------
class HostUpdateIn(BaseModel):
    method: str = "git"           # git|scp
    bundle_b64: str = ""


@app.post("/api/update/host")
async def api_update_host(body: HostUpdateIn, _: bool = Depends(require_auth)):
    if body.method == "scp":
        return await run_in_threadpool(updates.host_update_scp, body.bundle_b64)
    return await run_in_threadpool(updates.host_update_git)


@app.post("/api/maintenance/pause")
async def api_maintenance_pause(minutes: int = 20, _: bool = Depends(require_auth)):
    """Объявить агентам плановый простой (перед ручным ребутом хоста) — не самоуничтожатся."""
    return await run_in_threadpool(agents.pause_deadman_all, minutes)


@app.get("/api/update/status")
async def api_update_status(_: bool = Depends(require_auth)):
    return await run_in_threadpool(updates.host_update_status)


def _safe_topology() -> Dict:
    """Целевой слой для live — не роняем сокет, если БД временно недоступна."""
    try:
        return topology.build()
    except Exception:  # noqa: BLE001
        return {"targets": [], "counts": {}}


# --------------------------- WebSocket живого статуса ----------------------
@app.websocket("/api/live")
async def live(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            ags = agents.list_agents()
            running = [reports.job(j["id"]) for j in reports.list_jobs()
                       if j["status"] == "running"]
            payload = {
                "t": time.time(),
                "agents": [{
                    "id": a["id"], "name": a["name"], "status": a["status"],
                    "tunnel_ip": a["tunnel_ip"], "roles": a["roles"],
                    "ssh_port": a.get("ssh_port"),
                    "cpu": a.get("live", {}).get("cpu", []),
                    "mem": a.get("live", {}).get("mem", []),
                    "caps": (a.get("live", {}).get("health", {}) or {}).get("metrics", {}),
                    "last_seen": a["last_seen"],
                } for a in ags],
                "jobs": running,
                "vpn": vpn.status(),
                "host_ip": config.HOST_TUNNEL_IP,
                "topology": _safe_topology(),
            }
            await ws.send_json(payload)
            await asyncio.sleep(config.LIVE_INTERVAL)
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        return


# ------------------------------ старт --------------------------------------
@app.on_event("startup")
async def _startup():
    config.ensure_dirs()
    await run_in_threadpool(db.init)
    await run_in_threadpool(users.seed_admin)
    await run_in_threadpool(admin_vpn.ensure_bootstrap)   # первый вход: bootstrap admin VPN
    asyncio.create_task(agents.heartbeat_loop())
    asyncio.create_task(backup.daily_loop())              # ежедневный авто-снимок состояния

    asyncio.create_task(agents.cleanup_loop())            # периодич. чистка дашборда: ретайр
                                                          # давно-потерянных нод + снятие висячих awg-пиров


@app.get("/api/health")
def health():
    return {"ok": True, "version": config.VERSION, "ssh_port": config.SSH_PORT}
