#!/usr/bin/env sh
# entrypoint.sh — сгенерировать самоподписанный SSL (если нет) и запустить nginx.
set -u
CERT_DIR=/etc/nginx/certs
mkdir -p "$CERT_DIR"
if [ ! -f "$CERT_DIR/server.crt" ]; then
  echo "[frontend] генерирую самоподписанный сертификат…"
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" \
    -subj "/C=RU/O=pintest-lab/CN=pintest.local" >/dev/null 2>&1
fi
exec nginx -g 'daemon off;'
