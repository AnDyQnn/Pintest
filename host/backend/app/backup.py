"""backend.backup — полноценные бэкапы СОСТОЯНИЯ хоста (не кода).

Модель — как в каскадном шлюзе AnDyQnn/OpenWRT_AWG:
  • снимок всего durable-состояния: дамп таблиц БД (JSON) + артефакты на volume
    (reports/keys) + ИДЕНТИЧНОСТЬ VPN-сервера (data/vpn: server.json + awg0.conf +
    ключи — без него теряются все туннели) + TLS-серты (data/certs);
  • meta.json (версия/причина/дата/счётчики таблиц) + sha256 (целостность);
  • причина бэкапа: manual | cron | update | pre-restore;
  • перед восстановлением — «страховочная» pre-restore копия текущего состояния;
  • ежедневный авто-снимок (03:30) + ротация последних N;
  • скачать / загрузить (перенос на новый хост) / удалить / восстановить.

Restore возвращает состояние ПОБАЙТОВО поверх + перезаливает БД, затем просит
vpn-контейнер перечитать awg0 (иначе восстановленные ключи сервера не применятся).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import config, db, users, vpn

# сколько бэкапов держать (крошечные, но чистим, чтоб не копились)
KEEP = int(getattr(config, "BACKUP_KEEP", 0) or 10)

# durable-таблицы БД (все, что нужно восстановить один-в-один)
_TABLES = ["agents", "targets", "jobs", "chunks", "findings", "captures",
           "pivot_hosts", "admin_configs", "settings", "users"]

# каталоги состояния на volume (относительно DATA_DIR) — включаем в архив как есть
_STATE_DIRS = ["reports", "keys", "vpn", "certs"]

_REASONS = {"manual", "cron", "update", "pre-restore"}
_NAME_RE = re.compile(r"^backup_\d{8}_\d{6}_(manual|cron|update|pre-restore)\.tar\.gz$")


# --------------------------------------------------------------- helpers
def _dump_db() -> bytes:
    data = {t: db.all_(f"SELECT * FROM {t}") for t in _TABLES}
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


def _add_bytes(tar: tarfile.TarFile, arcname: str, raw: bytes) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(raw)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(raw))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    """Защита от path-traversal: только валидное имя бэкапа, без путей."""
    base = Path(name).name
    if not _NAME_RE.match(base):
        raise ValueError("недопустимое имя бэкапа")
    return base


def _reason_of(name: str) -> str:
    m = _NAME_RE.match(name)
    return m.group(1) if m else "manual"


# --------------------------------------------------------------- create
def create(reason: str = "manual", label: str = "") -> Dict:
    """Снимок текущего состояния. reason: manual|cron|update|pre-restore."""
    if reason not in _REASONS:
        reason = "manual"
    config.ensure_dirs()
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"backup_{ts}_{reason}.tar.gz"
    path = config.BACKUP_DIR / name

    counts = {}
    with tarfile.open(path, "w:gz") as tar:
        dbtxt = _dump_db()
        _add_bytes(tar, "db.json", dbtxt)
        try:
            counts = {t: len(v) for t, v in json.loads(dbtxt).items()}
        except ValueError:
            counts = {}
        for sub in _STATE_DIRS:
            p = config.DATA_DIR / sub
            if p.exists():
                # исключаем сами бэкапы, если BACKUP_DIR вдруг внутри DATA_DIR/…
                tar.add(p, arcname=sub, filter=lambda ti: None if "/backups/" in ("/" + ti.name + "/") else ti)
        meta = {
            "version": label or config.VERSION,
            "reason": reason,
            "created": datetime.now(timezone.utc).isoformat(),
            "ts": ts,
            "tables": counts,
        }
        _add_bytes(tar, "meta.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))

    # sha256 рядом + закрываем права (внутри приватные ключи VPN)
    digest = _sha256(path)
    (config.BACKUP_DIR / f"{name}.sha256").write_text(digest, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass

    _rotate()
    return {"name": name, "size": path.stat().st_size, "reason": reason,
            "sha256": digest, "created": meta["created"], "tables": counts}


def _rotate() -> None:
    files = sorted(config.BACKUP_DIR.glob("backup_*.tar.gz"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        old.unlink(missing_ok=True)
        (config.BACKUP_DIR / f"{old.name}.sha256").unlink(missing_ok=True)


# --------------------------------------------------------------- list / meta
def _read_meta(path: Path) -> Dict:
    try:
        with tarfile.open(path, "r:gz") as tar:
            m = tar.extractfile("meta.json")
            if m:
                return json.loads(m.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, ValueError, OSError):
        pass
    return {}


def list_backups() -> List[Dict]:
    out = []
    for f in sorted(config.BACKUP_DIR.glob("backup_*.tar.gz"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        meta = _read_meta(f)
        out.append({
            "name": f.name,
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
            "reason": meta.get("reason", _reason_of(f.name)),
            "version": meta.get("version", ""),
            "created": meta.get("created", ""),
            "verified": (config.BACKUP_DIR / f"{f.name}.sha256").exists(),
        })
    return out


def path_of(name: str) -> Path:
    p = config.BACKUP_DIR / _safe_name(name)
    if not p.exists():
        raise FileNotFoundError(name)
    return p


def delete(name: str) -> Dict:
    p = config.BACKUP_DIR / _safe_name(name)
    if not p.exists():
        raise FileNotFoundError(name)
    p.unlink(missing_ok=True)
    (config.BACKUP_DIR / f"{p.name}.sha256").unlink(missing_ok=True)
    return {"ok": True, "name": p.name}


# --------------------------------------------------------------- upload
def save_uploaded(raw: bytes, filename: str) -> Dict:
    """Принять внешний бэкап (перенос на новый хост). Валидируем структуру."""
    config.ensure_dirs()
    # проверяем, что это валидный наш архив (есть db.json и meta.json)
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            names = tar.getnames()
    except tarfile.TarError:
        raise ValueError("не удалось прочитать архив (ожидался .tar.gz)")
    if "db.json" not in names:
        raise ValueError("это не бэкап pintest (нет db.json)")
    meta = {}
    if "meta.json" in names:
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                meta = json.loads(tar.extractfile("meta.json").read().decode("utf-8"))
        except (tarfile.TarError, KeyError, ValueError):
            meta = {}
    # имя: сохраняем оригинальное валидное, иначе присваиваем uploaded-имя
    base = Path(filename).name if filename else ""
    if not _NAME_RE.match(base):
        reason = meta.get("reason", "manual")
        reason = reason if reason in _REASONS else "manual"
        base = f"backup_{time.strftime('%Y%m%d_%H%M%S')}_{reason}.tar.gz"
    path = config.BACKUP_DIR / base
    path.write_bytes(raw)
    digest = _sha256(path)
    (config.BACKUP_DIR / f"{base}.sha256").write_text(digest, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    _rotate()
    return {"name": base, "size": len(raw), "sha256": digest, "meta": meta}


# --------------------------------------------------------------- restore
def restore(name: str) -> Dict:
    path = config.BACKUP_DIR / _safe_name(name)
    if not path.exists():
        raise FileNotFoundError(name)

    # 1) целостность: sha256 (если есть сайдкар) — защита от битого/подменённого архива
    sc = config.BACKUP_DIR / f"{path.name}.sha256"
    if sc.exists():
        want = sc.read_text(encoding="utf-8").strip()
        if want and want != _sha256(path):
            raise ValueError("sha256 не совпал — архив повреждён")

    # 2) страховка: снимок ТЕКУЩЕГО состояния (можно откатить восстановление)
    safety = None
    try:
        safety = create(reason="pre-restore")["name"]
    except Exception:  # noqa: BLE001 — страховка не должна блокировать restore
        safety = None

    # 3) файлы состояния (reports/keys/vpn/certs) — поверх, побайтово
    with tarfile.open(path, "r:gz") as tar:
        db_member = tar.extractfile("db.json")
        data = json.loads(db_member.read().decode("utf-8")) if db_member else {}
        prefixes = tuple(f"{d}/" for d in _STATE_DIRS)
        for m in tar.getmembers():
            if m.name.startswith(prefixes):
                tar.extract(m, config.DATA_DIR)

    # 4) БД: очистить и залить снапшот
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

    # 5) не залочиться: гарантируем админа из .env (UPSERT)
    try:
        users.seed_admin()
    except Exception:  # noqa: BLE001
        pass

    # 6) применить восстановленную идентичность VPN-сервера (иначе ключи не активны)
    vpn_reloaded = False
    try:
        vpn.reload_server()
        vpn_reloaded = True
    except Exception:  # noqa: BLE001
        vpn_reloaded = False

    return {"name": path.name, "restored_rows": restored, "safety": safety,
            "vpn_reloaded": vpn_reloaded}


# --------------------------------------------------------------- daily cron
async def daily_loop() -> None:
    """Ежедневный авто-снимок в ~03:30 (как gateway-backup.timer). Идемпотентно."""
    from datetime import timedelta
    while True:
        now = datetime.now()
        nxt = now.replace(hour=3, minute=30, second=0, microsecond=0)
        if nxt <= now:
            nxt = nxt + timedelta(days=1)
        await asyncio.sleep(max(60, (nxt - now).total_seconds()))
        try:
            await asyncio.get_event_loop().run_in_executor(None, lambda: create(reason="cron"))
        except Exception:  # noqa: BLE001
            pass
