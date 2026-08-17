"""backend.db — тонкий слой над PostgreSQL (psycopg3).

Хранит durable-метаданные: агенты, джобы, чанки, находки, попытки эксплуатации,
бэкапы, настройки. Артефакты отчётов лежат на диске (volume), в БД — пути/статусы.
Схема создаётся на старте с ретраями (ждём готовности postgres-контейнера).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool: Optional[ConnectionPool] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    ssh_host    TEXT, ssh_port INT, ssh_user TEXT,
    tunnel_ip   TEXT,
    pubkey      TEXT,
    roles       JSONB DEFAULT '["scanner"]',
    status      TEXT DEFAULT 'provisioning',   -- provisioning|online|lost|destroyed
    last_seen   DOUBLE PRECISION DEFAULT 0,
    meta        JSONB DEFAULT '{}',
    created_at  DOUBLE PRECISION DEFAULT extract(epoch from now())
);
CREATE TABLE IF NOT EXISTS targets (
    id          SERIAL PRIMARY KEY,
    raw         TEXT,
    canonical   JSONB,        -- {targets:[...], v4n, v6n, notes}
    created_at  DOUBLE PRECISION DEFAULT extract(epoch from now())
);
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    opts        JSONB,
    mode        TEXT,         -- sequential|parallel
    status      TEXT DEFAULT 'pending',   -- pending|running|done|failed|cancelled
    total       INT DEFAULT 0,
    report_dir  TEXT,
    diff_against TEXT,
    stats       JSONB DEFAULT '{}',
    created_at  DOUBLE PRECISION DEFAULT extract(epoch from now()),
    finished_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS chunks (
    job_id      TEXT, chunk_id TEXT,
    targets     JSONB,
    status      TEXT DEFAULT 'pending',   -- pending|assigned|done|failed
    agent_id    TEXT,
    progress    JSONB DEFAULT '{}',
    PRIMARY KEY (job_id, chunk_id)
);
CREATE TABLE IF NOT EXISTS findings (
    id          SERIAL PRIMARY KEY,
    job_id      TEXT,
    host        TEXT, family TEXT,
    cve         TEXT, cvss DOUBLE PRECISION, severity TEXT,
    service     TEXT, port INT
);
CREATE TABLE IF NOT EXISTS captures (
    id          SERIAL PRIMARY KEY,
    ts          DOUBLE PRECISION DEFAULT extract(epoch from now()),
    agent_id    TEXT, target TEXT, cve TEXT, port INT,
    phase       TEXT,          -- check|capture
    success     BOOLEAN,
    flag        TEXT,
    data        JSONB DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value JSONB
);
"""


def init(retries: int = 30) -> None:
    global _pool
    last = None
    for _ in range(retries):
        try:
            _pool = ConnectionPool(config.DB_DSN, min_size=1, max_size=8, timeout=10)
            with _pool.connection() as conn:
                conn.execute(SCHEMA)
            return
        except Exception as e:  # noqa: BLE001 — ждём готовности postgres
            last = e
            time.sleep(2)
    raise RuntimeError(f"postgres недоступен: {last}")


def q(sql: str, params: tuple = (), fetch: str = "none") -> Any:
    """fetch: none|one|all. Возвращает dict-строки."""
    assert _pool is not None, "db.init() не вызван"
    with _pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return None


def one(sql: str, params: tuple = ()) -> Optional[Dict]:
    return q(sql, params, fetch="one")


def all_(sql: str, params: tuple = ()) -> List[Dict]:
    return q(sql, params, fetch="all") or []


def js(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)
