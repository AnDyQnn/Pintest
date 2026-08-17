"""
core.reporting — сборка СВОДНОГО отчёта на хосте.

Оркестратор сливает results.jsonl от всех агентов в один мастер-каталог и после
каждого влитого чанка пересобирает отчёт (md/html/csv/json). Это ровно тот механизм
«отчёт растёт, а не собирается с нуля», что уже есть в движке для --resume:
переиспользуем auditor._load_results / auditor.build_reports / auditor._merge.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable

from . import auditor


def load_results(outdir: str | Path) -> dict:
    """Прочитать накопленные результаты (results.jsonl) из мастер-каталога."""
    return auditor._load_results(Path(outdir))


def merge_result_lines(acc: dict, lines: Iterable[str], outdir: str | Path) -> int:
    """Влить строки results.jsonl (host-словари) в acc и дописать в мастер-файл.
    Возвращает число реально влитых записей. Идемпотентно на уровне хостов (_merge)."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    appended = []
    n = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        d["ports"] = [tuple(x) for x in d.get("ports", [])]
        d["up"] = True
        auditor._merge(acc, d)
        appended.append(auditor._dump_host(d))
        n += 1
    if appended:
        auditor._append_lines(outdir / "results.jsonl", appended)
    return n


def mark_hosts_done(outdir: str | Path, ips: Iterable[str]) -> None:
    """Отметить хосты как просканированные (svc_done.txt) — как в движке для resume."""
    auditor._append_lines(Path(outdir) / "svc_done.txt", list(ips))


def rebuild(outdir: str | Path, meta: Dict, acc: dict) -> None:
    """Пересобрать все 4 формата отчёта из текущего acc (безопасно — гасит сбои записи)."""
    auditor._safe_build(Path(outdir), meta, acc)


def findings(acc: dict) -> list:
    """Плоский список находок (family, host, cve, cvss, severity) — для DIFF/статистики."""
    return auditor._findings(acc)
