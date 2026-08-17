"""
core.targets — чистка и валидация «грязного» списка целей.

Хост принимает список из UI в произвольном виде (адреса, CIDR, диапазоны, мусор,
комментарии, дубликаты), прогоняет через движок ОДИН раз и хранит канон-версию.
Переиспользует парсер из движка (core.auditor.parse_targets) — единая логика с CLI.
"""
from __future__ import annotations

import os
import tempfile
from typing import List, Tuple

from . import auditor


def clean_text(raw: str) -> Tuple[List[str], List[str], List[str]]:
    """Грязный текст -> (v4, v6, notes). Пишет во временный файл и зовёт движок."""
    tf = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8")
    try:
        tf.write(raw if raw.endswith("\n") else raw + "\n")
        tf.close()
        return auditor.parse_targets(tf.name)
    finally:
        try:
            os.unlink(tf.name)
        except OSError:
            pass


def canonical(raw: str) -> dict:
    """Свести грязный список к канону + статистика для UI/оркестратора."""
    v4, v6, notes = clean_text(raw)
    ordered = list(v4) + list(v6)          # v4 затем v6, дубликаты уже убраны множествами
    return {
        "targets": ordered,
        "v4": v4,
        "v6": v6,
        "count": len(ordered),
        "v4n": len(v4),
        "v6n": len(v6),
        "notes": notes,                    # что пропущено/схлопнуто — показываем в UI
    }
