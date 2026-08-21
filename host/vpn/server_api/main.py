"""server_api.main — control-API VPN-контейнера (внутренний, слушает :8080)."""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from . import awg

app = FastAPI(title="pintest-vpn", version="0.1.0")


class PeerIn(BaseModel):
    pubkey: str
    allowed_ip: str


@app.get("/status")
def status():
    return awg.status()


@app.get("/server-info")
def server_info():
    return awg.server_info()


@app.post("/genkeys")
def genkeys():
    return awg.genkeys()


@app.post("/reload")
def reload():
    """Перечитать awg0 из server.json (down/up) — после restore бэкапа."""
    awg.up()
    return awg.status()


@app.post("/peer")
def add_peer(body: PeerIn):
    return awg.add_peer(body.pubkey, body.allowed_ip)


@app.delete("/peer/{pubkey}")
def remove_peer(pubkey: str):
    return awg.remove_peer(pubkey)
