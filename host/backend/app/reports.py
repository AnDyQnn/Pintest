"""backend.reports — доступ к сводным отчётам джоб (артефакты на диске-volume)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import config, db


def job_dir(job_id: str) -> Path:
    return config.REPORTS_DIR / job_id


def artifact(job_id: str, name: str) -> Optional[Path]:
    allowed = {"report.md", "report.html", "findings.csv", "findings.json"}
    if name not in allowed:
        return None
    p = job_dir(job_id) / name
    return p if p.exists() else None


def list_jobs() -> List[Dict]:
    return db.all_("SELECT id,opts,mode,status,total,stats,diff_against,created_at,finished_at "
                   "FROM jobs ORDER BY created_at DESC")


def job(job_id: str) -> Optional[Dict]:
    j = db.one("SELECT * FROM jobs WHERE id=%s", (job_id,))
    if not j:
        return None
    j["chunks"] = db.all_("SELECT chunk_id,status,agent_id,progress FROM chunks "
                          "WHERE job_id=%s ORDER BY chunk_id", (job_id,))
    j["findings_count"] = db.one("SELECT count(*) c FROM findings WHERE job_id=%s", (job_id,))["c"]
    return j
