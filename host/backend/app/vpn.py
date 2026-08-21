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


def reload_server() -> Dict:
    """Перечитать awg0 из server.json на диске (down/up). Нужно после restore —
    иначе восстановленные ключи/пиры VPN-сервера не станут активными."""
    with _client() as c:
        return c.post("/reload").json()


def add_peer(pubkey: str, allowed_ip: str) -> Dict:
    with _client() as c:
        return c.post("/peer", json={"pubkey": pubkey, "allowed_ip": allowed_ip}).json()


def remove_peer(pubkey: str) -> Dict:
    with _client() as c:
        return c.delete(f"/peer/{pubkey}").json()


def build_client_conf(agent_priv: str, tunnel_ip: str,
                      allowed_ips: str = "", dns: str = "") -> str:
    """Собрать клиентский awg0.conf.

    allowed_ips: что клиент гонит в туннель. По умолчанию TUNNEL_NET (split — только
      сеть управления; так у АГЕНТОВ, их интернет через хост гнать не надо). Для
      АДМИН-конфигов передаётся "0.0.0.0/0, ::/0" (full-tunnel: интернет тоже через VPN).
    dns: строка DNS для [Interface] (нужна при full-tunnel, чтобы резолвился интернет).
    """
    si = server_info()
    p = si.get("params", {})
    endpoint = config.AWG_ENDPOINT or si.get("endpoint", "")
    allowed = allowed_ips or config.TUNNEL_NET
    params_lines = "\n".join(f"{k} = {p[k]}" for k in
                             ("Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4") if k in p)
    dns_line = f"DNS = {dns}\n" if dns else ""
    return (
        "[Interface]\n"
        f"PrivateKey = {agent_priv}\n"
        f"Address = {tunnel_ip}/24\n"
        "MTU = 1280\n"                       # AmneziaWG с джиттером/обфускацией — иначе handshake виснет на части сетей
        f"{dns_line}"
        f"{params_lines}\n\n"
        "[Peer]\n"
        f"PublicKey = {si['pubkey']}\n"
        f"Endpoint = {endpoint}\n"
        f"AllowedIPs = {allowed}\n"
        "PersistentKeepalive = 25\n"
    )
