"""backend.vpn — управление VPN-сервером через control-API vpn-контейнера.

Сам AmneziaWG-сервер живёт в отдельном контейнере vpn/ (роль по ТЗ). Бэкенд им
управляет по внутреннему API: берёт server-info, генерирует ключевые пары для агентов,
собирает клиентские конфиги (их хост вбрасывает агентам по SSH) и добавляет/снимает пиры.
"""
from __future__ import annotations

from typing import Dict

import httpx

from . import config


def _client() -> httpx.Client:
    return httpx.Client(base_url=config.VPN_API, timeout=15)


def status() -> Dict:
    try:
        with _client() as c:
            return c.get("/status").json()
    except Exception as e:  # noqa: BLE001
        return {"up": False, "error": str(e), "peers": []}


def server_info() -> Dict:
    with _client() as c:
        return c.get("/server-info").json()


def gen_keys() -> Dict:
    """Сгенерировать ключевую пару агента (делает vpn-контейнер, там есть awg)."""
    with _client() as c:
        return c.post("/genkeys").json()


def add_peer(pubkey: str, allowed_ip: str) -> Dict:
    with _client() as c:
        return c.post("/peer", json={"pubkey": pubkey, "allowed_ip": allowed_ip}).json()


def remove_peer(pubkey: str) -> Dict:
    with _client() as c:
        return c.delete(f"/peer/{pubkey}").json()


def build_client_conf(agent_priv: str, tunnel_ip: str) -> str:
    """Собрать клиентский awg0.conf для агента (инвертированный туннель к хосту)."""
    si = server_info()
    p = si.get("params", {})
    endpoint = config.AWG_ENDPOINT or si.get("endpoint", "")
    params_lines = "\n".join(f"{k} = {p[k]}" for k in
                             ("Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4") if k in p)
    return (
        "[Interface]\n"
        f"PrivateKey = {agent_priv}\n"
        f"Address = {tunnel_ip}/24\n"
        f"{params_lines}\n\n"
        "[Peer]\n"
        f"PublicKey = {si['pubkey']}\n"
        f"Endpoint = {endpoint}\n"
        f"AllowedIPs = {config.TUNNEL_NET}\n"
        "PersistentKeepalive = 25\n"
    )
