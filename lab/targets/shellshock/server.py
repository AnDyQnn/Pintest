#!/usr/bin/env python3
"""Учебная цель: CGI, уязвимый к Shellshock (CVE-2014-6271).

Заголовки запроса становятся окружением CGI; уязвимый bash выполняет код после
определения функции '() { :;};'. Реагирует и на nmap http-shellshock, и на наш модуль.
Только для стенда.
"""
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CGI = "/cgi-bin/status"
SHELLSHOCK = re.compile(r"\(\)\s*\{\s*:;\s*\};(.*)", re.S)


class H(BaseHTTPRequestHandler):
    def version_string(self):
        return "Apache/2.4.7 (Ubuntu)"

    def _handle(self):
        if not self.path.startswith(CGI):
            self.send_response(404)
            self.send_header("Server", self.version_string())
            self.end_headers()
            return
        # ищем shellshock-паттерн в любом заголовке (как в CGI-окружении)
        injected = None
        for _, val in self.headers.items():
            m = SHELLSHOCK.search(val or "")
            if m:
                injected = m.group(1).strip()
                break
        self.send_response(200)
        self.send_header("Server", self.version_string())
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        if injected:
            try:
                out = subprocess.run(["/bin/bash", "-c", injected],
                                     capture_output=True, timeout=10).stdout
            except Exception as e:  # noqa: BLE001
                out = str(e).encode()
            self.wfile.write(out)
        else:
            self.wfile.write(b"status: ok\n")

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), H).serve_forever()
