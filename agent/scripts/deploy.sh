#!/usr/bin/env bash
# ===================================================================
#  deploy.sh — ПОЛНОЕ развёртывание агента, доставленного ХОСТОМ по SSH.
#
#  Репозиторий на ноде НЕ клонируется (это деанон). Хост копирует исходники
#  (agent/ core/ exploits/ awg-base/) тарболом, ставит зависимости, собирает
#  и поднимает контейнер-агента ИЗ КОПИИ, затем вбрасывает туннель и взводит
#  dead-man. Идемпотентно: повторный запуск переустанавливает поверх.
#
#  Аргументы: <bundle.tgz> <awg0.conf> <AGENT_NAME> <HOST_TUNNEL_IP> [SSH_PASS]
# ===================================================================
set -euo pipefail
ROOT="${PINTEST_ROOT:-/opt/pintest}"
BUNDLE="${1:?нет пути к бандлу}"
AWGCONF="${2:?нет пути к awg-конфигу}"
NAME="${3:-agent}"
HOSTIP="${4:-10.9.0.1}"
SSHPASS="${5:-pintest}"
export DEBIAN_FRONTEND=noninteractive
export PATH="/usr/bin:/usr/sbin:/bin:/sbin:${PATH:-}"

[ -f "$BUNDLE" ] || { echo "[deploy] нет бандла $BUNDLE"; exit 2; }

echo "[deploy] apt update/upgrade + базовые пакеты"
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl

if ! command -v docker >/dev/null 2>&1; then
  echo "[deploy] ставлю Docker"
  install -m0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

echo "[deploy] распаковываю исходники (с ХОСТА, не git) -> $ROOT"
mkdir -p "$ROOT"
tar -xzf "$BUNDLE" -C "$ROOT"
rm -f "$BUNDLE"

# per-node конфиг ноды (имя + адрес хоста в туннеле)
cat > "$ROOT/agent/config.env" <<EOF
AGENT_NAME=${NAME}
HOST_TUNNEL_IP=${HOSTIP}
AGENT_SSH_USER=root
AGENT_SSH_PASSWORD=${SSHPASS}
EOF

echo "[deploy] собираю образы из копии (awg-base + agent), без клонирования"
docker build -t pintest-awg-base:latest "$ROOT/awg-base"
cd "$ROOT/agent"
docker compose --env-file config.env up -d --build

# инъекция туннеля в контейнер + взвод dead-man (локально, без второго SSH)
CID="$(docker compose --env-file config.env ps -q agent 2>/dev/null || docker ps -q -f name=pintest-agent)"
[ -n "$CID" ] || { echo "[deploy] контейнер-агент не найден после up"; exit 3; }
docker exec "$CID" mkdir -p /etc/amnezia/amneziawg
docker cp "$AWGCONF" "$CID":/etc/amnezia/amneziawg/awg0.conf
rm -f "$AWGCONF"
echo "[deploy] поднимаю туннель + взвожу dead-man внутри контейнера"
docker exec "$CID" bash /opt/pintest/agent/scripts/apply-awg.sh || {
  echo "[deploy] apply-awg внутри контейнера завершился с ошибкой"; exit 4; }

echo "[deploy] готово: агент ${NAME} развёрнут из копии, туннель поднят, dead-man взведён."
