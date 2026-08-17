#!/usr/bin/env bash
# entrypoint.sh — старт ноды-агента.
#
# Супервизор: держит sshd (для provisioning с хоста) и API агента. Если API упал по
# обновлению — перезапускаем. Если появился DESTROYED (самоуничтожение) — выходим,
# контейнер остаётся выключенным (нода «уничтожена»).
set -u
ROOT="${PINTEST_ROOT:-/opt/pintest}"
export PYTHONPATH="${ROOT}:${ROOT}/agent"
export PYTHONUNBUFFERED=1

# --- TUN для userspace AmneziaWG ---
if [ ! -e /dev/net/tun ]; then
  mkdir -p /dev/net && mknod /dev/net/tun c 10 200 2>/dev/null || true
fi

# переменные для awg-quick доступны и в SSH-сессиях provisioning
{
  echo "WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go"
  echo "WG_SUDO=1"
} >> /etc/environment 2>/dev/null || true

# --- SSH для provisioning с хоста (пароль задаёт хост через ENV) ---
mkdir -p /run/sshd
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
  ssh-keygen -A >/dev/null 2>&1 || true
fi
: "${AGENT_SSH_USER:=root}"
: "${AGENT_SSH_PASSWORD:=pintest}"
echo "${AGENT_SSH_USER}:${AGENT_SSH_PASSWORD}" | chpasswd 2>/dev/null || true
# drop-in переопределяет дефолты Ubuntu (root по паролю для provisioning с хоста)
mkdir -p /etc/ssh/sshd_config.d
printf 'PermitRootLogin yes\nPasswordAuthentication yes\n' > /etc/ssh/sshd_config.d/00-pintest.conf
/usr/sbin/sshd 2>/dev/null || true
echo "[entrypoint] sshd запущен (provisioning), пользователь ${AGENT_SSH_USER}"

# --- авто-восстановление туннеля при рестарте той же ноды ---
# Если нода была провижнена (armed) и не уничтожена — пробуем поднять туннель заново.
# Хост доступен  -> нода выживает (обычный рестарт).
# Хост недоступен -> dead-man boot-check вычистит хвосты (изоляция/выключили-включили).
if [ -f "${ROOT}/agent/.armed" ] && [ -f /etc/amnezia/amneziawg/awg0.conf ] && [ ! -f "${ROOT}/DESTROYED" ]; then
  echo "[entrypoint] нода была провижнена — восстанавливаю туннель"
  bash "${ROOT}/agent/scripts/apply-awg.sh" || echo "[entrypoint] туннель не восстановлен (изоляция?) — решит dead-man"
fi

# --- супервизор API ---
cd "${ROOT}/agent"
while true; do
  if [ -f "${ROOT}/DESTROYED" ]; then
    echo "[entrypoint] нода уничтожена (DESTROYED) — выхожу."
    exit 0
  fi
  echo "[entrypoint] запуск agent_api на :${AGENT_API_PORT:-9101}"
  uvicorn agent_api.main:app --host "${AGENT_API_HOST:-0.0.0.0}" --port "${AGENT_API_PORT:-9101}"
  rc=$?
  if [ -f "${ROOT}/DESTROYED" ]; then
    echo "[entrypoint] нода уничтожена — выхожу."
    exit 0
  fi
  echo "[entrypoint] agent_api завершился (rc=${rc}); перезапуск через 1с"
  sleep 1
done
