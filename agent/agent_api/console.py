"""agent_api.console — интерактивные shell-сессии на агенте через настоящий PTY.

Каждая сессия — отдельный процесс `bash` под псевдотерминалом (pty). Хост шлёт
СЫРЫЕ байты клавиш (стрелки, Ctrl-C, Tab-дополнение — всё как в обычном терминале),
bash сам их обрабатывает. Вывод копится в кольцевой буфер, вебка забирает его по
смещению (long-poll GET). Это даёт настоящую линуксовую консоль в браузере без
внешних библиотек.

Сессии живут в памяти агента; при рестарте агента — сбрасываются. Лимит на буфер,
чтобы длинный вывод не съел память. Только для авторизованного лаб/CTF-доступа.
"""
from __future__ import annotations

import errno
import fcntl
import os
import pty
import signal
import struct
import termios
import threading
import time
import uuid
from typing import Dict, Optional

_MAXBUF = 256 * 1024        # держим последние 256 КБ вывода на сессию
_IDLE_TTL = 1800            # 30 мин без активности → сессия закрывается


class _Session:
    def __init__(self, cols: int, rows: int, shell: str = "bash"):
        self.id = uuid.uuid4().hex[:12]
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.alive = True
        self.last = time.time()
        pid, fd = pty.fork()
        if pid == 0:                      # ── дочерний процесс: сам shell ──
            os.environ["TERM"] = "xterm-256color"
            os.environ["PS1"] = r"\u@\h:\w\$ "
            try:
                os.execvp(shell, [shell])
            except Exception:
                os.execvp("sh", ["sh"])
        self.pid = pid
        self.fd = fd
        self._winsize(cols, rows)
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        threading.Thread(target=self._reader, daemon=True).start()

    def _winsize(self, cols: int, rows: int) -> None:
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def _reader(self) -> None:
        while self.alive:
            try:
                data = os.read(self.fd, 4096)
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    time.sleep(0.03)
                    continue
                break                     # EIO — shell завершился
            if not data:
                break
            with self.lock:
                self.buf.extend(data)
                if len(self.buf) > _MAXBUF:
                    del self.buf[:len(self.buf) - _MAXBUF]
        self.alive = False

    def write(self, data: bytes) -> None:
        self.last = time.time()
        try:
            os.write(self.fd, data)
        except OSError:
            self.alive = False

    def read(self, since: int) -> Dict:
        with self.lock:
            total = len(self.buf)
            since = max(0, min(since, total))
            chunk = bytes(self.buf[since:])
        self.last = time.time()
        return {"data": chunk.decode("utf-8", "replace"), "offset": total, "alive": self.alive}

    def resize(self, cols: int, rows: int) -> None:
        self._winsize(cols, rows)

    def close(self) -> None:
        self.alive = False
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


_SESSIONS: Dict[str, _Session] = {}
_GC_LOCK = threading.Lock()


def _gc() -> None:
    now = time.time()
    with _GC_LOCK:
        for sid, s in list(_SESSIONS.items()):
            if not s.alive or now - s.last > _IDLE_TTL:
                s.close()
                _SESSIONS.pop(sid, None)


def open_session(cols: int = 120, rows: int = 30) -> Dict:
    _gc()
    s = _Session(cols, rows)
    _SESSIONS[s.id] = s
    return {"sid": s.id}


def write_session(sid: str, data: str) -> Dict:
    s = _SESSIONS.get(sid)
    if not s or not s.alive:
        return {"ok": False, "alive": False}
    s.write(data.encode("utf-8", "replace"))
    return {"ok": True, "alive": s.alive}


def read_session(sid: str, since: int = 0) -> Dict:
    s = _SESSIONS.get(sid)
    if not s:
        return {"data": "", "offset": since, "alive": False, "gone": True}
    return s.read(since)


def resize_session(sid: str, cols: int, rows: int) -> Dict:
    s = _SESSIONS.get(sid)
    if s:
        s.resize(cols, rows)
    return {"ok": bool(s)}


def close_session(sid: str) -> Dict:
    s = _SESSIONS.pop(sid, None)
    if s:
        s.close()
    return {"ok": True}


def list_sessions() -> list:
    _gc()
    return [{"sid": s.id, "alive": s.alive, "idle": int(time.time() - s.last)}
            for s in _SESSIONS.values()]
