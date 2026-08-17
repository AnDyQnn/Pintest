"""backend.diff — DIFF-режим: сравнение находок двух прогонов.

Показывает, что появилось (новые CVE/хосты), что ушло (закрыто) и что осталось —
удобно отслеживать динамику между сканами одной инфраструктуры.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from . import db


def _findings_set(job_id: str) -> Dict[Tuple[str, str], Dict]:
    rows = db.all_("SELECT host,cve,cvss,severity,family FROM findings WHERE job_id=%s", (job_id,))
    return {(r["host"], r["cve"]): r for r in rows}


def compare(job_a: str, job_b: str) -> Dict:
    """job_a — базовый (старый), job_b — новый. added = появилось в b."""
    A, B = _findings_set(job_a), _findings_set(job_b)
    ka, kb = set(A), set(B)
    added = [B[k] for k in sorted(kb - ka)]
    removed = [A[k] for k in sorted(ka - kb)]
    kept = [B[k] for k in sorted(ka & kb)]
    new_hosts = sorted({h for h, _ in kb} - {h for h, _ in ka})
    gone_hosts = sorted({h for h, _ in ka} - {h for h, _ in kb})
    return {
        "job_a": job_a, "job_b": job_b,
        "added": added, "removed": removed, "kept": kept,
        "counts": {"added": len(added), "removed": len(removed), "kept": len(kept)},
        "new_hosts": new_hosts, "gone_hosts": gone_hosts,
    }
