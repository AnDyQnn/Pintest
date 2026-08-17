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

from fastapi import (Depends, FastAPI, HTTPException, Request, Response,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import (agents, backup, config, db, diff, exploitation, orchestrator,
               reports, targets, updates, vpn)

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


class LoginIn(BaseModel):
    user: str
    password: str


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    if body.user == config.ADMIN_USER and body.password == config.ADMIN_PASSWORD:
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
    return {"authenticated": _valid(tok)}


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


@app.get("/api/agents")
def api_agents(_: bool = Depends(require_auth)):
    return agents.list_agents()


@app.post("/api/agents")
async def api_add_agent(body: AgentIn, _: bool = Depends(require_auth)):
    # provisioning блокирующий (paramiko) -> в threadpool
    return await run_in_threadpool(
        agents.add_agent, body.name, body.ssh_host, body.ssh_port, body.ssh_user, body.ssh_password)


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


@app.get("/api/captures")
def api_captures(_: bool = Depends(require_auth)):
    return exploitation.captures()


# ------------------------------ VPN ----------------------------------------
@app.get("/api/vpn")
def api_vpn(_: bool = Depends(require_auth)):
    return vpn.status()


# ------------------------------ бэкапы -------------------------------------
@app.get("/api/backups")
def api_backups(_: bool = Depends(require_auth)):
    return backup.list_backups()


@app.post("/api/backups")
def api_backup_create(_: bool = Depends(require_auth)):
    return backup.create()


@app.post("/api/backups/{name}/restore")
def api_backup_restore(name: str, _: bool = Depends(require_auth)):
    try:
        return backup.restore(name)
    except FileNotFoundError:
        raise HTTPException(404, "бэкап не найден")


# --------------------------- обновление хоста ------------------------------
class HostUpdateIn(BaseModel):
    method: str = "git"           # git|scp
    bundle_b64: str = ""


@app.post("/api/update/host")
async def api_update_host(body: HostUpdateIn, _: bool = Depends(require_auth)):
    if body.method == "scp":
        return await run_in_threadpool(updates.host_update_scp, body.bundle_b64)
    return await run_in_threadpool(updates.host_update_git)


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
                    "cpu": a.get("live", {}).get("cpu", []),
                    "mem": a.get("live", {}).get("mem", []),
                    "last_seen": a["last_seen"],
                } for a in ags],
                "jobs": running,
                "vpn": vpn.status(),
                "host_ip": config.HOST_TUNNEL_IP,
            }
            await ws.send_json(payload)
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        return


# ------------------------------ старт --------------------------------------
@app.on_event("startup")
async def _startup():
    config.ensure_dirs()
    await run_in_threadpool(db.init)
    asyncio.create_task(agents.heartbeat_loop())


@app.get("/api/health")
def health():
    return {"ok": True, "version": config.VERSION}
