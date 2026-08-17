"""backend.updates — обновления. ДВЕ разные механики (требование ТЗ):

  ХОСТ обновляет СЕБЯ сам:      git pull  ИЛИ  распаковка scp-бандла поверх репо.
  АГЕНТЫ обновляются С ХОСТА:   доставкой бандла (по API-туннелю или по SSH) — агент
                                код сам не тянет.
"""
from __future__ import annotations

import base64
import io
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Dict, List

import httpx

from . import agents, config, provisioner

# --------------------------- обновление ХОСТА ------------------------------
def host_update_git() -> Dict:
    repo = config.HOST_REPO_DIR
    try:
        r = subprocess.run(["git", "-C", repo, "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=120)
        log = (r.stdout + r.stderr).strip().splitlines()
        return {"ok": r.returncode == 0, "mechanic": "git", "log": log,
                "note": "перезапусти стек: docker compose up -d --build"}
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
