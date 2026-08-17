#!/usr/bin/env python3
"""Учебная цель: эмуляция vsftpd 2.3.4 с бэкдором (CVE-2011-2523).

Отдаёт баннер '220 (vsFTPd 2.3.4)' — nmap -sV распознаёт версию, vulners мапит CVE.
Логин с ':)' открывает «рут-шелл» на TCP/6200 (как настоящий бэкдор). Только для стенда.
"""
import socket
import subprocess
import threading

FLAG_PATHS = ("/flag", "/root/flag")


def backdoor():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 6200))
    srv.listen(5)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=_shell, args=(c,), daemon=True).start()


def _shell(c):
    c.sendall(b"")  # немой рут-шелл, как в оригинале
    buf = b""
    c.settimeout(30)
    try:
        while True:
            data = c.recv(1024)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                cmd = line.decode("utf-8", "ignore").strip()
                if not cmd:
                    continue
                try:
                    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True,
                                         timeout=10).stdout
                except Exception as e:  # noqa: BLE001
                    out = str(e).encode()
                c.sendall(out)
    except (socket.timeout, OSError):
        pass
    finally:
        c.close()


def ftp():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 21))
    srv.listen(10)
    started = {"bd": False}
    while True:
        c, _ = srv.accept()
        threading.Thread(target=_ftp_sess, args=(c, started), daemon=True).start()


def _ftp_sess(c, started):
    c.settimeout(20)
    triggered = {"v": False}
    try:
        c.sendall(b"220 (vsFTPd 2.3.4)\r\n")
        buf = b""
        while True:
            data = c.recv(1024)
            if not data:
                break
            buf += data
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                s = line.decode("utf-8", "ignore")
                cmd = s.upper()
                if cmd.startswith("USER"):
                    if ":)" in s:
                        triggered["v"] = True
                    c.sendall(b"331 Please specify the password.\r\n")
                elif cmd.startswith("PASS"):
                    if triggered["v"] and not started["bd"]:
                        started["bd"] = True   # бэкдор уже слушает (поднят при старте)
                    c.sendall(b"230 Login successful.\r\n")
                elif cmd.startswith("QUIT"):
                    c.sendall(b"221 Goodbye.\r\n")
                    return
                else:
                    c.sendall(b"530 Please login with USER and PASS.\r\n")
    except (socket.timeout, OSError):
        pass
    finally:
        c.close()


if __name__ == "__main__":
    threading.Thread(target=backdoor, daemon=True).start()
    ftp()
