#!/usr/bin/env bash
# ===================================================================
#  lab/build.sh — поднять всю симуляцию одной командой.
#  Собирает общий образ AmneziaWG, затем весь стек лабы.
# ===================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

echo "== [1/3] общий образ AmneziaWG (amneziawg-go из исходников, ~2-4 мин) =="
docker build -t pintest-awg-base:latest "$ROOT/awg-base"

echo "== [2/3] сборка стека лабы (хост + 3 агента + цели) =="
cd "$HERE"
docker compose build

echo "== [3/3] запуск =="
docker compose up -d

echo
echo "== готово =="
echo "Вебка:   https://localhost:8443   (логин admin / admin)"
echo "Статус:  docker compose -f lab/docker-compose.yml ps"
echo "Логи:    docker compose -f lab/docker-compose.yml logs -f backend"
echo "Стоп:    docker compose -f lab/docker-compose.yml down          (данные в ./data сохранятся)"
echo "Снести:  docker compose -f lab/docker-compose.yml down -v && rm -rf lab/data"
