#!/usr/bin/env bash
# apply-update.sh — применение обновления, доставленного ХОСТОМ по SSH.
#
# Механика обновления агента отличается от хостовой: агент код сам не тянет. Хост
# по SSH кладёт бандл /tmp/pintest-update.tgz и запускает этот скрипт. Он разворачивает
# бандл поверх проекта и мягко перезапускает API. (Тот же результат, что и API-механика
# updater.apply_bundle, но другой транспорт — SSH вместо HTTP по туннелю.)
set -u
ROOT="${PINTEST_ROOT:-/opt/pintest}"
BUNDLE="${1:-/tmp/pintest-update.tgz}"
VERSION="${2:-}"

if [ ! -f "$BUNDLE" ]; then
  echo "[update] бандл $BUNDLE не найден"; exit 2
fi
echo "[update] разворачиваю $BUNDLE поверх $ROOT"
tar -xzf "$BUNDLE" -C "$ROOT" || { echo "[update] распаковка не удалась"; exit 3; }
[ -n "$VERSION" ] && echo "$VERSION" > "${ROOT}/agent/state/version" 2>/dev/null

echo "[update] перезапускаю API ноды"
pkill -f "uvicorn" 2>/dev/null   # entrypoint поднимет заново (restart policy / supervisor)
rm -f "$BUNDLE"
echo "[update] готово"
exit 0
