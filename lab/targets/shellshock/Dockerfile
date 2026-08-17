FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends bash \
    && rm -rf /var/lib/apt/lists/*
COPY lab/targets/shellshock/server.py /server.py
RUN echo 'flag{shellshock_cgi_rce}' > /flag && chmod 644 /flag
EXPOSE 80
CMD ["python3", "/server.py"]
LABEL org.pintest.role="target" org.pintest.cve="CVE-2014-6271"
