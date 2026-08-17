#!/usr/bin/env python3
"""Учебная цель: старый SSH-баннер (находка есть, модуля эксплуатации нет).

Показывает случай «CVE найден, но автоматического модуля закрепления под него нет» —
в UI такая находка видна, но кнопки закрепления по ней не будет. Только баннер, без сервиса.
"""
import socket
import threading

BANNER = b"SSH-2.0-OpenSSH_7.2p2 Ubuntu-4ubuntu2.1\r\n"


def sess(c):
    try:
        c.sendall(BANNER)
        c.settimeout(5)
        c.recv(1024)
    except OSError:
        pass
    finally:
        c.close()


if __name__ == "__main__":
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 22))
    srv.listen(10)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=sess, args=(c,), daemon=True).start()
