#!/usr/bin/env bash
# ===================================================================
#  host/update.sh — обновление ХОСТА (control plane) из git с откатом.
#
#  Механика (как в HD[A] update.sh, но источник — git, не .tar-релиз):
#    • git fetch + git reset --hard origin/<branch>  (жёсткий сброс на git)
#    • docker compose up -d --build                  (пересборка+перезапуск)
#    • при ЛЮБОЙ ошибке — авто-откат на прежний коммит + пересборка
#
#  ДВА ПУТИ запуска:
#    1) из консоли:   sudo bash host/update.sh              (разово)
#    2) кнопкой с вебки: бэкенд пишет маркер host/data/.update-request,
#       а на хосте крутится  sudo bash host/update.sh --watch  (демон),
#       который ловит маркер и запускает обновление. Хост не рестартит
#       сам себя из контейнера — рестартит внешний watch-процесс.
# ===================================================================
if [ -z "${BASH_VERSION:-}" ]; then echo "[ERR] нужен bash: sudo bash host/update.sh" >&2; exit 1; fi
if grep -q $'\r' "${BASH_SOURCE[0]}" 2>/dev/null; then
  sed -i 's/\r$//' "${BASH_SOURCE[0]}"; exec bash "${BASH_SOURCE[0]}" "$@"
fi
set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info(){ echo -e "${CYAN}[INFO]${NC} $*"; }
ok(){   echo -e "${GREEN}[OK]${NC}   $*"; }
warn(){ echo -e "${YELLOW}[WARN]${NC} $*"; }
fail(){ echo -e "${RED}[ERR]${NC} $*"; exit 1; }

# корень репо = родитель папки host/
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
COMPOSE="docker compose -f $HERE/docker-compose.yml"
[ -f "$HERE/config.env" ] && COMPOSE="$COMPOSE --env-file $HERE/config.env"
BRANCH="${PINTEST_BRANCH:-$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)}"
REMOTE="${PINTEST_REMOTE:-origin}"
MARKER="$HERE/data/.update-request"

do_update() {
  cd "$ROOT"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "$ROOT — не git-репозиторий (клонируй репу на хост)"
  local prev; prev="$(git rev-parse HEAD)"
  info "текущий коммит: ${prev:0:12} · ветка $BRANCH"

  rollback() {
    warn "ОШИБКА обновления — откат на ${prev:0:12}"
    git reset --hard "$prev" || true
    $COMPOSE up -d --build || true
    fail "откат выполнен, поднята прежняя версия"
  }
  trap rollback ERR

  info "git fetch $REMOTE + reset --hard $REMOTE/$BRANCH"
  git fetch "$REMOTE" "$BRANCH"
  git reset --hard "$REMOTE/$BRANCH"
  local new; new="$(git rev-parse HEAD)"
  if [ "$new" = "$prev" ]; then ok "уже актуально (${new:0:12}) — пересборка не нужна"; trap - ERR; return 0; fi
  ok "обновлён код: ${prev:0:12} → ${new:0:12}"

  info "пересобираю и поднимаю стек хоста…"
  $COMPOSE up -d --build
  trap - ERR
  ok "═══ ХОСТ ОБНОВЛЁН до ${new:0:12} ═══"
  $COMPOSE ps
}

# режим watch: крутимся и ждём маркер от вебки
if [ "${1:-}" = "--watch" ]; then
  info "watch-режим: слежу за маркером $MARKER (кнопка «обновить хост» в вебке)"
  mkdir -p "$HERE/data"
  while true; do
    if [ -f "$MARKER" ]; then
      info "маркер обнаружен — запускаю обновление хоста"
      rm -f "$MARKER"
      ( do_update ) || warn "обновление завершилось с ошибкой (см. выше)"
    fi
    sleep 5
  done
fi

do_update
