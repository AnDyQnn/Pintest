"""agent_api.updater — применение обновлений, присланных ХОСТОМ.

Механика обновления агентов принципиально отличается от механики хоста: агент
никогда не тянет код сам (у него нет доступа наружу и он должен уметь исчезнуть).
Хост доставляет новый код на агента (по SSH+API) и агент его разворачивает.

Здесь — приём бандла по API (tar.gz в base64), распаковка поверх /opt/pintest,
запись версии и мягкий рестарт API. По SSH хост доставляет тот же бандл скриптом
scripts/apply-update.sh (та же логика, другой транспорт).
"""
from __future__ import annotations

import base64
import io
import os
import signal
import tarfile
import time
from pathlib import Path
from typing import Dict

from . import config


def apply_bundle(b64: str, version: str = "") -> Dict:
    """Распаковать base64(tar.gz) поверх PINTEST_ROOT. Возвращает отчёт."""
    raw = base64.b64decode(b64)
    members = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        safe = []
        root = config.PINTEST_ROOT.resolve()
        for m in tar.getmembers():
            dest = (root / m.name).resolve()
            if not str(dest).startswith(str(root)):
                continue                      # защита от path traversal
            safe.append(m)
        tar.extractall(config.PINTEST_ROOT, members=safe)
        members = len(safe)
    if version:
        (config.STATE_DIR / "version").write_text(version, encoding="utf-8")
    return {"applied": True, "files": members, "version": version or config.VERSION}


def restart_soon(delay: float = 0.5) -> None:
    """Мягко перезапустить процесс API (entrypoint/uvicorn поднимет заново)."""
    def _kill():
        time.sleep(delay)
        os.kill(os.getpid(), signal.SIGTERM)
    import threading
    threading.Thread(target=_kill, daemon=True).start()
