#!/usr/bin/env bash
# ===================================================================
#  host/install.sh — bootstrap ОСНОВНОГО сервера на РЕАЛЬНОЙ Ubuntu.
#
#  Механика обновления/раскатки хоста — git ИЛИ scp (в отличие от агентов,
#  которым код доставляет хост). Здесь: git-клон/pull -> сборка стека.
#  Плюс базовый хардненинг (ufw + fail2ban + SSH только по ключу).
#
#  Использование:
#     git clone <repo> pintest && cd pintest/host && sudo ./install.sh
# ===================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

echo "== pintest host install =="

# 1) Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "[install] ставлю Docker…"
  apt-get update -y && apt-get install -y ca-certificates curl
  install -m0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# 2) Хардненинг основного сервера (закрыть от внешнего)
if [ "${PINTEST_HARDEN:-1}" = "1" ]; then
  echo "[install] хардненинг: ufw + fail2ban…"
  apt-get install -y ufw fail2ban || true
  ufw --force reset || true
  ufw default deny incoming || true
  ufw default allow outgoing || true
  ufw allow 22/tcp || true          # SSH (лучше сменить порт и ключи-only)
  ufw allow 51820/udp || true       # вход туннеля AmneziaWG
  ufw allow 8443/tcp || true        # вебка (в идеале — только из VPN-подсети)
  ufw --force enable || true
  cp "$HERE/fail2ban/jail.local" /etc/fail2ban/jail.local || true
  systemctl enable --now fail2ban || true
  # SSH: выключить парольный вход (только ключи) — раскомментируй при готовых ключах:
  # sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && systemctl reload ssh
fi

# 3) конфиг
if [ ! -f "$HERE/config.env" ]; then
  cp "$HERE/config.example.env" "$HERE/config.env"
  echo "[install] создан host/config.env — адрес хоста (AWG_ENDPOINT) определится САМ;"
  echo "[install]   пропиши его вручную, только если авто-детект ошибётся (сложный NAT)"
fi

# 4) общий образ AmneziaWG + стек
echo "[install] собираю pintest-awg-base…"
docker build -t pintest-awg-base:latest "$ROOT/awg-base"
echo "[install] поднимаю стек хоста…"
cd "$HERE"
docker compose --env-file config.env up -d --build

echo "== готово. Вебка: https://<host>:8443  (admin/admin, смени в config.env) =="
