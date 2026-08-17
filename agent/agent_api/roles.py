"""agent_api.roles — роли агента и «донастройка» под них.

По умолчанию агент — только сканер. Роль «эксплуататор» назначается с хоста для
ВЫБРАННЫХ нод; при назначении на ноде выполняется донастройка (setup-скрипт:
доустановка инструментов, подготовка окружения) и только после этого агент готов
запускать модули эксплуатации. Так пользователь сам решает, кто чем занят.
"""
from __future__ import annotations

import json
import subprocess
from typing import Dict, List

from . import config

ROLES_FILE = config.STATE_DIR / "roles.json"
KNOWN = {"scanner", "exploiter"}
_DEFAULT = {"roles": ["scanner"], "exploiter_ready": False, "setup_log": []}


def _load() -> Dict:
    try:
        return json.loads(ROLES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(_DEFAULT)


def _save(d: Dict) -> None:
    config.ensure_dirs()
    ROLES_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def state() -> Dict:
    return _load()


def has_role(role: str) -> bool:
    d = _load()
    if role == "exploiter":
        return "exploiter" in d.get("roles", []) and d.get("exploiter_ready", False)
    return role in d.get("roles", [])


def assign(role: str) -> Dict:
    """Назначить роль + выполнить донастройку. Идемпотентно."""
    if role not in KNOWN:
        raise ValueError(f"неизвестная роль: {role}")
    d = _load()
    if role not in d["roles"]:
        d["roles"].append(role)
    if role == "exploiter":
        log = _run_setup("setup-exploiter.sh")
        d["exploiter_ready"] = True
        d["setup_log"] = log
    _save(d)
    return d


def revoke(role: str) -> Dict:
    d = _load()
    if role == "scanner":
        raise ValueError("роль scanner снять нельзя")
    d["roles"] = [r for r in d["roles"] if r != role]
    if role == "exploiter":
        d["exploiter_ready"] = False
    _save(d)
    return d


def _run_setup(script: str) -> List[str]:
    """Запустить setup-скрипт роли (донастройка). Ошибки не фатальны — лог возвращаем."""
    path = config.SCRIPTS_DIR / script
    if not path.exists():
        return [f"setup-скрипт {script} не найден — пропуск"]
    try:
        r = subprocess.run(["bash", str(path)], capture_output=True, text=True, timeout=180)
        out = (r.stdout + r.stderr).strip().splitlines()
        return out[-40:] if out else ["донастройка выполнена (без вывода)"]
    except (subprocess.TimeoutExpired, OSError) as e:
        return [f"донастройка не завершилась: {e}"]
