#!/usr/bin/env bash
# ===================================================================
#  host/update.sh — обновление ХОСТА (control plane) из git с откатом.
#
#  Механика:
#    • git fetch + git reset --hard <remote>/<branch>  (жёсткий сброс на git)
#    • мягкий мерж .env (дописать новые ключи, значения не трогаем)
#    • docker compose up -d --build                    (пересборка+перезапуск)
#    • при ЛЮБОЙ ошибке — авто-откат на прежний коммит + пересборка
#
#  ДВА ПУТИ запуска:
#    1) из консоли:   sudo bash host/update.sh              (разово)
#    2) кнопкой с вебки: бэкенд пишет маркер host/data/.update-request,
#       а на хосте крутится  sudo bash host/update.sh --watch  (демон).
# ===================================================================
if [ -z "${BASH_VERSION:-}" ]; then echo "[ERR] нужен bash: sudo bash host/update.sh" >&2; exit 1; fi
if grep -q $'\r' "${BASH_SOURCE[0]}" 2>/dev/null; then
  sed -i 's/\r$//' "${BASH_SOURCE[0]}"; exec bash "${BASH_SOURCE[0]}" "$@"
fi
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
. "$HERE/lib.sh"
fail(){ err "$*"; exit 1; }

COMPOSE="docker compose -f $HERE/docker-compose.yml"
[ -f "$HERE/.env" ] && COMPOSE="$COMPOSE --env-file $HERE/.env"
BRANCH="${PINTEST_BRANCH:-$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)}"
REMOTE="${PINTEST_REMOTE:-origin}"
MARKER="$HERE/data/.update-request"
STATUS="$HERE/data/.update-status"

# статус последнего обновления — вебка его читает (/api/update/status), показывает исход
write_status(){  # $1=status(updated|uptodate|failed|running) $2=from $3=to
  mkdir -p "$HERE/data"
  printf '{"ts":%s,"status":"%s","from":"%s","to":"%s"}\n' \
    "$(date +%s)" "$1" "${2:-}" "${3:-}" > "$STATUS" 2>/dev/null || true
}

compose_up(){ $COMPOSE up -d --build; }

do_update() {
  cd "$ROOT"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "$ROOT — не git-репозиторий (клонируй репу на хост)"
  local prev; prev="$(git rev-parse HEAD)"
  title "обновление хоста"
  info "текущий коммит: ${prev:0:12} · ветка ${BRANCH}"

  write_status running "${prev:0:12}" ""

  rollback() {
    warn "ОШИБКА обновления — откат на ${prev:0:12}"
    git reset --hard "$prev" || true
    $COMPOSE up -d --build >>"$PINTEST_LOG" 2>&1 || true
    write_status failed "${prev:0:12}" "${prev:0:12}"
    fail "откат выполнен, поднята прежняя версия (лог: $PINTEST_LOG)"
  }
  trap rollback ERR

  step "git fetch ${REMOTE}/${BRANCH}" git fetch "$REMOTE" "$BRANCH"
  step "git reset --hard ${REMOTE}/${BRANCH}" git reset --hard "$REMOTE/$BRANCH"
  local new; new="$(git rev-parse HEAD)"
  if [ "$new" = "$prev" ]; then
    ok "уже актуально (${new:0:12}) — пересборка не нужна"
    write_status uptodate "${prev:0:12}" "${new:0:12}"; trap - ERR; return 0
  fi
  ok "код обновлён: ${prev:0:12} → ${new:0:12}"

  merge_config "$HERE/.env.example" "$HERE/.env"   # мягко дописать новые ключи

  step "пересобираю и поднимаю стек" compose_up
  trap - ERR
  write_status updated "${prev:0:12}" "${new:0:12}"
  ok "═══ ХОСТ ОБНОВЛЁН до ${new:0:12} ═══"
  $COMPOSE ps >>"$PINTEST_LOG" 2>&1 || true
  note "статус контейнеров — в логе: $PINTEST_LOG"
}

# режим watch: крутимся и ждём маркер от вебки
if [ "${1:-}" = "--watch" ]; then
  info "watch-режим: слежу за маркером $MARKER (кнопка «обновить хост» в вебке)"
  mkdir -p "$HERE/data"
  while true; do
    if [ -f "$MARKER" ]; then
      info "маркер обнаружен — запускаю обновление хоста"
      rm -f "$MARKER"
      ( do_update ) || warn "обновление завершилось с ошибкой (лог: $PINTEST_LOG)"
    fi
    sleep 5
  done
fi

do_update
