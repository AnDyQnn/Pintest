"""backend.vpn — управление VPN-сервером через control-API vpn-контейнера.

Сам AmneziaWG-сервер живёт в отдельном контейнере vpn/ (роль по ТЗ). Бэкенд им
управляет по внутреннему API: берёт server-info, генерирует ключевые пары для агентов,
собирает клиентские конфиги (их хост вбрасывает агентам по SSH) и добавляет/снимает пиры.
"""
from __future__ import annotations

import ipaddress
from typing import Dict, List

import httpx

from . import config

# Приватные сети клиента, которые НЕЛЬЗЯ заворачивать в full-tunnel, иначе у админа
# отвалится его локалка (LAN/докер/CGNAT). Сеть управления (TUNNEL_NET) сюда НЕ
# входит — её, наоборот, оставляем в туннеле (там панель).
_PRIVATE_KEEP_DIRECT = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                        "169.254.0.0/16", "100.64.0.0/10"]


def full_tunnel_allowed_ips() -> str:
    """AllowedIPs для «умного» full-tunnel: весь интернет через VPN, но приватные
    сети клиента — МИМО туннеля (чтобы full-tunnel не рвал его локалку). Сеть
    управления (TUNNEL_NET) принудительно возвращаем в туннель — там вебка.
    Порт логики build_split_allowed_ips из каскадного AWG (без РФ-байпаса)."""
    remaining: List[ipaddress.IPv4Network] = [ipaddress.ip_network("0.0.0.0/0")]
    for ex in _PRIVATE_KEEP_DIRECT:
        exn = ipaddress.ip_network(ex)
        rebuilt: List[ipaddress.IPv4Network] = []
        for n in remaining:
            if exn.subnet_of(n):
                rebuilt.extend(n.address_exclude(exn))   # вырезаем приватный диапазон
            elif n.subnet_of(exn):
                continue                                  # n целиком приватный — убираем
            else:
                rebuilt.append(n)                         # не пересекается — оставляем
        remaining = rebuilt
    cidrs = [str(n) for n in ipaddress.collapse_addresses(remaining)]
    cidrs.append(config.TUNNEL_NET)   # сеть управления/панель — обратно в туннель
    cidrs.append("::/0")
    return ", ".join(cidrs)


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
