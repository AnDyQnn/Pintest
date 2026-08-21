# ===================================================================
#  host/lib.sh — общие хелперы для install.sh и update.sh:
#    • цветной аккуратный вывод (шаги/статусы), полный лог — в файл;
#    • мягкий мерж config.env (дописать недостающие ключи, не трогая значения);
#    • set_config (обновить/добавить ключ), detect_ssh_port.
#  Источится: . "$HERE/lib.sh"
# ===================================================================

# --- цвета (только в TTY и если не запрещено NO_COLOR) ---
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_G=$'\033[0;32m'; C_Y=$'\033[1;33m'; C_R=$'\033[0;31m'
  C_C=$'\033[0;36m'; C_D=$'\033[2m'; C_B=$'\033[1m'; C_N=$'\033[0m'
else
  C_G=; C_Y=; C_R=; C_C=; C_D=; C_B=; C_N=
fi

# куда сыпать полный (шумный) вывод apt/docker — чтобы не забивать консоль
PINTEST_LOG="${PINTEST_LOG:-/tmp/pintest-setup.log}"
: > "$PINTEST_LOG" 2>/dev/null || PINTEST_LOG=/dev/null

_STEPNO=0

title(){ printf '\n%s%s%s\n' "$C_B$C_C" "$*" "$C_N"; }
info(){  printf '  %sℹ%s %s\n' "$C_C" "$C_N" "$*"; }
ok(){    printf '  %s✓%s %s\n' "$C_G" "$C_N" "$*"; }
warn(){  printf '  %s!%s %s\n' "$C_Y" "$C_N" "$*"; }
err(){   printf '  %s✗%s %s\n' "$C_R" "$C_N" "$*"; }
note(){  printf '     %s%s%s\n' "$C_D" "$*" "$C_N"; }

# step "описание" cmd args…  — выполнить ТИХО (вывод в лог), напечатать [NN] описание … ✓/✗.
# При падении печатает хвост лога и возвращает 1 (под set -e — прервёт скрипт).
step(){
  _STEPNO=$((_STEPNO + 1))
  local desc="$1"; shift
  printf '  %s[%02d]%s %s … ' "$C_B" "$_STEPNO" "$C_N" "$desc"
  if "$@" >>"$PINTEST_LOG" 2>&1; then
    printf '%s✓%s\n' "$C_G" "$C_N"; return 0
  fi
  printf '%s✗%s\n' "$C_R" "$C_N"
  note "лог: $PINTEST_LOG (последние строки):"
  tail -n 12 "$PINTEST_LOG" 2>/dev/null | sed "s/^/       ${C_D}/;s/\$/${C_N}/"
  return 1
}
# step_soft — то же, но падение НЕ фатально (для необязательных шагов)
step_soft(){ step "$@" || true; }

# set_config KEY VALUE FILE — обновить существующий ключ или дописать новый.
set_config(){
  local k="$1" v="$2" f="$3"
  if grep -qE "^[[:space:]]*${k}=" "$f" 2>/dev/null; then
    sed -i -E "s|^[[:space:]]*${k}=.*|${k}=${v}|" "$f"
  else
    printf '%s=%s\n' "$k" "$v" >> "$f"
  fi
}

# get_config KEY FILE — вернуть значение ключа (или пусто). Безопасно под set -e.
get_config(){
  local k="$1" f="$2"
  { grep -E "^[[:space:]]*${k}=" "$f" 2>/dev/null || true; } | tail -1 | cut -d= -f2- | tr -d '[:space:]'
}

# merge_config EXAMPLE TARGET — МЯГКО дописать в TARGET недостающие ключи из EXAMPLE
# (значения существующих ключей НЕ трогаем). Возвращает 0. Печатает результат.
merge_config(){
  local ex="$1" tgt="$2" added=0 k line
  if [ ! -f "$tgt" ]; then
    cp "$ex" "$tgt"; ok "создан $(basename "$tgt") из шаблона"; return 0
  fi
  for k in $(grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$ex" \
             | sed -E 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=.*/\1/'); do
    if ! grep -qE "^[[:space:]]*${k}=" "$tgt" 2>/dev/null; then
      line="$( { grep -E "^[[:space:]]*${k}=" "$ex" || true; } | head -1)"
      [ "$added" -eq 0 ] && printf '\n# --- дописано при обновлении %s ---\n' "$(date +%Y-%m-%d)" >> "$tgt"
      printf '%s\n' "$line" >> "$tgt"; added=$((added + 1))
    fi
  done
  if [ "$added" -gt 0 ]; then ok "config.env дополнен новыми ключами: ${added}"
  else info "config.env актуален — новых ключей нет"; fi
}

# detect_ssh_port — реальный порт sshd: из живого SSH-подключения -> sshd -T -> конфиг -> 22.
detect_ssh_port(){
  local p
  p="$(printf '%s' "${SSH_CONNECTION:-}" | awk '{print $4}')"
  [ -z "$p" ] && p="$( { sshd -T 2>/dev/null || true; } | awk '/^port /{print $2; exit}')"
  [ -z "$p" ] && p="$( { grep -E '^[[:space:]]*Port[[:space:]]+[0-9]+' /etc/ssh/sshd_config 2>/dev/null || true; } | awk '{print $2; exit}')"
  [ -z "$p" ] && p=22
  printf '%s' "$p"
}
