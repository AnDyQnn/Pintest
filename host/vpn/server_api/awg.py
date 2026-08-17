"""server_api.awg — управление AmneziaWG-сервером (awg0) через awg/awg-quick.

Идентичность сервера (ключи + обфускация-параметры «амнезия») хранится на volume,
чтобы переживать пересоздание контейнера. Пиры добавляются на лету и дописываются в
конфиг для persistency.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List

IFACE = os.environ.get("AWG_IFACE", "awg0")
CONF_DIR = Path("/etc/amnezia/amneziawg")
CONF = CONF_DIR / f"{IFACE}.conf"
STATE = CONF_DIR / "server.json"          # ключи + params (на volume)
SERVER_IP = os.environ.get("HOST_TUNNEL_IP", "10.9.0.1")
LISTEN_PORT = int(os.environ.get("AWG_LISTEN_PORT", "51820"))
ENDPOINT = os.environ.get("AWG_ENDPOINT", "")

# Дефолтные обфускация-параметры (одинаковы на сервере и всех клиентах)
DEFAULT_PARAMS = {"Jc": 4, "Jmin": 40, "Jmax": 70, "S1": 50, "S2": 100,
                  "H1": 1234567890, "H2": 1234567891, "H3": 1234567892, "H4": 1234567893}


def _run(args: List[str], inp: str = None) -> str:
    r = subprocess.run(args, capture_output=True, text=True, input=inp)
    return r.stdout.strip()


def genkeys() -> Dict:
    priv = _run(["awg", "genkey"])
    pub = _run(["awg", "pubkey"], inp=priv)
    return {"private": priv, "public": pub}


def _load_state() -> Dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except ValueError:
            pass
    keys = genkeys()
    st = {"private": keys["private"], "public": keys["public"], "params": DEFAULT_PARAMS,
          "peers": {}}
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False))
    return st


def _write_conf(st: Dict) -> None:
    params = "\n".join(f"{k} = {v}" for k, v in st["params"].items())
    lines = ["[Interface]",
             f"PrivateKey = {st['private']}",
             f"Address = {SERVER_IP}/24",
             f"ListenPort = {LISTEN_PORT}",
             params, ""]
    for pub, allowed in st.get("peers", {}).items():
        lines += ["[Peer]", f"PublicKey = {pub}", f"AllowedIPs = {allowed}", ""]
    CONF.write_text("\n".join(lines) + "\n")


def up() -> None:
    st = _load_state()
    _write_conf(st)
    subprocess.run(["awg-quick", "down", IFACE], capture_output=True)
    subprocess.run(["awg-quick", "up", IFACE], capture_output=True)


def add_peer(pubkey: str, allowed_ip: str) -> Dict:
    st = _load_state()
    st["peers"][pubkey] = allowed_ip
    STATE.write_text(json.dumps(st, ensure_ascii=False))
    subprocess.run(["awg", "set", IFACE, "peer", pubkey, "allowed-ips", allowed_ip],
                   capture_output=True)
    return {"ok": True, "peers": len(st["peers"])}


def remove_peer(pubkey: str) -> Dict:
    st = _load_state()
    st["peers"].pop(pubkey, None)
    STATE.write_text(json.dumps(st, ensure_ascii=False))
    subprocess.run(["awg", "set", IFACE, "peer", pubkey, "remove"], capture_output=True)
    return {"ok": True, "peers": len(st["peers"])}


def server_info() -> Dict:
    st = _load_state()
    return {"pubkey": st["public"], "endpoint": ENDPOINT or f"{SERVER_IP}:{LISTEN_PORT}",
            "params": st["params"], "server_ip": SERVER_IP, "tunnel_net": "10.9.0.0/24"}


def status() -> Dict:
    out = _run(["awg", "show", IFACE])
    up_iface = subprocess.run(["ip", "link", "show", IFACE], capture_output=True).returncode == 0
    peers = []
    cur = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("peer:"):
            cur = {"peer": line.split(":", 1)[1].strip(), "handshake": "", "endpoint": ""}
            peers.append(cur)
        elif cur is not None and line.startswith("latest handshake:"):
            cur["handshake"] = line.split(":", 1)[1].strip()
        elif cur is not None and line.startswith("endpoint:"):
            cur["endpoint"] = line.split(":", 1)[1].strip()
    return {"up": up_iface, "iface": IFACE, "listen_port": LISTEN_PORT, "peers": peers,
            "peer_count": len(peers)}
