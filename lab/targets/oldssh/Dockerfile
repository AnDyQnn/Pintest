FROM python:3.12-slim
COPY lab/targets/oldssh/server.py /server.py
EXPOSE 22
CMD ["python3", "/server.py"]
LABEL org.pintest.role="target" org.pintest.note="finding-without-module"
