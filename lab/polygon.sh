#!/usr/bin/env bash
# ==============================================================================
#  polygon.sh — АВТОНОМНЫЙ полигон устройств для ручной репетиции лабы Pintest.
#
#  Зависит ТОЛЬКО от docker CLI — код проекта не трогает. Поднимает N разнотипных
#  «устройств» (web, СУБД, камера, принтер, роутер, telnet-IoT, SCADA, VoIP…):
#    • часть — в сети целей lab_targets_net (её сканируют агенты — «связанные»);
#    • часть — в отдельной изолированной сети («несвязанные», для сегментации/pivot).
#  Эти контейнеры — исключительно для того, чтобы ты сам прогнал лабу руками.
#
#  Использование:
#     ./polygon.sh up [N]     поднять N устройств (по умолчанию из COUNT_DEFAULT)
#     ./polygon.sh down       снести весь полигон (устройства + изолированную сеть)
#     ./polygon.sh list       показать поднятое (имя · тип · сеть · IP · порт)
#     ./polygon.sh ips        только IP связанных устройств (для вставки в «Цели»)
#
#  Настройки — блок ниже (число, доля несвязанных, типы, имена сетей).
# ==============================================================================
set -u

# ── НАСТРОЙКИ (правь под себя) ────────────────────────────────────────────────
COUNT_DEFAULT="${POLYGON_COUNT:-12}"                 # сколько устройств поднимать по умолчанию
CONNECTED_NET="${POLYGON_NET:-lab_targets_net}"      # сеть целей, которую сканируют агенты
ISOLATED_NET="${POLYGON_ISO_NET:-pgon_isolated}"     # изолированная сеть «несвязанных»
ISO_SUBNET="${POLYGON_ISO_SUBNET:-172.45.0.0/24}"    # подсеть изолированной сети
CONN_IP_PREFIX="${POLYGON_IP_PREFIX:-172.30.0.}"     # свободные адреса в сети целей…
CONN_IP_START="${POLYGON_IP_START:-20}"              # …начиная с этого последнего октета (.20+)
ISOLATED_EVERY="${POLYGON_ISOLATED_EVERY:-4}"        # каждое N-е устройство — несвязанное (0 = все связаны)

# набор типов (перебираются по кругу; повторяй/убирай, как надо)
TYPES=(
  web-nginx web-apache-vuln db-mysql db-redis db-postgres cache-memcached
  camera-iot printer-iot router-iot telnet-iot scada-plc voip-sip
)

PREFIX="pgon"
LABEL="pintest.polygon=1"
# ──────────────────────────────────────────────────────────────────────────────

c_red=$'\e[31m'; c_grn=$'\e[32m'; c_yel=$'\e[33m'; c_cyn=$'\e[36m'; c_dim=$'\e[2m'; c_rst=$'\e[0m'
die(){ echo "${c_red}[polygon] $*${c_rst}" >&2; exit 1; }
info(){ echo "${c_cyn}[polygon]${c_rst} $*"; }

need_docker(){ command -v docker >/dev/null 2>&1 || die "docker не найден в PATH"; }

# порт, который «слушает» тип (для вывода и подсказки по скану)
type_port(){
  case "$1" in
    web-nginx|web-apache-vuln|camera-iot|router-iot) echo 80 ;;
    db-mysql)        echo 3306 ;;
    db-redis)        echo 6379 ;;
    db-postgres)     echo 5432 ;;
    cache-memcached) echo 11211 ;;
    printer-iot)     echo 9100 ;;
    telnet-iot)      echo 23 ;;
    scada-plc)       echo 502 ;;
    voip-sip)        echo 5060 ;;
    *) echo 0 ;;
  esac
}

# busybox-баннер на порту (для «железочных» сервисов без образа)
_banner(){ # name net iparg port banner
  docker run -d --name "$1" --label "$LABEL" --network "$2" $3 busybox \
    sh -c "while true; do printf '%b' '$5' | nc -l -p $4 -w 4 2>/dev/null; done" >/dev/null
}
# busybox-httpd страничка (камера/роутер выглядят как устройство и в браузере)
_web(){ # name net iparg port title body
  docker run -d --name "$1" --label "$LABEL" --network "$2" $3 busybox \
    sh -c "mkdir -p /www && printf '%s' '<title>$5</title>$6' > /www/index.html && httpd -f -p $4 -h /www" >/dev/null
}

spawn(){ # type name net iparg
  local t="$1" name="$2" net="$3" ip="$4"
  case "$t" in
    web-nginx)        docker run -d --name "$name" --label "$LABEL" --network "$net" $ip nginx:alpine >/dev/null ;;
    web-apache-vuln)  docker run -d --name "$name" --label "$LABEL" --network "$net" $ip httpd:2.4.49 >/dev/null ;;
    db-mysql)         docker run -d --name "$name" --label "$LABEL" --network "$net" $ip -e MYSQL_ROOT_PASSWORD=root mysql:5.7 >/dev/null ;;
    db-redis)         docker run -d --name "$name" --label "$LABEL" --network "$net" $ip redis:alpine >/dev/null ;;
    db-postgres)      docker run -d --name "$name" --label "$LABEL" --network "$net" $ip -e POSTGRES_PASSWORD=postgres postgres:alpine >/dev/null ;;
    cache-memcached)  docker run -d --name "$name" --label "$LABEL" --network "$net" $ip memcached:alpine >/dev/null ;;
    camera-iot)       _web "$name" "$net" "$ip" 80 "IP Camera" "<h1>NetSurveillance WEB</h1><p>Hi3516 IP Camera — DVR/NVR admin</p>" ;;
    router-iot)       _web "$name" "$net" "$ip" 80 "Router" "<h1>RouterOS admin</h1><p>MikroTik-like web admin</p>" ;;
    printer-iot)      _banner "$name" "$net" "$ip" 9100 "@PJL INFO ID\r\nHP LaserJet 4200 JetDirect\r\n" ;;
    telnet-iot)       docker run -d --name "$name" --label "$LABEL" --network "$net" $ip busybox telnetd -F -p 23 -l /bin/sh >/dev/null ;;
    scada-plc)        _banner "$name" "$net" "$ip" 502 "Schneider Modbus TCP PLC ready\r\n" ;;
    voip-sip)         _banner "$name" "$net" "$ip" 5060 "SIP/2.0 200 OK\r\nServer: Asterisk PBX\r\n" ;;
    *) echo "неизвестный тип: $t" >&2; return 1 ;;
  esac
}

ensure_nets(){
  docker network inspect "$CONNECTED_NET" >/dev/null 2>&1 || \
    die "сеть '$CONNECTED_NET' не найдена — сначала подними лабу (bash lab/build.sh), либо задай POLYGON_NET"
  if ! docker network inspect "$ISOLATED_NET" >/dev/null 2>&1; then
    info "создаю изолированную сеть $ISOLATED_NET ($ISO_SUBNET)"
    docker network create --subnet "$ISO_SUBNET" "$ISOLATED_NET" >/dev/null
  fi
}

container_ip(){ docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$1" 2>/dev/null | awk '{print $1}'; }
container_net(){ docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$1" 2>/dev/null; }
polygon_names(){ docker ps -a --filter "label=$LABEL" --format '{{.Names}}' | sort; }

cmd_up(){
  need_docker; ensure_nets
  local count="${1:-$COUNT_DEFAULT}"
  [ "$count" -ge 1 ] 2>/dev/null || die "нужно число устройств (>=1)"
  info "поднимаю $count устройств: связанные → $CONNECTED_NET, каждое ${ISOLATED_EVERY}-е → $ISOLATED_NET (изолировано)"
  local connidx=0 i
  for ((i=0;i<count;i++)); do
    local t="${TYPES[$((i % ${#TYPES[@]}))]}"
    local name="${PREFIX}-${t}-${i}"
    docker rm -f "$name" >/dev/null 2>&1
    local isolated=0
    if [ "$ISOLATED_EVERY" -gt 0 ] && [ $(((i+1) % ISOLATED_EVERY)) -eq 0 ]; then isolated=1; fi
    if [ "$isolated" -eq 1 ]; then
      if spawn "$t" "$name" "$ISOLATED_NET" ""; then echo "  ${c_yel}⊘${c_rst} $name  ($t, изолирован)"; fi
    else
      local ip="${CONN_IP_PREFIX}$((CONN_IP_START + connidx))"; connidx=$((connidx+1))
      if spawn "$t" "$name" "$CONNECTED_NET" "--ip $ip"; then echo "  ${c_grn}●${c_rst} $name  ($t) → $ip:$(type_port "$t")"; fi
    fi
  done
  echo; cmd_ips
}

cmd_list(){
  need_docker
  local names; names="$(polygon_names)"
  [ -n "$names" ] || { info "полигон пуст"; return; }
  printf "%-28s %-16s %-18s %-16s %s\n" "ИМЯ" "ТИП" "СЕТЬ" "IP" "ПОРТ"
  while read -r n; do
    [ -n "$n" ] || continue
    local t="${n#${PREFIX}-}"; t="${t%-*}"
    printf "%-28s %-16s %-18s %-16s %s\n" "$n" "$t" "$(container_net "$n")" "$(container_ip "$n")" "$(type_port "$t")"
  done <<< "$names"
}

cmd_ips(){
  need_docker
  local any=0
  echo "${c_grn}СВЯЗАННЫЕ${c_rst} (агенты видят — вставь в веб-вкладку «Цели»):"
  while read -r n; do
    [ -n "$n" ] || continue
    if [ "$(container_net "$n")" = "$CONNECTED_NET" ]; then echo "  $(container_ip "$n")"; any=1; fi
  done <<< "$(polygon_names)"
  [ "$any" -eq 1 ] || echo "  ${c_dim}— нет —${c_rst}"
  echo
  echo "${c_yel}НЕСВЯЗАННЫЕ${c_rst} (изолированы, агенты НЕ видят — для сегментации/pivot):"
  any=0
  while read -r n; do
    [ -n "$n" ] || continue
    if [ "$(container_net "$n")" = "$ISOLATED_NET" ]; then echo "  $(container_ip "$n")  ${c_dim}($n)${c_rst}"; any=1; fi
  done <<< "$(polygon_names)"
  [ "$any" -eq 1 ] || echo "  ${c_dim}— нет —${c_rst}"
}

cmd_down(){
  need_docker
  local names; names="$(polygon_names)"
  if [ -n "$names" ]; then
    info "сношу устройства полигона…"
    echo "$names" | xargs -r docker rm -f >/dev/null 2>&1
  fi
  docker network rm "$ISOLATED_NET" >/dev/null 2>&1 && info "изолированная сеть $ISOLATED_NET удалена" || true
  info "готово."
}

usage(){ sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

case "${1:-}" in
  up)   shift; cmd_up "${1:-$COUNT_DEFAULT}" ;;
  down) cmd_down ;;
  list) cmd_list ;;
  ips)  cmd_ips ;;
  ""|-h|--help|help) usage ;;
  *) die "неизвестная команда '$1' (см. ./polygon.sh --help)" ;;
esac
