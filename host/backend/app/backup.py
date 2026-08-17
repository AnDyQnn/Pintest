"""backend.backup — бэкапы состояния хоста (на диск-volume + ротация).

В бэкап входят: дамп таблиц БД (JSON) + артефакты отчётов + ключи агентов. Всё это
и так лежит на volume, но бэкап собирает единый архив со снапшотом и датой, плюс
restore. Ротация хранит последние N архивов.
"""
from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Dict, List

from . import config, db

KEEP = 10
_TABLES = ["agents", "targets", "jobs", "chunks", "findings", "captures", "settings"]


def _dump_db() -> bytes:
    data = {t: db.all_(f"SELECT * FROM {t}") for t in _TABLES}
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


def create() -> Dict:
    config.ensure_dirs()
    name = f"backup_{time.strftime('%Y%m%d_%H%M%S')}.tar.gz"
    path = config.BACKUP_DIR / name
    with tarfile.open(path, "w:gz") as tar:
        dbtxt = _dump_db()
        info = tarfile.TarInfo("db.json")
        info.size = len(dbtxt)
        tar.addfile(info, io.BytesIO(dbtxt))
        if config.REPORTS_DIR.exists():
            tar.add(config.REPORTS_DIR, arcname="reports")
        if config.KEYS_DIR.exists():
            tar.add(config.KEYS_DIR, arcname="keys")
    _rotate()
    return {"name": name, "size": path.stat().st_size}


def _rotate():
    files = sorted(config.BACKUP_DIR.glob("backup_*.tar.gz"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        old.unlink(missing_ok=True)


def list_backups() -> List[Dict]:
    files = sorted(config.BACKUP_DIR.glob("backup_*.tar.gz"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime} for f in files]


def restore(name: str) -> Dict:
    path = config.BACKUP_DIR / name
    if not path.exists():
        raise FileNotFoundError(name)
    with tarfile.open(path, "r:gz") as tar:
        db_member = tar.extractfile("db.json")
        data = json.loads(db_member.read().decode("utf-8")) if db_member else {}
        # восстановить артефакты
        for m in tar.getmembers():
            if m.name.startswith(("reports/", "keys/")):
                tar.extract(m, config.DATA_DIR)
    # восстановить БД (грубо: очистить и залить снапшот)
    restored = 0
    for t in _TABLES:
        rows = data.get(t, [])
        if not rows:
            continue
        db.q(f"DELETE FROM {t}")
        cols = list(rows[0].keys())
        ph = ",".join(["%s"] * len(cols))
        for r in rows:
            vals = [db.js(r[c]) if isinstance(r[c], (dict, list)) else r[c] for c in cols]
            try:
                db.q(f"INSERT INTO {t}({','.join(cols)}) VALUES({ph})", tuple(vals))
                restored += 1
            except Exception:  # noqa: BLE001
                pass
    return {"restored_rows": restored, "name": name}
