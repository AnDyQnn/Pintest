# ===================================================================
#  backend — control plane (FastAPI, вся полезная нагрузка).
#  Контекст сборки — КОРЕНЬ репозитория (нужны core/, exploits/, agent/, host/).
# ===================================================================
FROM python:3.12-slim

ENV PINTEST_ROOT=/opt/pintest \
    HOST_REPO_DIR=/opt/pintest \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/pintest:/opt/pintest/host/backend

# git — для механики само-обновления хоста (git pull); openssh-client не нужен (paramiko)
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY host/backend/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

# движок, эксплойты, код агента (для сборки бандлов обновления) и сам backend
COPY core/     /opt/pintest/core/
COPY exploits/ /opt/pintest/exploits/
COPY agent/    /opt/pintest/agent/
COPY host/backend/ /opt/pintest/host/backend/

WORKDIR /opt/pintest/host/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

LABEL org.pintest.role="backend"
