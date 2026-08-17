# ===================================================================
#  vpn — AmneziaWG-сервер (userspace) + control-API.
#  Наследует общий awg-base. Контекст сборки — КОРЕНЬ репозитория.
# ===================================================================
FROM pintest-awg-base:latest

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv procps \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

COPY host/vpn/requirements.txt /tmp/req.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/req.txt

COPY host/vpn/ /opt/pintest/host/vpn/
RUN chmod +x /opt/pintest/host/vpn/entrypoint.sh

WORKDIR /opt/pintest/host/vpn
EXPOSE 8080 51820/udp
ENTRYPOINT ["/opt/pintest/host/vpn/entrypoint.sh"]

LABEL org.pintest.role="vpn"
