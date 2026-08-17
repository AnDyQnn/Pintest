# fail2ban — слой защиты основного сервера (демонстрационный контейнер).
# На реальном сервере ставится через host/install.sh на сам хост и следит за sshd.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        fail2ban iptables rsyslog \
    && rm -rf /var/lib/apt/lists/*
COPY host/fail2ban/jail.local /etc/fail2ban/jail.local
# лог sshd монтируется/симулируется; в демо fail2ban поднимается и показывает статус
CMD ["sh","-c","touch /var/log/auth.log; fail2ban-server -xf start"]
LABEL org.pintest.role="fail2ban"
