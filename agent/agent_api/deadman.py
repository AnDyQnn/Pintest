"""agent_api.deadman — «выключатель мертвеца» (устойчивый к реальному интернету).

Взводится ИДЕМПОТЕНТНО только когда нода УСТАНОВЛЕНА и получила УСТОЙЧИВУЮ связь с
хостом (непрерывная reachability в течение DEADMAN_ARM_GRACE). Пока связь не
подтверждена столько времени — нода НЕ вооружена и себя не трогает (свежая/полу-
настроенная нода не нукается зря). После взвода: если связь с хостом пропала дольше
DEADMAN_TIMEOUT (заметно больше keepalive=25с, чтобы NAT/джиттер не давал ложняк) —
проект на ноде немедленно и полностью самоуничтожается.

Reachability = ICMP-ping хоста ИЛИ свежий AmneziaWG-handshake (данные шли недавно) —
чтобы фильтрация ICMP при живом туннеле не приводила к ложному самоуничтожению.
"""
from __future__ import annotations

import subprocess
import threading
import time

from . import config

_state = {"armed": False, "last_ok": 0.0, "reachable": False, "triggered": False, "arm_streak": 0.0}


def status() -> dict:
    return {
        "enabled": config.DEADMAN_ENABLED,
        "armed": config.ARMED_FLAG.exists(),
        "reachable": _state["reachable"],
        "timeout": config.DEADMAN_TIMEOUT,
        "arm_grace": config.DEADMAN_ARM_GRACE,
        "since_ok": round(time.time() - _state["last_ok"], 1) if _state["last_ok"] else None,
    }


def _ping_ok() -> bool:
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "2", config.HOST_TUNNEL_IP],
                           capture_output=True, timeout=4)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _handshake_fresh() -> bool:
    """Свежий ли рукопожатие туннеля (данные шли недавно) — надёжнее ICMP при живом туннеле."""
    try:
        out = subprocess.run(["awg", "show", config.AWG_IFACE, "latest-handshakes"],
                             capture_output=True, text=True, timeout=4).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                if time.time() - int(parts[1]) < config.DEADMAN_HANDSHAKE_MAX:
                    return True
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return False


def _reachable() -> bool:
    return _ping_ok() or _handshake_fresh()


def self_destruct(reason: str = "dead-man") -> None:
    """Запустить необратимое самоуничтожение проекта на этом агенте."""
    if _state["triggered"]:
        return
    _state["triggered"] = True
    script = config.SCRIPTS_DIR / "self-destruct.sh"
    try:
        subprocess.Popen(["bash", str(script), reason], start_new_session=True)
    except OSError:
        subprocess.run(["awg-quick", "down", config.AWG_IFACE], capture_output=True)


def _boot_check() -> bool:
    """Старт УЖЕ-взведённой ноды: даём DEADMAN_BOOT_GRACE секунд на реконнект. Если хост
    так и не виден — это «изолировали и перезапустили», чистим хвосты. Возвращает False,
    если сработало самоуничтожение (цикл дальше не идёт)."""
    if not config.ARMED_FLAG.exists():
        return True                       # ещё не взведена — обычный цикл (фаза взвода)
    for _ in range(max(1, config.DEADMAN_BOOT_GRACE)):
        if _reachable():
            _state["last_ok"] = time.time()
            return True
        time.sleep(1)
    if config.DEADMAN_ENABLED:
        self_destruct("перезапуск без связи с хостом (чистка хвостов)")
        return False
    return True


def _loop() -> None:
    if not _boot_check():
        return
    while True:
        armed = config.ARMED_FLAG.exists()
        ok = _reachable()
        _state["reachable"] = ok
        now = time.time()
        if ok:
            _state["last_ok"] = now

        if not armed:
            # ── ФАЗА ВЗВОДА: копим НЕПРЕРЫВНУЮ связь, взводим идемпотентно ──
            if ok:
                _state["arm_streak"] += config.DEADMAN_INTERVAL
                if _state["arm_streak"] >= config.DEADMAN_ARM_GRACE:
                    try:
                        config.ARMED_FLAG.parent.mkdir(parents=True, exist_ok=True)
                        config.ARMED_FLAG.touch()          # взвели (идемпотентно)
                    except OSError:
                        pass
                    _state["armed"] = True
            else:
                _state["arm_streak"] = 0.0                 # обрыв до взвода — счётчик с нуля
        else:
            # ── ФАЗА МОНИТОРИНГА: нукаем только при УСТОЙЧИВОЙ потере связи ──
            _state["armed"] = True
            if (config.DEADMAN_ENABLED and not ok and _state["last_ok"]
                    and now - _state["last_ok"] >= config.DEADMAN_TIMEOUT):
                self_destruct("потеря связи с хостом")
                return
        time.sleep(config.DEADMAN_INTERVAL)


def start() -> None:
    threading.Thread(target=_loop, daemon=True).start()
