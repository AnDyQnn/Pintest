"""agent_api.config — пути и настройки агента, всё через ENV (переносимость).

Никаких «лабовых» веток и захардкоженных адресов: одни и те же значения работают
и в докер-лабе, и на реальном сервере — меняются только переменные окружения.
"""
from __future__ import annotations

import os
from pathlib import Path

# Корень проекта внутри контейнера/сервера (сюда же кладётся движок core/)
PINTEST_ROOT = Path(os.environ.get("PINTEST_ROOT", "/opt/pintest"))
AGENT_DIR = PINTEST_ROOT / "agent"
STATE_DIR = Path(os.environ.get("AGENT_STATE_DIR", str(AGENT_DIR / "state")))
JOBS_DIR = STATE_DIR / "jobs"
SCRIPTS_DIR = AGENT_DIR / "scripts"

# Сеть/туннель
API_HOST = os.environ.get("AGENT_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("AGENT_API_PORT", "9101"))
HOST_TUNNEL_IP = os.environ.get("HOST_TUNNEL_IP", "10.9.0.1")  # адрес хоста внутри AWG
AWG_IFACE = os.environ.get("AWG_IFACE", "awg0")

# Dead-man switch: сколько секунд без связи с хостом до самоуничтожения.
# ВАЖНО: таймаут должен быть ЗАМЕТНО больше PersistentKeepalive (25с) — иначе на реальном
# интернете (NAT/джиттер) обычная пауза keepalive-цикла ложно триггерит самоуничтожение.
DEADMAN_ENABLED = os.environ.get("DEADMAN_ENABLED", "1") == "1"
DEADMAN_TIMEOUT = int(os.environ.get("DEADMAN_TIMEOUT", "90"))   # >3× keepalive
DEADMAN_INTERVAL = int(os.environ.get("DEADMAN_INTERVAL", "5"))
# ВЗВОД (arming): dead-man взводится ИДЕМПОТЕНТНО только после того, как нода
# установлена И получила УСТОЙЧИВУЮ связь с хостом — непрерывную reachability в течение
# ARM_GRACE секунд. Пока связь не подтверждена столько времени — нода НЕ вооружена и
# себя не тронет (свежая/полу-настроенная нода не нукается зря).
DEADMAN_ARM_GRACE = int(os.environ.get("DEADMAN_ARM_GRACE", "15"))
# Порог «свежести» AmneziaWG-handshake для reachability. Больше таймаута и заведомо
# больше интервала рехендшейка WireGuard (~120с), чтобы рабочий-но-простаивающий туннель
# считался живым даже при фильтрации ICMP (иначе ложное самоуничтожение).
DEADMAN_HANDSHAKE_MAX = int(os.environ.get("DEADMAN_HANDSHAKE_MAX", "150"))
# Проверка при СТАРТЕ уже-взведённой ноды: если при запуске хост не виден за это число
# секунд — самоуничтожение (сценарий «отрубили сеть → выключили → включили»).
DEADMAN_BOOT_GRACE = int(os.environ.get("DEADMAN_BOOT_GRACE", "30"))

# Файлы-маркеры состояния.
# ARMED_FLAG — в ФС контейнера (не на volume!): переживает stop/start той же ноды
# (сценарий «выключили/включили» -> быстрая чистка хвостов), но сбрасывается при
# пересоздании контейнера (полный редеплой -> нода ждёт нового provision, не нукается зря).
ARMED_FLAG = AGENT_DIR / ".armed"       # взведён ли dead-man (ставится после provision)
TOMBSTONE = PINTEST_ROOT / "DESTROYED"  # маркер после самоуничтожения

VERSION = os.environ.get("PINTEST_VERSION", "0.7.0")
AGENT_NAME = os.environ.get("AGENT_NAME", os.environ.get("HOSTNAME", "agent"))


def ensure_dirs() -> None:
    for d in (STATE_DIR, JOBS_DIR):
        d.mkdir(parents=True, exist_ok=True)
