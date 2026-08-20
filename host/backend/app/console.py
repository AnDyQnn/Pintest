"""backend.console — проброс интерактивной консоли к агенту (через туннель).

Вебка не ходит к агенту напрямую — только через хост. Хост ретранслирует запросы
на агентский /console/* API поверх AmneziaWG. Так реализуется «консоль любого узла
из вебки»: пока — к агент-нодам (они под нашим управлением); к захваченным целям —
позже через pivot (агент на цели).
"""
from __future__ import annotations

from typing import Dict

import httpx

from . import agents, config


def _base(agent_id: str) -> str:
    a = agents.get(agent_id)
    if not a:
        raise KeyError("агент не найден")
    return f"http://{a['tunnel_ip']}:{config.AGENT_API_PORT}"


def open(agent_id: str, cols: int = 120, rows: int = 30) -> Dict:
    r = httpx.post(_base(agent_id) + "/console",
                   json={"cols": cols, "rows": rows}, timeout=15)
    return r.json()


def write(agent_id: str, sid: str, data: str) -> Dict:
    r = httpx.post(f"{_base(agent_id)}/console/{sid}/input",
                   json={"data": data}, timeout=15)
    return r.json()


def read(agent_id: str, sid: str, since: int = 0) -> Dict:
    r = httpx.get(f"{_base(agent_id)}/console/{sid}/output",
                  params={"since": since}, timeout=40)
    return r.json()


def resize(agent_id: str, sid: str, cols: int, rows: int) -> Dict:
    r = httpx.post(f"{_base(agent_id)}/console/{sid}/resize",
                   json={"cols": cols, "rows": rows}, timeout=15)
    return r.json()


def close(agent_id: str, sid: str) -> Dict:
    r = httpx.delete(f"{_base(agent_id)}/console/{sid}", timeout=15)
    return r.json()
