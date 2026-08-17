# ===================================================================
#  pintest-awg-base — общий базовый образ с AmneziaWG (userspace)
#  Собирает amneziawg-go (userspace-реализация WireGuard с обфускацией
#  «амнезия») + amneziawg-tools (awg / awg-quick) из исходников.
#  От него наследуются образы host/ и agent/ — тоннель одинаковый на всех.
#
#  Userspace => работает в обычном Docker-контейнере через /dev/net/tun,
#  без модуля ядра и без WSL-специфики. Нужен --cap-add NET_ADMIN.
# ===================================================================

# ---- стадия сборки: Go + make -------------------------------------
FROM golang:1-bookworm AS build
ARG AWG_GO_REF=master
ARG AWG_TOOLS_REF=master
RUN apt-get update && apt-get install -y --no-install-recommends \
        git make gcc libc6-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# amneziawg-go — userspace-демон, создаёт TUN-интерфейс awg0.
# Собираем go build'ом напрямую (детерминированный путь вывода), не полагаясь на Makefile.
RUN mkdir -p /out && git clone --depth 1 --branch ${AWG_GO_REF} \
        https://github.com/amnezia-vpn/amneziawg-go /src/awg-go \
    && cd /src/awg-go && go build -o /out/amneziawg-go . \
    && /out/amneziawg-go --version 2>/dev/null || ls -l /out/amneziawg-go

# amneziawg-tools — awg / awg-quick (форк wireguard-tools)
RUN git clone --depth 1 --branch ${AWG_TOOLS_REF} \
        https://github.com/amnezia-vpn/amneziawg-tools /src/awg-tools \
    && cd /src/awg-tools/src && make \
    && make install DESTDIR=/out-tools WITH_BASHCOMPLETION=no WITH_SYSTEMDUNITS=no

# ---- рантайм-стадия: Ubuntu 24.04 ---------------------------------
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
# iproute2 — ip addr/link; iptables — маршрутизация; заранее ставим то,
# что нужно и туннелю, и продукту (nmap/ssh ставят наследники).
RUN apt-get update && apt-get install -y --no-install-recommends \
        iproute2 iptables procps iputils-ping ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /out/amneziawg-go /usr/bin/amneziawg-go
COPY --from=build /out-tools/usr/bin/awg        /usr/bin/awg
COPY --from=build /out-tools/usr/bin/awg-quick  /usr/bin/awg-quick

# awg-quick должен использовать userspace-реализацию (нет модуля ядра)
ENV WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go
ENV WG_SUDO=1
# каталог конфигов awg-quick для amneziawg-tools
RUN mkdir -p /etc/amnezia/amneziawg /var/run/wireguard

LABEL org.pintest.role="awg-base" \
      org.pintest.desc="AmneziaWG userspace base (amneziawg-go + amneziawg-tools)"
