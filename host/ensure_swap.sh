#!/bin/bash
# Идемпотентно гарантирует swap на ноде. Нужен на 1–2 ГБ RAM, чтобы бот/сборки не словили
# OOM. Безопасно вызывать многократно (install.sh и КАЖДЫЙ deploy.sh): если swap уже есть и
# достаточный — ничего не делает. Переживает ребут (/etc/fstab).
#
# Размер: по умолчанию 2 ГБ, НО не больше ~25% размера корневого диска (на маленьких 10-ГБ
# VPS иначе swap съедал пол-диска — реальный баг на DE: был 5 ГБ swapfile, к тому же с
# «дырами» от fallocate → swapon его отвергал, и 5 ГБ просто висели впустую).
# Переопределить:  SWAP_SIZE_GB=4 bash ensure_swap.sh
set -u

SWAP_SIZE_GB="${SWAP_SIZE_GB:-2}"
SWAP_FILE="${SWAP_FILE:-/swapfile}"
MIN_KB=1048576   # < ~1 ГБ swap → создаём

# только root может включать swap; без прав — тихо выходим (не ломаем деплой)
if [ "$(id -u)" -ne 0 ]; then
    echo "[swap] не root — пропускаю (запусти deploy/install от root для swap)"
    exit 0
fi

# Не даём swap съесть маленький диск: не больше ~25% общего размера ФС корня.
DISK_TOTAL_GB=$(df -BG / 2>/dev/null | awk 'NR==2{gsub(/G/,"",$2); print $2}')
if [ -n "${DISK_TOTAL_GB:-}" ] && [ "${DISK_TOTAL_GB:-0}" -gt 0 ]; then
    MAX_BY_DISK=$(( DISK_TOTAL_GB / 4 ))
    [ "$MAX_BY_DISK" -lt 1 ] && MAX_BY_DISK=1
    if [ "$SWAP_SIZE_GB" -gt "$MAX_BY_DISK" ]; then
        echo "[swap] диск ${DISK_TOTAL_GB} ГБ — ограничиваю swap с ${SWAP_SIZE_GB} до ${MAX_BY_DISK} ГБ"
        SWAP_SIZE_GB="$MAX_BY_DISK"
    fi
fi

SWAP_TOTAL_KB=$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${SWAP_TOTAL_KB:-0}" -ge "$MIN_KB" ]; then
    echo "[swap] уже есть ($((SWAP_TOTAL_KB/1024)) МБ) — пропускаю"
    exit 0
fi

echo "[swap] swap мал/отсутствует — создаю $SWAP_FILE на ${SWAP_SIZE_GB} ГБ..."
swapoff "$SWAP_FILE" 2>/dev/null || true
rm -f "$SWAP_FILE" 2>/dev/null || true
# dd (а не fallocate): fallocate может создать файл с «дырами», и тогда swapon его отвергает
# ("swapfile has holes"), оставляя большой файл впустую занимать диск. dd пишет плотно.
if dd if=/dev/zero of="$SWAP_FILE" bs=1M count=$((SWAP_SIZE_GB*1024)) status=none; then
    chmod 600 "$SWAP_FILE" && mkswap "$SWAP_FILE" >/dev/null
    if swapon "$SWAP_FILE" 2>/dev/null; then
        grep -q "^${SWAP_FILE} " /etc/fstab || echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
        sysctl -w vm.swappiness=10 >/dev/null 2>&1 || true
        SYSCTL_CONF=/etc/sysctl.d/99-pintest-security.conf
        grep -q '^vm.swappiness' "$SYSCTL_CONF" 2>/dev/null || echo 'vm.swappiness = 10' >> "$SYSCTL_CONF"
        echo "[swap] ✅ swap ${SWAP_SIZE_GB} ГБ включён (и в /etc/fstab — переживёт ребут)"
    else
        echo "[swap] ⚠️ swapon не удался — удаляю файл, чтобы не занимал диск впустую"
        rm -f "$SWAP_FILE"
    fi
else
    echo "[swap] ⚠️ не удалось создать swapfile (мало места?) — пропускаю"
    rm -f "$SWAP_FILE" 2>/dev/null || true
fi
