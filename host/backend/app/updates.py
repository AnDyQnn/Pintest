"""backend.updates — обновления. ДВЕ разные механики (требование ТЗ):

  ХОСТ обновляет СЕБЯ сам:      git pull  ИЛИ  распаковка scp-бандла поверх репо.
  АГЕНТЫ обновляются С ХОСТА:   доставкой бандла (по API-туннелю или по SSH) — агент
                                код сам не тянет.
"""
from __future__ import annotations

import base64
import io
import json
import tarfile
import time
from pathlib import Path
from typing import Dict

import httpx

from . import agents, config, provisioner

# --------------------------- обновление ХОСТА ------------------------------
# ВАЖНО: бэкенд крутится В КОНТЕЙНЕРЕ и НЕ может пересобрать/перезапустить сам
# себя (ни репы /opt/pintest внутри нет, ни docker внутри). Поэтому «обновить
# хост» = не git pull здесь, а ЗАЯВКА внешнему демону: пишем маркер в /data
# (bind-mount ./data -> /data, значит файл ложится в host/data/.update-request),
# а на самом хосте крутится  sudo bash host/update.sh --watch , который ловит
# маркер и делает git reset --hard + docker compose up --build с авто-откатом.
UPDATE_MARKER = config.DATA_DIR / ".update-request"
UPDATE_STATUS = config.DATA_DIR / ".update-status"


def host_update_status() -> Dict:
    """Исход последнего обновления (пишет host/update.sh) — вебка показывает результат."""
    try:
        if UPDATE_STATUS.exists():
            return json.loads(UPDATE_STATUS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {"status": "none"}


def host_update_git() -> Dict:
    try:
        # автобэкап ПЕРЕД обновлением (как в gateway update — есть куда откатиться)
        pre = ""
        try:
            from . import backup
            pre = backup.create(reason="update")["name"]
        except Exception:  # noqa: BLE001 — бэкап не должен блокировать обновление
            pre = ""
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPDATE_MARKER.write_text(str(time.time()), encoding="utf-8")
        return {"ok": True, "mechanic": "git", "log": [
            (f"автобэкап перед обновлением: {pre}" if pre else "автобэкап не удался (продолжаю)"),
            "заявка на обновление хоста поставлена (маркер host/data/.update-request).",
            "внешний демон  sudo bash host/update.sh --watch  выполнит:",
            "  git fetch + git reset --hard <remote>/<branch> + docker compose up -d --build",
            "с авто-откатом на прежний коммит при любой ошибке.",
            "если демон не запущен — выполни разово:  sudo bash host/update.sh",
        ], "note": "стек перезапустится внешним демоном (контейнер не рестартит сам себя)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mechanic": "git", "log": [str(e)]}


def host_update_scp(bundle_b64: str) -> Dict:
    raw = base64.b64decode(bundle_b64)
    repo = Path(config.HOST_REPO_DIR)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        tar.extractall(repo)
    return {"ok": True, "mechanic": "scp", "files": len(raw),
            "note": "перезапусти стек: docker compose up -d --build"}


# --------------------------- обновление АГЕНТОВ ----------------------------
def build_agent_bundle() -> bytes:
    """Собрать tar.gz с core/, exploits/, agent/ из репо хоста — для доставки агентам."""
    repo = Path(config.HOST_REPO_DIR)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for sub in ("core", "exploits", "agent/agent_api", "agent/scripts"):
            p = repo / sub
            if p.exists():
                tar.add(p, arcname=sub)
    return buf.getvalue()


def push_agent_api(agent_id: str, version: str = "") -> Dict:
    """Механика по туннелю: POST /update с бандлом (без SSH-пароля)."""
    a = agents.get(agent_id)
    if not a:
        raise KeyError("агент не найден")
    b64 = base64.b64encode(build_agent_bundle()).decode()
    url = f"http://{a['tunnel_ip']}:{config.AGENT_API_PORT}/update"
    r = httpx.post(url, json={"bundle_b64": b64, "version": version or config.VERSION}, timeout=60)
    return {"ok": r.status_code == 200, "transport": "api", "response": r.json()}


def push_agent_ssh(agent_id: str, ssh_password: str, version: str = "") -> Dict:
    """Механика по SSH: доставка бандла и apply-update.sh (нужен пароль ноды)."""
    a = agents.get(agent_id)
    if not a:
        raise KeyError("агент не найден")
    t = provisioner.SSHTarget(a["ssh_host"], a["ssh_port"], a["ssh_user"], ssh_password)
    ok, log = provisioner.push_update(t, build_agent_bundle(), version or config.VERSION)
    return {"ok": ok, "transport": "ssh", "log": log}
