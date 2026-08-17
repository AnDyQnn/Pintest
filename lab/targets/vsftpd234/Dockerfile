FROM python:3.12-slim
COPY lab/targets/vsftpd234/server.py /server.py
RUN echo 'flag{vsftpd_2323_backdoor_pwned}' > /flag && chmod 644 /flag
EXPOSE 21 6200
CMD ["python3", "/server.py"]
LABEL org.pintest.role="target" org.pintest.cve="CVE-2011-2523"
