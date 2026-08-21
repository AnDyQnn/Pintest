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

# --fresh (или PINTEST_FRESH=1) — ЧИСТЫЙ СТАРТ: снести host/data (БД, VPN-ключи,
# отчёты, бэкапы) и подняться с нуля. Нужен, когда хочешь голый сервак: без него
# install ПЕРЕИСПОЛЬЗУЕТ уже лежащую host/data/pg (БД переживает переустановку —
# отсюда «старые данные/креды не сменились»).
FRESH="${PINTEST_FRESH:-0}"
[ "${1:-}" = "--fresh" ] && FRESH=1

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

# docker сам поднимается при РЕБУТЕ ТАЧКИ (иначе стек не встанет после ребута).
# Вместе с restart:unless-stopped в compose это = весь стек авто-поднимается при загрузке.
systemctl enable --now docker >/dev/null 2>&1 || true

# 2) конфиг (создаём ДО хардненинга — из него берём SSH_PORT для ufw)
if [ ! -f "$HERE/config.env" ]; then
  cp "$HERE/config.example.env" "$HERE/config.env"
  echo "[install] создан host/config.env — адрес хоста (AWG_ENDPOINT) определится САМ;"
  echo "[install]   пропиши его вручную, только если авто-детект ошибётся (сложный NAT)"
fi
SSH_PORT="$(grep -E '^[[:space:]]*SSH_PORT=' "$HERE/config.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]')"
SSH_PORT="${SSH_PORT:-22}"

# 3) Хардненинг основного сервера (закрыть от внешнего)
if [ "${PINTEST_HARDEN:-1}" = "1" ]; then
  echo "[install] хардненинг: ufw + fail2ban (SSH-порт ${SSH_PORT})…"
  apt-get install -y ufw fail2ban || echo "[install][WARN] не поставился ufw/fail2ban — проверь apt/сеть"
  ufw --force reset || true
  ufw default deny incoming || true
  ufw default allow outgoing || true
  ufw allow "${SSH_PORT}/tcp" || true   # SSH (порт из config.env; ключи-only — по желанию ниже)
  ufw allow 51820/udp || true           # вход туннеля AmneziaWG
  ufw allow 80/tcp || true              # http → редирект на https (вход по чистому IP)
  ufw allow 443/tcp || true             # вебка HTTPS (в идеале — только из VPN-подсети)
  ufw --force enable || true
  # fail2ban: наш jail (journald + ignoreip приватных сетей) + перезапуск для загрузки
  cp "$HERE/fail2ban/jail.local" /etc/fail2ban/jail.local || true
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban >/dev/null 2>&1 || true
  fail2ban-client status sshd >/dev/null 2>&1 && echo "[install] fail2ban: jail sshd активен" \
    || echo "[install][WARN] fail2ban jail sshd не активен — проверь: fail2ban-client status sshd"
  # SSH: выключить парольный вход (только ключи) — раскомментируй при готовых ключах:
  # sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && systemctl reload ssh
fi

# 4) общий образ AmneziaWG + стек
echo "[install] собираю pintest-awg-base…"
docker build -t pintest-awg-base:latest "$ROOT/awg-base"
cd "$HERE"

# 4a) чистый старт по запросу: снести host/data и подняться с нуля
if [ "$FRESH" = "1" ] && [ -d "$HERE/data" ]; then
  echo "[install] --fresh: останавливаю стек и стираю host/data (БД, VPN-ключи, отчёты)…"
  docker compose --env-file config.env down -v >/dev/null 2>&1 || docker compose down >/dev/null 2>&1 || true
  rm -rf "$HERE/data"
fi

# 4b) гард версии Postgres: образ теперь 17. Если в host/data/pg лежит БД другой
#     мажорной версии — контейнер НЕ стартанёт на чужом каталоге. Явно объясняем.
PGV_FILE="$HERE/data/pg/PG_VERSION"
if [ -f "$PGV_FILE" ]; then
  cur="$(tr -d '[:space:]' < "$PGV_FILE" 2>/dev/null || echo '?')"
  if [ "$cur" != "17" ]; then
    echo "[install][ERR] в host/data/pg лежит БД PostgreSQL $cur, а образ теперь 17 —"
    echo "               Postgres не запустится на чужой мажорной версии каталога."
    echo "               Чистый старт (СОТРЁТ host/data):  sudo ./install.sh --fresh"
    exit 1
  fi
fi

echo "[install] поднимаю стек хоста…"
docker compose --env-file config.env up -d --build

# 5) bootstrap-доступ: ждём backend и вытаскиваем первый админский VPN-конфиг,
#    сгенерённый на старте (иначе к вебке по туннелю не подключиться)
echo "[install] жду backend и bootstrap admin VPN…"
BOOT="$HERE/data/bootstrap-admin.conf"
for i in $(seq 1 30); do [ -f "$BOOT" ] && break; sleep 2; done

echo
echo "== готово =="
if [ -f "$BOOT" ]; then
  echo "ПЕРВЫЙ ВХОД (VPN): импортируй в AmneziaWG-клиент конфиг:"
  echo "    $BOOT"
  echo "  затем открой вебку по туннелю и войди под кредами из config.env (ADMIN_USER/ADMIN_PASSWORD)."
else
  echo "bootstrap-конфиг ещё не готов (VPN поднимается) — появится в host/data/bootstrap-admin.conf,"
  echo "  либо создай админ-конфиг во вкладке «VPN» после первого входа."
fi
echo "Вебка: https://<host>   (по чистому IP, без порта; http сам редиректит на https)"
