#!/usr/bin/env bash
# self-destruct.sh — необратимое самоуничтожение проекта на этой ноде.
#
# Срабатывает при потере связи с хостом (dead-man) или по ручному триггеру. Всё, что
# относится к проекту, стирается: туннель вниз, конфиги/ключи/результаты/код — прочь.
# Стирание СТРОГО в пределах проектной директории (безопасно для остальной системы).
set -u
REASON="${1:-неизвестно}"
ROOT="${PINTEST_ROOT:-/opt/pintest}"
IFACE="${AWG_IFACE:-awg0}"

echo "[destruct] ПРИЧИНА: ${REASON} — уничтожаю проект на ноде $(hostname)"

# 1) оборвать туннель, чтобы не оставлять следов маршрутизации
awg-quick down "$IFACE" 2>/dev/null
ip link del "$IFACE" 2>/dev/null

# 2) остановить текущие сканы/процессы движка
pkill -f "core.auditor" 2>/dev/null
pkill -f nmap 2>/dev/null

# 3) стереть конфиги/ключи туннеля
rm -rf /etc/amnezia/amneziawg/* 2>/dev/null
rm -rf /var/run/wireguard/* 2>/dev/null

# 4) маркер и стирание проекта (кроме самого скрипта, пока он выполняется)
echo "destroyed $(date -u +%FT%TZ) reason=${REASON}" > "${ROOT}/DESTROYED" 2>/dev/null
rm -rf "${ROOT}/agent/state" "${ROOT}/core" "${ROOT}/exploits" \
       "${ROOT}/agent/agent_api" 2>/dev/null

echo "[destruct] готово. Останавливаю сервисы ноды."
# 5) уронить API (pid1/uvicorn) -> контейнер остановится
pkill -f "uvicorn" 2>/dev/null
pkill -f "agent_api" 2>/dev/null
kill 1 2>/dev/null
exit 0
