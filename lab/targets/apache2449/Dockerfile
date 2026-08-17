FROM python:3.12-slim
COPY lab/targets/apache2449/server.py /server.py
RUN echo 'flag{apache_2449_traversal_rce}' > /flag && chmod 644 /flag
EXPOSE 80
CMD ["python3", "/server.py"]
LABEL org.pintest.role="target" org.pintest.cve="CVE-2021-41773"
