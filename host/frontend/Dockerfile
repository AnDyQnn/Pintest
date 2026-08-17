# frontend — nginx + статика дашборда, HTTPS с самоподписанным сертом.
FROM nginx:1.27-alpine

RUN apk add --no-cache openssl
COPY host/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY host/frontend/static/    /usr/share/nginx/html/
COPY host/frontend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 443 80
ENTRYPOINT ["/entrypoint.sh"]
LABEL org.pintest.role="frontend"
