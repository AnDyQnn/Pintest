"""backend.targets — приём «грязного» списка целей и хранение канон-версии.

Список из UI прогоняется движком ОДИН раз (core.targets.canonical): валидация,
разворачивание CIDR/диапазонов, дедупликация, отсев мусора. Канон хранится в БД и
используется оркестратором для нарезки на чанки.
"""
from __future__ import annotations

from typing import Dict, Optional

from core import targets as core_targets

from . import db


def ingest(raw: str) -> Dict:
    canon = core_targets.canonical(raw)
    db.q("INSERT INTO targets(raw, canonical) VALUES(%s,%s)", (raw, db.js(canon)))
    return canon


def latest() -> Optional[Dict]:
    row = db.one("SELECT canonical FROM targets ORDER BY id DESC LIMIT 1")
    return row["canonical"] if row else None
