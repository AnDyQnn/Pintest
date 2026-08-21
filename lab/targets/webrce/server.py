#!/usr/bin/env python3
"""Учебная веб-мишень, симулирующая RCE под конкретный CVE (задаётся через SIM_CVE).

Одна и та же реализация изображает разные «устройства»/уязвимости — под каждый CVE
свой способ доставки команды (как в реальном эксплойте), но исполнение общее: извлекаем
команду из запроса и запускаем /bin/sh. БАННЕР (Server) задаётся SIM_BANNER, чтобы
nmap/vulners видели «уязвимую версию». Только для стенда.

Поддержанные SIM_CVE:
  struts   (CVE-2017-5638)  — OGNL в заголовке Content-Type: ...#cmd='CMD'...
  phpcgi   (CVE-2012-1823)  — GET ?-d... + тело '<?php system("CMD"); ?>'
  drupal   (CVE-2018-7600)  — POST с 'mail[#markup]=CMD'
  tomcat   (CVE-2017-12617) — PUT /x.jsp с '<% ... exec("CMD") ... %>', затем GET /x.jsp
"""
import os
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

CVE = os.environ.get("SIM_CVE", "struts")
BANNER = os.environ.get("SIM_BANNER", "GenericWeb/1.0")
_STORE = {}


def run(cmd):
    try:
        return subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, timeout=8).stdout
    except Exception as e:  # noqa: BLE001
        return str(e).encode()


class H(BaseHTTPRequestHandler):
    def version_string(self):
        return BANNER

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n).decode("utf-8", "ignore") if n else ""

    def _cmd(self, body):
        if CVE == "struts":
            m = re.search(r"#cmd='([^']*)'", self.headers.get("Content-Type", ""))
            return m.group(1) if m else None
        if CVE == "phpcgi":
            if "-d" in self.path or "allow_url_include" in self.path:
                m = re.search(r"system\(['\"]?(.*?)['\"]?\)", body)
                return m.group(1) if m else None
        if CVE == "drupal":
            m = re.search(r"mail\[#markup\]=([^&]*)", body)
            return urllib.parse.unquote_plus(m.group(1)) if m else None
        if CVE == "tomcat":
            m = re.search(r'exec\(["\']?(.*?)["\']?\)', body)
            return m.group(1) if m else None
        return None

    def _respond(self, out):
        self.send_response(200)
        self.send_header("Server", BANNER)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(out or b"<html>web app</html>")

    def do_GET(self):
        # tomcat: GET уже загруженного «jsp» исполняет сохранённую команду
        if CVE == "tomcat" and self.path in _STORE:
            self._respond(run(_STORE[self.path]))
            return
        self._respond(run(self._cmd("")) if self._cmd("") else b"")

    def do_POST(self):
        body = self._body()
        self._respond(run(self._cmd(body)) if self._cmd(body) else b"")

    def do_PUT(self):
        body = self._body()
        cmd = self._cmd(body)
        if CVE == "tomcat" and cmd:
            _STORE[self.path.rstrip("/")] = cmd    # «залили shell.jsp» — исполнится при GET
        self.send_response(201)
        self.send_header("Server", BANNER)
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 80), H).serve_forever()
