"""backend.admin_vpn — генерация админских VPN-конфигов для доступа к вебке.

Админ подключается к control plane НЕ напрямую, а по AmneziaWG. Хост генерирует
персональный клиентский awg0.conf (ключи + туннельный IP + пир на сервере) — админ
скачивает .conf, поднимает туннель и заходит на вебку по адресу хоста в туннеле.
Тот же механизм, что и для агентов, но адреса выдаются с ВЕРХНЕГО конца диапазона.
"""
from __future__ import annotations

import ipaddress
import os
import time
from typing import Dict, List, Optional

from . import config, db, vpn


def _alloc_admin_ip() -> str:
    used = {r["tunnel_ip"] for r in db.all_("SELECT tunnel_ip FROM agents WHERE tunnel_ip IS NOT NULL")}
    used |= {r["tunnel_ip"] for r in db.all_("SELECT tunnel_ip FROM admin_configs")}
    net = ipaddress.ip_network(config.TUNNEL_NET)
    server = ipaddress.ip_address(config.HOST_TUNNEL_IP)
    for host in reversed(list(net.hosts())):        # админам — сверху диапазона
        if host == server:
            continue
        if str(host) not in used:
            return str(host)
    raise RuntimeError("свободных туннельных адресов не осталось")


def create(name: str) -> Dict:
    name = (name or "").strip() or f"admin-{int(time.time())}"
    if db.one("SELECT name FROM admin_configs WHERE name=%s", (name,)):
        raise ValueError("конфиг с таким именем уже есть")
    keys = vpn.gen_keys()
    ip = _alloc_admin_ip()
    vpn.add_peer(keys["public"], f"{ip}/32")
    # админ по умолчанию — FULL-TUNNEL: интернет тоже через VPN (сервер NAT'ит в WAN).
    # split (только сеть управления) — если ADMIN_VPN_FULL_TUNNEL=0.
    if config.ADMIN_VPN_FULL_TUNNEL:
        conf = vpn.build_client_conf(keys["private"], ip,
                                     allowed_ips="0.0.0.0/0, ::/0", dns=config.ADMIN_VPN_DNS)
    else:
        conf = vpn.build_client_conf(keys["private"], ip)
    db.q("INSERT INTO admin_configs(name,tunnel_ip,pubkey,conf) VALUES(%s,%s,%s,%s)",
         (name, ip, keys["public"], conf))
    return {"name": name, "tunnel_ip": ip, "conf": conf}


def list_() -> List[Dict]:
    return db.all_("SELECT name, tunnel_ip, created_at FROM admin_configs ORDER BY created_at DESC")


def conf(name: str) -> Optional[str]:
    r = db.one("SELECT conf FROM admin_configs WHERE name=%s", (name,))
    return r["conf"] if r else None


def delete(name: str) -> Dict:
    r = db.one("SELECT pubkey FROM admin_configs WHERE name=%s", (name,))
    if not r:
        raise ValueError("нет такого конфига")
    try:
        vpn.remove_peer(r["pubkey"])
    except Exception:  # noqa: BLE001
        pass
    db.q("DELETE FROM admin_configs WHERE name=%s", (name,))
    return {"ok": True}


BOOTSTRAP_NAME = "admin-bootstrap"


def ensure_bootstrap():
    """Первый старт: если админ-конфигов нет — создать bootstrap и положить файл в DATA_DIR,
    чтобы оператор сразу вошёл в вебку по VPN (проблема курицы-яйца). Идемпотентно."""
    try:
        if db.all_("SELECT name FROM admin_configs LIMIT 1"):
            return None
        res = create(BOOTSTRAP_NAME)
        path = config.DATA_DIR / "bootstrap-admin.conf"
        path.write_text(res["conf"], encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return str(path)
    except Exception as e:  # noqa: BLE001  — VPN мог ещё не подняться; не валим старт
        return f"[bootstrap отложен: {e}]"
