# ===================================================================
#  agent — удалённая нода (сканер + опц. эксплуататор) поверх AmneziaWG.
#  Контекст сборки — КОРЕНЬ репозитория (нужны core/, exploits/, agent/).
#  Container-зависимости здесь; install.sh на реальном сервере лишь ставит
#  Docker и поднимает ЭТОТ образ — код при переносе не меняется.
# ===================================================================
FROM pintest-awg-base:latest

ENV DEBIAN_FRONTEND=noninteractive \
    PINTEST_ROOT=/opt/pintest \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# nmap (+ NSE vulners), python, sshd для provisioning, утилиты туннеля
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        nmap openssh-server iproute2 iputils-ping iptables bash ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

# python-зависимости агента (кэш-слой отдельно от кода)
COPY agent/requirements.txt /tmp/req.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/req.txt

# движок, модульный блок эксплойтов и код агента
COPY core/     /opt/pintest/core/
COPY exploits/ /opt/pintest/exploits/
COPY agent/    /opt/pintest/agent/

RUN chmod +x /opt/pintest/agent/entrypoint.sh /opt/pintest/agent/scripts/*.sh \
    && sed -i 's/PermitRootLogin.*/PermitRootLogin yes/;s/#\?PasswordAuthentication.*/PasswordAuthentication yes/' \
        /etc/ssh/sshd_config 2>/dev/null || true

WORKDIR /opt/pintest/agent
EXPOSE 9101 22
ENTRYPOINT ["/opt/pintest/agent/entrypoint.sh"]

LABEL org.pintest.role="agent"
