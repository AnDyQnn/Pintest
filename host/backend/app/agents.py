"""backend.agents — реестр агентов, provisioning, heartbeat, роли.

Полный цикл подключения ноды: сгенерировать ключи (vpn), выделить туннельный IP,
собрать клиент-конфиг, по SSH вбросить и поднять туннель, добавить пир на сервере,
проверить доступность /health по туннелю. Дальше фоновый heartbeat следит за живостью
и метит потерянные/уничтоженные ноды (это триггерит failover в оркестраторе).
"""
from __future__ import annotations

import ipaddress
import time
import uuid
from typing import Dict, List, Optional

import httpx

from . import config, db, provisioner, vpn

# Живой кэш метрик/статуса для UI и WS (эпемерный)
LIVE: Dict[str, Dict] = {}


def _base(tunnel_ip: str) -> str:
    return f"http://{tunnel_ip}:{config.AGENT_API_PORT}"


def _allocate_ip() -> str:
    used = {r["tunnel_ip"] for r in db.all_("SELECT tunnel_ip FROM agents WHERE tunnel_ip IS NOT NULL")}
    net = ipaddress.ip_network(config.TUNNEL_NET)
    server = ipaddress.ip_address(config.HOST_TUNNEL_IP)
    for host in net.hosts():
        if host == server:
            continue
        if str(host) not in used:
            return str(host)
    raise RuntimeError("свободных туннельных адресов не осталось")


# --------------------------- provisioning ----------------------------------
def add_agent(name: str, ssh_host: str, ssh_port: int, ssh_user: str, ssh_password: str,
              full_deploy: bool = False) -> Dict:
    """Синхронный полный provisioning (запускать в threadpool из роутера)."""
    aid = uuid.uuid4().hex[:12]
    keys = vpn.gen_keys()                       # {private, public}
    tip = _allocate_ip()
    db.q("""INSERT INTO agents(id,name,ssh_host,ssh_port,ssh_user,tunnel_ip,pubkey,status)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'provisioning')""",
         (aid, name, ssh_host, ssh_port, ssh_user, tip, keys["public"]))
    # приватный ключ агента — на диск хоста (volume), как «ключик, что вбросили»
    (config.KEYS_DIR / f"{aid}.key").write_text(keys["private"], encoding="utf-8")

    conf = vpn.build_client_conf(keys["private"], tip)
    # пир на СЕРВЕРЕ добавляем ДО поднятия туннеля агентом — иначе handshake не пройдёт
    vpn.add_peer(keys["public"], f"{tip}/32")
    t = provisioner.SSHTarget(ssh_host, ssh_port, ssh_user, ssh_password)
    if full_deploy:
        # ПОЛНЫЙ деплой с хоста: копируем исходники (без git), ставим+собираем+поднимаем
        # контейнер-агента на ноде, вбрасываем туннель, взводим dead-man.
        tarball = provisioner.build_deploy_tarball()
        ok, log = provisioner.deploy(t, tarball, conf, name, config.HOST_TUNNEL_IP)
    else:
        # только вброс туннеля в УЖЕ стоящий агент (ре-провижн)
        ok, log = provisioner.provision(t, conf)
    if ok:
        # финальная проверка: доступен ли API агента по туннелю
        reachable = _probe(tip)
        status = "online" if reachable else "provisioning"
        log.append("API агента доступен по туннелю" if reachable else "API агента пока не отвечает по туннелю")
    else:
        status = "failed"
        try:
            vpn.remove_peer(keys["public"])     # откат пира, если провижн не удался
        except Exception:  # noqa: BLE001
            pass
    db.q("UPDATE agents SET status=%s, last_seen=%s WHERE id=%s", (status, time.time(), aid))
    LIVE.setdefault(aid, {})["provision_log"] = log
    return {"id": aid, "name": name, "tunnel_ip": tip, "status": status, "log": log}


def _probe(tunnel_ip: str) -> bool:
    try:
        r = httpx.get(_base(tunnel_ip) + "/health", timeout=5)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def remove_agent(aid: str) -> None:
    rec = db.one("SELECT pubkey FROM agents WHERE id=%s", (aid,))
    if rec and rec.get("pubkey"):
        try:
            vpn.remove_peer(rec["pubkey"])
        except Exception:  # noqa: BLE001
            pass
    db.q("DELETE FROM agents WHERE id=%s", (aid,))
    LIVE.pop(aid, None)
    (config.KEYS_DIR / f"{aid}.key").unlink(missing_ok=True)


# ------------------------------ роли ---------------------------------------
def assign_role(aid: str, role: str) -> Dict:
    a = get(aid)
    if not a:
        raise KeyError("агент не найден")
    r = httpx.post(_base(a["tunnel_ip"]) + f"/role/{role}", timeout=200)
    r.raise_for_status()
    state = r.json()
    db.q("UPDATE agents SET roles=%s WHERE id=%s", (db.js(state.get("roles", ["scanner"])), aid))
    return state


def revoke_role(aid: str, role: str) -> Dict:
    a = get(aid)
    r = httpx.request("DELETE", _base(a["tunnel_ip"]) + f"/role/{role}", timeout=30)
    r.raise_for_status()
    state = r.json()
    db.q("UPDATE agents SET roles=%s WHERE id=%s", (db.js(state.get("roles", ["scanner"])), aid))
    return state


def destroy(aid: str) -> Dict:
    a = get(aid)
    try:
        httpx.post(_base(a["tunnel_ip"]) + "/destroy", timeout=10)
    except Exception:  # noqa: BLE001
        pass
    db.q("UPDATE agents SET status='destroyed' WHERE id=%s", (aid,))
    return {"destroyed": True}


# ------------------------------ чтение -------------------------------------
def get(aid: str) -> Optional[Dict]:
    return db.one("SELECT * FROM agents WHERE id=%s", (aid,))


def list_agents() -> List[Dict]:
    rows = db.all_("SELECT * FROM agents ORDER BY created_at")
    for r in rows:
        r["live"] = LIVE.get(r["id"], {})
    return rows


def online_ids(require_role: str = None) -> List[str]:
    rows = db.all_("SELECT id, roles FROM agents WHERE status='online'")
    if not require_role:
        return [r["id"] for r in rows]
    out = []
    for r in rows:
        roles = r["roles"] if isinstance(r["roles"], list) else []
        if require_role in roles:
            out.append(r["id"])
    return out


# ------------------------------ heartbeat ----------------------------------
async def heartbeat_loop():
    import asyncio
    async with httpx.AsyncClient(timeout=5) as cli:
        while True:
            rows = db.all_("SELECT id, tunnel_ip, status FROM agents "
                           "WHERE status IN ('online','provisioning','lost')")
            for r in rows:
                await _beat(cli, r)
            await asyncio.sleep(config.HEARTBEAT_INTERVAL)


async def _beat(cli: httpx.AsyncClient, r: Dict):
    aid, tip = r["id"], r["tunnel_ip"]
    live = LIVE.setdefault(aid, {"misses": 0, "cpu": [], "mem": []})
    try:
        resp = await cli.get(_base(tip) + "/health")
        h = resp.json()
        live["misses"] = 0
        live["health"] = h
        m = h.get("metrics", {})
        live["cpu"] = (live.get("cpu", []) + [m.get("cpu", 0)])[-30:]   # спарклайны
        live["mem"] = (live.get("mem", []) + [m.get("mem", 0)])[-30:]
        if h.get("destroyed"):
            db.q("UPDATE agents SET status='destroyed', last_seen=%s WHERE id=%s", (time.time(), aid))
        else:
            db.q("UPDATE agents SET status='online', last_seen=%s, roles=%s WHERE id=%s",
                 (time.time(), db.js(h.get("roles", {}).get("roles", ["scanner"])), aid))
    except Exception:  # noqa: BLE001 — нет ответа
        live["misses"] = live.get("misses", 0) + 1
        if live["misses"] >= config.HEARTBEAT_MISS and r["status"] != "destroyed":
            db.q("UPDATE agents SET status='lost' WHERE id=%s AND status!='destroyed'", (aid,))
