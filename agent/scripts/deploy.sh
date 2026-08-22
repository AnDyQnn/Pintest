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
SSH_PORT="${6:-22}"                 # порт sshd ноды — для fail2ban/ufw (тот же, чем хост зашёл)
export DEBIAN_FRONTEND=noninteractive
export PATH="/usr/bin:/usr/sbin:/bin:/sbin:${PATH:-}"

[ -f "$BUNDLE" ] || { echo "[deploy] нет бандла $BUNDLE"; exit 2; }

echo "[deploy] apt update + базовые пакеты (без upgrade — не нужен для агента, экономит время)"
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl

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

# ── Хардненинг агент-СЕРВЕРА: fail2ban под перебор SSH (от РОДНОГО root). ───────────
# ВАЖНО: ufw здесь НЕ включаем. Агент-сервер — docker-хост, а `ufw enable` ставит
# FORWARD policy = DROP и рвёт сеть контейнера-агента (он не дозвонится по туннелю →
# нода LOST). В рабочем VPN-проекте юзера ufw на docker-хостах тоже не включается —
# защиту даёт fail2ban. Отключить хардненинг целиком: AGENT_HARDEN=0.
if [ "${AGENT_HARDEN:-1}" = "1" ]; then
  echo "[deploy] хардненинг ноды: fail2ban (SSH-порт ${SSH_PORT})"
  apt-get install -y fail2ban python3-systemd || echo "[deploy][WARN] fail2ban не поставился"
  # fail2ban: journald (sshd не пишет в /var/log/auth.log на совр. Ubuntu) + ignoreip приватных
  cat > /etc/fail2ban/jail.local <<'F2B'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
banaction = iptables-allports
ignoreip = 127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 ::1/128 fc00::/7 fe80::/10

[sshd]
enabled = true
backend = systemd
F2B
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban >/dev/null 2>&1 || true
fi

echo "[deploy] распаковываю исходники (с ХОСТА, не git) -> $ROOT"
mkdir -p "$ROOT"
tar -xzf "$BUNDLE" -C "$ROOT"
rm -f "$BUNDLE"

# per-node конфиг ноды (имя + адрес хоста в туннеле)
cat > "$ROOT/agent/.env" <<EOF
AGENT_NAME=${NAME}
HOST_TUNNEL_IP=${HOSTIP}
AGENT_SSH_USER=root
AGENT_SSH_PASSWORD=${SSHPASS}
EOF

echo "[deploy] собираю образы из копии (awg-base + agent), без клонирования"
docker build -t pintest-awg-base:latest "$ROOT/awg-base"
cd "$ROOT/agent"
docker compose up -d --build

# инъекция туннеля в контейнер + взвод dead-man (локально, без второго SSH)
CID="$(docker compose ps -q agent 2>/dev/null || docker ps -q -f name=pintest-agent)"
[ -n "$CID" ] || { echo "[deploy] контейнер-агент не найден после up"; exit 3; }
docker exec "$CID" mkdir -p /etc/amnezia/amneziawg
docker cp "$AWGCONF" "$CID":/etc/amnezia/amneziawg/awg0.conf
rm -f "$AWGCONF"
echo "[deploy] поднимаю туннель + взвожу dead-man внутри контейнера"
docker exec "$CID" bash /opt/pintest/agent/scripts/apply-awg.sh || {
  echo "[deploy] apply-awg внутри контейнера завершился с ошибкой"; exit 4; }

echo "[deploy] готово: агент ${NAME} развёрнут из копии, туннель поднят, dead-man взведён."
