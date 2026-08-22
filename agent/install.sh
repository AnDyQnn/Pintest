#!/usr/bin/env bash
# ===================================================================
#  agent/install.sh — bootstrap удалённой ноды на РЕАЛЬНОМ сервере (Ubuntu).
#
#  Ставит Docker (если нет) и поднимает ТОТ ЖЕ образ агента, что и в лабе.
#  В докер-лабе этот скрипт не нужен — там ноды поднимает lab/docker-compose.yml,
#  но образ и код идентичны => перенос в реальную лабу без переписывания.
#
#  Использование на сервере:
#     git clone <repo> pintest && cd pintest/agent && sudo ./install.sh
# ===================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"     # корень репо (нужны core/, exploits/, awg-base/)

echo "== pintest agent install =="

# 1) зависимости хоста: Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "[install] ставлю Docker…"
  apt-get update -y
  apt-get install -y ca-certificates curl
  install -m0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# 2) конфиг ноды
if [ ! -f "$HERE/.env" ]; then
  cp "$HERE/.env.example" "$HERE/.env"
  echo "[install] создан agent/.env — проверь AGENT_SSH_PASSWORD и HOST_TUNNEL_IP"
fi

# 3) общий образ AmneziaWG + образ агента (тот же, что в лабе)
echo "[install] собираю pintest-awg-base…"
docker build -t pintest-awg-base:latest "$ROOT/awg-base"

echo "[install] поднимаю ноду-агента…"
cd "$HERE"
docker compose --env-file .env up -d --build

echo "== готово. Нода поднята. Провижнинг (SSH + вброс ключа) делается с хоста из вебки. =="
