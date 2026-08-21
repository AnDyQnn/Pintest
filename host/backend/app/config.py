"""backend.config — настройки control plane, всё через ENV (переносимость лаба->реал)."""
from __future__ import annotations

import os
from pathlib import Path

# БД (postgres-контейнер)
DB_DSN = os.environ.get(
    "DB_DSN", "postgresql://pintest:pintest@postgres:5432/pintest")

# Данные НА ДИСКЕ через volume (отчёты/лут/ключи/бэкапы) — не внутри контейнера
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
REPORTS_DIR = DATA_DIR / "reports"      # сводные отчёты по джобам (md/html/csv/json)
BACKUP_DIR = DATA_DIR / "backups"
KEYS_DIR = DATA_DIR / "keys"            # приватные ключи агентов, что вбрасывал хост

# VPN
VPN_API = os.environ.get("VPN_API", "http://vpn:8080")   # control API vpn-контейнера
AWG_ENDPOINT = os.environ.get("AWG_ENDPOINT", "")        # <host_ip>:51820 для клиент-конфигов
HOST_TUNNEL_IP = os.environ.get("HOST_TUNNEL_IP", "10.9.0.1")
TUNNEL_NET = os.environ.get("TUNNEL_NET", "10.9.0.0/24")

# Агенты
AGENT_API_PORT = int(os.environ.get("AGENT_API_PORT", "9101"))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "3"))   # опрос /health
HEARTBEAT_MISS = int(os.environ.get("HEARTBEAT_MISS", "4"))          # промахов до «потерян»

# Оркестрация
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "4"))    # целей в чанке (мелко для наглядности лабы)
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "2"))  # опрос статуса чанка
LIVE_INTERVAL = float(os.environ.get("LIVE_INTERVAL", "1.5"))  # период WS-обновления вебки (сек)

# Доступ к вебке (через админский VPN-конфиг)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me-in-real-lab")

VERSION = os.environ.get("PINTEST_VERSION", "0.7.0")
HOST_REPO_DIR = os.environ.get("HOST_REPO_DIR", "/opt/pintest")  # для git-обновления хоста


def ensure_dirs() -> None:
    for d in (DATA_DIR, REPORTS_DIR, BACKUP_DIR, KEYS_DIR):
        d.mkdir(parents=True, exist_ok=True)
