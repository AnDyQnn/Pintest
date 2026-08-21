# webrce — учебная веб-мишень, изображающая RCE под конкретный CVE (SIM_CVE/SIM_BANNER).
FROM python:3.12-slim
COPY lab/targets/webrce/server.py /server.py
RUN echo 'flag{webrce_pwned}' > /flag && chmod 644 /flag
EXPOSE 80
CMD ["python3", "/server.py"]
LABEL org.pintest.role="target"
