#!/usr/bin/env bash
# entrypoint.sh — старт VPN-контейнера: поднять AmneziaWG-сервер + control-API.
set -u
export PYTHONPATH="/opt/pintest/host/vpn"

# TUN для userspace AmneziaWG
if [ ! -e /dev/net/tun ]; then
  mkdir -p /dev/net && mknod /dev/net/tun c 10 200 2>/dev/null || true
fi
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true

# поднять серверный интерфейс awg0 (ключи/params берутся/создаются на volume)
python3 -c "from server_api import awg; awg.up()" && echo "[vpn] awg0 поднят" \
  || echo "[vpn] предупреждение: awg0 не поднялся"
awg show awg0 2>/dev/null | sed 's/^/[vpn] /' || true

# control-API для backend (внутренний, :8080)
cd /opt/pintest/host/vpn
exec uvicorn server_api.main:app --host 0.0.0.0 --port 8080
