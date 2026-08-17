"""agent_api.deadman — «выключатель мертвеца».

Если агент был подключён (провижнен), но потерял связь с хостом по туннелю дольше
DEADMAN_TIMEOUT секунд (упал интернет, оборвался туннель, ноду изолировали) — всё,
что относится к проекту, немедленно самоуничтожается: туннель вниз, стирание кода,
результатов, ключей и конфигов. Это требование ТЗ: «незамедлительно и полностью».

Взводится только ПОСЛЕ первого успешного provision (ARMED_FLAG) — чтобы свежий,
ещё не настроенный агент не удалил себя, пока хоста ожидаемо не видно.
"""
from __future__ import annotations

import subprocess
import threading
import time

from . import config

_state = {"armed": False, "last_ok": 0.0, "reachable": False, "triggered": False}


def status() -> dict:
    return {
        "enabled": config.DEADMAN_ENABLED,
        "armed": config.ARMED_FLAG.exists(),
        "reachable": _state["reachable"],
        "timeout": config.DEADMAN_TIMEOUT,
        "since_ok": round(time.time() - _state["last_ok"], 1) if _state["last_ok"] else None,
    }


def _host_reachable() -> bool:
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "2", config.HOST_TUNNEL_IP],
            capture_output=True, timeout=4,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def self_destruct(reason: str = "dead-man") -> None:
    """Запустить необратимое самоуничтожение проекта на этом агенте."""
    if _state["triggered"]:
        return
    _state["triggered"] = True
    script = config.SCRIPTS_DIR / "self-destruct.sh"
    try:
        subprocess.Popen(["bash", str(script), reason], start_new_session=True)
    except OSError:
        # если даже скрипта нет — гасим туннель напрямую и оставляем tombstone
        subprocess.run(["awg-quick", "down", config.AWG_IFACE], capture_output=True)


def _boot_check() -> bool:
    """Проверка при старте. Если нода уже была провижнена (armed), но при запуске хост
    недоступен за DEADMAN_BOOT_GRACE секунд — считаем, что ноду изолировали и перезапустили:
    немедленно чистим хвосты. Возвращает True, если можно продолжать нормальный мониторинг."""
    if not config.ARMED_FLAG.exists():
        return True                       # ещё не провижнена — ждём provision, не трогаем
    for _ in range(max(1, config.DEADMAN_BOOT_GRACE)):
        if _host_reachable():
            _state["last_ok"] = time.time()
            return True
        time.sleep(1)
    if config.DEADMAN_ENABLED:
        self_destruct("перезапуск без связи с хостом (чистка хвостов)")
        return False
    return True


def _loop() -> None:
    # (1) быстрая проверка на старте — сценарий «отрубили сеть, выключили, включили»
    if not _boot_check():
        return
    # (2) постоянный мониторинг — сценарий «заблокировали сеть/порты во время работы»
    while True:
        armed = config.ARMED_FLAG.exists()
        _state["armed"] = armed
        ok = _host_reachable()
        _state["reachable"] = ok
        now = time.time()
        if ok:
            _state["last_ok"] = now
        if config.DEADMAN_ENABLED and armed and not ok and _state["last_ok"]:
            if now - _state["last_ok"] >= config.DEADMAN_TIMEOUT:
                self_destruct("потеря связи с хостом")
                return
        time.sleep(config.DEADMAN_INTERVAL)


def start() -> None:
    threading.Thread(target=_loop, daemon=True).start()
