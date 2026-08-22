#!/usr/bin/env bash
# apply-awg.sh — поднять клиентский туннель AmneziaWG из конфига, вброшенного хостом.
#
# Хост по SSH кладёт /etc/amnezia/amneziawg/awg0.conf и запускает этот скрипт. После
# успешного handshake взводится dead-man switch (ARMED) — с этого момента потеря связи
# с хостом означает самоуничтожение ноды.
set -u
IFACE="${AWG_IFACE:-awg0}"
CONF="/etc/amnezia/amneziawg/${IFACE}.conf"
HOST_IP="${HOST_TUNNEL_IP:-10.9.0.1}"
STATE_DIR="${AGENT_STATE_DIR:-/opt/pintest/agent/state}"
# гарантируем userspace-реализацию (SSH-сессия может не унаследовать Docker-ENV)
export WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go
export WG_SUDO=1
export PATH="/usr/bin:/usr/sbin:/bin:/sbin:${PATH:-}"

echo "[awg] поднимаю туннель ${IFACE} из ${CONF}"
[ -e /dev/net/tun ] || { mkdir -p /dev/net; mknod /dev/net/tun c 10 200 2>/dev/null; }

if [ ! -f "$CONF" ]; then
  echo "[awg] НЕТ конфига $CONF — хост его не вбросил"; exit 2
fi

awg-quick down "$IFACE" 2>/dev/null
if ! awg-quick up "$IFACE"; then
  echo "[awg] awg-quick up не удался"; exit 3
fi

# ждём handshake / доступность хоста в туннеле
ok=0
for i in $(seq 1 10); do
  if ping -c1 -W2 "$HOST_IP" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done

mkdir -p "$STATE_DIR"
# ВЗВОД dead-man здесь НЕ делаем: его взводит сам агент (deadman._loop) идемпотентно —
# только после УСТОЙЧИВОЙ связи с хостом (DEADMAN_ARM_GRACE), чтобы кратковременная
# заминка на реальном интернете не приводила к ложному самоуничтожению.
if [ "$ok" = "1" ]; then
  echo "[awg] туннель поднят, хост ${HOST_IP} доступен — dead-man взведётся сам после устойчивого коннекта"
  awg show "$IFACE" 2>/dev/null | sed 's/^/[awg] /'
  exit 0
else
  echo "[awg] туннель поднят, но хост ${HOST_IP} НЕ отвечает (handshake?)"; exit 4
fi
