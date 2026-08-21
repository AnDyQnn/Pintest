#!/usr/bin/env bash
# ===================================================================
#  host/install.sh — bootstrap ОСНОВНОГО сервера на РЕАЛЬНОЙ Ubuntu.
#
#  Здесь: Docker -> хардненинг (ufw + fail2ban под SSH-порт) -> сборка стека.
#  Шумный вывод apt/docker уходит в лог-файл, в консоли — аккуратные шаги.
#
#  Использование:  cd pintest/host && sudo ./install.sh   [--fresh]
#    --fresh — снести host/data (БД/VPN-ключи/отчёты) и подняться с нуля.
# ===================================================================
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive   # apt тихий в лог — без невидимых debconf-промптов
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
. "$HERE/lib.sh"
CFG="$HERE/config.env"

FRESH="${PINTEST_FRESH:-0}"
[ "${1:-}" = "--fresh" ] && FRESH=1

# ------------------------------- фазы --------------------------------------
install_docker(){
  apt-get update -y
  apt-get install -y ca-certificates curl
  install -m0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
}

# Перевести sshd на порт (логика рабочего VPN-проекта AnDyQnn/vpn_awg_2_VPS_pub):
# порт задаём в sshd_config И МАСКИРУЕМ ssh.socket. На Ubuntu 24.04 sshd идёт через
# socket-активацию — она берёт порт из сокета (22), sshd_config игнорится, и после
# апдейта/ребута порт «слетает» на 22 (а ufw его уже не пускает → лок-аут). Маскировка
# сокета отдаёт порт обычному sshd.service из sshd_config — порт держится всегда.
configure_ssh_port(){
  local p="$1"
  sed -i "s/^#*Port .*/Port ${p}/" /etc/ssh/sshd_config
  grep -qE "^[[:space:]]*Port[[:space:]]+${p}([[:space:]]|\$)" /etc/ssh/sshd_config \
    || printf 'Port %s\n' "$p" >> /etc/ssh/sshd_config
  rm -f /etc/systemd/system/ssh.socket.d/*.conf 2>/dev/null || true
  systemctl disable --now ssh.socket 2>/dev/null || true
  systemctl mask ssh.socket 2>/dev/null || true
  systemctl daemon-reload 2>/dev/null || true
  systemctl enable ssh 2>/dev/null || systemctl enable sshd 2>/dev/null || true
  sshd -t                                          # валидация конфига (ошибка → шаг ✗, без рестарта)
  systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
}
port_listening(){ ss -tlnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}\$"; }

harden_sysctl(){
  cat > /etc/sysctl.d/99-pintest-security.conf <<'S'
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_synack_retries = 2
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.tcp_rfc1337 = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
S
  sysctl --system >/dev/null 2>&1 || true
}

# ufw БЕЗ reset. SSH-порт к этому моменту уже реально слушает (configure_ssh_port),
# поэтому deny-incoming + enable не роняет доступ. На своём порту — allow, на 22 — limit.
harden_ufw(){
  ufw default deny incoming
  ufw default allow outgoing
  if [ "$SSH_PORT" = "22" ]; then ufw limit 22/tcp; else ufw allow "${SSH_PORT}/tcp"; fi
  ufw allow 51820/udp             # вход туннеля AmneziaWG
  ufw allow 80/tcp                # http → редирект на https
  ufw allow 443/tcp               # вебка HTTPS (в идеале — только из VPN-подсети)
  ufw --force enable
}

harden_fail2ban(){
  # jail.local: backend=systemd (journald) — порт в jail не нужен (бан iptables-allports).
  cp "$HERE/fail2ban/jail.local" /etc/fail2ban/jail.local
  systemctl enable fail2ban
  systemctl restart fail2ban
}

fresh_wipe(){
  ( cd "$HERE" && { docker compose --env-file config.env down -v || docker compose down || true; } )
  rm -rf "$HERE/data"
}

stack_up(){ ( cd "$HERE" && docker compose --env-file config.env up -d --build ); }

# ------------------------------- ход --------------------------------------
title "pintest · установка хоста"
note "полный лог: $PINTEST_LOG"

# 1) Docker
if command -v docker >/dev/null 2>&1; then
  ok "Docker уже установлен"
else
  step "устанавливаю Docker" install_docker
fi
step_soft "автозапуск Docker при ребуте" systemctl enable --now docker

# 2) config.env — создать/мягко дополнить (не трогая значения)
title "конфигурация"
merge_config "$HERE/config.example.env" "$CFG"

# 3) SSH-порт — задаётся при установке. Enter = оставить текущий; другой порт —
#    sshd РЕАЛЬНО переносится на него (sed Port + маскировка ssh.socket), и только
#    после подтверждения, что он слушает, включается ufw. Так — без лок-аута.
CUR_PORT="$(detect_ssh_port)"
DEF_PORT="$(get_config SSH_PORT "$CFG")"; [ -z "$DEF_PORT" ] && DEF_PORT="$CUR_PORT"
if [ -n "${SSH_PORT:-}" ]; then
  :                                             # из окружения
elif [ -t 0 ] && [ "${PINTEST_NONINTERACTIVE:-0}" != "1" ]; then
  info "sshd сейчас на порту ${C_B}${CUR_PORT}${C_N}. Enter — оставить; другой порт — перенесу sshd на него"
  printf '  %sпорт SSH%s [%s%s%s]: ' "$C_C" "$C_N" "$C_B" "$DEF_PORT" "$C_N"
  read -r _ans || _ans=""
  SSH_PORT="${_ans:-$DEF_PORT}"
else
  SSH_PORT="$DEF_PORT"
fi
set_config SSH_PORT "$SSH_PORT" "$CFG"

# Если выбран НЕ текущий порт — реально переносим sshd на него (иначе бессмысленно
# и опасно). SSH_OK=1 только когда порт подтверждённо слушает — иначе ufw НЕ включим.
SSH_OK=1
if [ "$SSH_PORT" != "$CUR_PORT" ]; then
  step_soft "перевожу sshd на порт ${SSH_PORT} (+ маскирую ssh.socket)" configure_ssh_port "$SSH_PORT"
  if port_listening "$SSH_PORT"; then
    ok "sshd теперь слушает ${C_B}${SSH_PORT}${C_N} — ДАЛЬШЕ подключайся по нему (старая сессия не рвётся)"
  else
    SSH_OK=0
    warn "sshd не поднялся на ${SSH_PORT} — firewall НЕ включаю (анти-лок-аут)"
    note "проверь: sshd -t; systemctl status ssh; ss -tlnp | grep ssh"
  fi
else
  ok "SSH-порт: ${C_B}${SSH_PORT}${C_N} (текущий, записан в config.env)"
fi

# 4) Хардненинг
if [ "${PINTEST_HARDEN:-1}" = "1" ]; then
  title "хардненинг сервера"
  step_soft "ставлю ufw + fail2ban" apt-get install -y ufw fail2ban python3-systemd
  step_soft "sysctl: защита от SYN-флуда/спуфинга" harden_sysctl
  step_soft "fail2ban: jail sshd (journald + ignoreip)" harden_fail2ban
  f2b_ok=0
  for _ in 1 2 3; do fail2ban-client status sshd >/dev/null 2>&1 && { f2b_ok=1; break; }; sleep 1; done
  [ "$f2b_ok" = 1 ] && ok "fail2ban jail sshd активен" \
    || warn "fail2ban jail sshd не активен — проверь: fail2ban-client status sshd"
  if [ "$SSH_OK" = 1 ]; then
    step_soft "ufw: правила (SSH ${SSH_PORT}, 80/443, 51820/udp) + включение" harden_ufw
  else
    warn "ufw НЕ включён — SSH-порт ${SSH_PORT} не подтверждён; исправь sshd и включи ufw вручную"
    note "после починки:  sudo ufw allow ${SSH_PORT}/tcp && sudo ufw --force enable"
  fi
fi

# 5) Стек
title "сборка и запуск стека"
step "собираю образ pintest-awg-base" docker build -t pintest-awg-base:latest "$ROOT/awg-base"

if [ "$FRESH" = "1" ] && [ -d "$HERE/data" ]; then
  step "--fresh: стираю host/data (чистый старт)" fresh_wipe
fi

# гард версии Postgres (образ = 17; на чужом каталоге PG не стартует)
PGV_FILE="$HERE/data/pg/PG_VERSION"
if [ -f "$PGV_FILE" ]; then
  cur="$(tr -d '[:space:]' < "$PGV_FILE" 2>/dev/null || echo '?')"
  if [ "$cur" != "17" ]; then
    err "в host/data/pg лежит БД PostgreSQL ${cur}, а образ теперь 17 — Postgres не стартует на чужой версии."
    note "чистый старт (СОТРЁТ host/data):  sudo ./install.sh --fresh"
    exit 1
  fi
fi

step "собираю и поднимаю стек (может занять пару минут)" stack_up

# 6) bootstrap admin VPN
BOOT="$HERE/data/bootstrap-admin.conf"
printf '  %s[..]%s жду backend и bootstrap admin VPN … ' "$C_B" "$C_N"
for _ in $(seq 1 30); do [ -f "$BOOT" ] && break; sleep 2; done
[ -f "$BOOT" ] && printf '%s✓%s\n' "$C_G" "$C_N" || printf '%s⌛%s\n' "$C_Y" "$C_N"

# 7) итог
title "готово"
if [ -f "$BOOT" ]; then
  ok "первый вход (VPN): импортируй в AmneziaWG-клиент конфиг:"
  note "$BOOT"
  info "затем открой вебку по туннелю и войди под кредами из config.env (ADMIN_USER/ADMIN_PASSWORD)"
else
  warn "bootstrap-конфиг ещё не готов — появится в host/data/bootstrap-admin.conf"
  note "либо создай админ-конфиг во вкладке «VPN» после первого входа"
fi
info "вебка: ${C_B}https://<host>${C_N} (по чистому IP, без порта; http сам редиректит на https)"
