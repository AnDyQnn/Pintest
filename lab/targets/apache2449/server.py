#!/usr/bin/env python3
"""Учебная цель: эмуляция Apache httpd 2.4.49 (CVE-2021-41773).

Баннер 'Server: Apache/2.4.49 (Unix)' — nmap -sV распознаёт версию, vulners мапит CVE.
Path traversal через /cgi-bin/.%2e/... читает файлы (GET) и выполняет команды (POST) —
как настоящая уязвимость. Только для стенда.
"""
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


class H(BaseHTTPRequestHandler):
    def version_string(self):
        return "Apache/2.4.49 (Unix)"

    def _resolved(self):
        dec = unquote(self.path)
        if "cgi-bin" in self.path and "%2e" in self.path:
            tail = dec.split("cgi-bin/", 1)[1]
            return os.path.normpath("/" + tail)   # ".." схлопывается -> /etc/passwd, /bin/sh
        return None

    def do_GET(self):
        target = self._resolved()
        if target and os.path.isfile(target):      # traversal: чтение файла (read-only)
            try:
                data = open(target, "rb").read()
            except OSError:
                data = b""
            self.send_response(200)
            self.send_header("Server", self.version_string())
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Server", self.version_string())
        self.end_headers()
        self.wfile.write(b"<html><body>It works! (Apache/2.4.49)</body></html>")

    def do_POST(self):
        target = self._resolved()
        ln = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(ln).decode("utf-8", "ignore")
        if target and target.endswith("sh") and "|" in body:   # RCE через /bin/sh
            cmd = body.split("|", 1)[1]
            try:
                out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, timeout=10).stdout
            except Exception as e:  # noqa: BLE001
                out = str(e).encode()
            self.send_response(200)
            self.send_header("Server", self.version_string())
            self.end_headers()
            self.wfile.write(out)
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), H).serve_forever()
