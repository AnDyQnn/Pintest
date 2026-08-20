"""backend.topology — сеть как ГРАФ: узлы (host/агенты/цели) + рёбра + достижимость.

Три эшелона узлов:
  * хост (control plane);
  * агент-ноды (по статусу online/provisioning/lost/destroyed);
  * цели — под тем агентом, который держит к ним МАРШРУТ сейчас (route_agent).

Статус цели (по данным аудита/обработки, не «выдумкой»):
  captured    — модуль capture отработал успешно (есть флаг) — «залутали»;
  exploitable — есть CVE с модулем в каталоге exploits либо check подтвердил;
  vulnerable  — CVE есть, модуля под них нет;
  discovered  — хост просканирован, зацепок нет — «просто пропинговали»;
  pending     — цель в списке, но ещё не досталась ни одному агенту.

ДОСТИЖИМОСТЬ / РОКИРОВКА (обобщение failover-чанков на уровень графа целей):
  У каждой цели есть КАНДИДАТЫ — агенты, которые её реально видят (сканировали или
  обрабатывали). Маршрут = host -> живой агент-кандидат -> цель. Если исходный агент
  офлайн, но другой кандидат online — маршрут ПЕРЕНОСИТСЯ (reroute, «рокировка»).
  Если живых кандидатов нет — цель unreachable (маршрут отрезан, но цель не «мертва»).
  Единая точка отказа (single point of failure) — агент, который для какой-то цели
  ЕДИНСТВЕННЫЙ живой кандидат: убери его — цель станет недостижимой.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set

from exploits import catalog

from . import agents as agents_mod, db, targets as targets_mod


# ---------------------------------------------------------------- источники данных
def _target_agent_map() -> Dict[str, str]:
    """IP -> агент, которому достался последний чанк с этой целью (исходный маршрут)."""
    m: Dict[str, str] = {}
    rows = db.all_(
        "SELECT c.agent_id, c.targets FROM chunks c JOIN jobs j ON j.id = c.job_id "
        "WHERE c.agent_id IS NOT NULL ORDER BY j.created_at ASC")
    for r in rows:
        for t in (r["targets"] or []):
            m[t] = r["agent_id"]
    return m


def _candidates() -> Dict[str, Set[str]]:
    """IP -> множество агентов, которые эту цель РЕАЛЬНО видели (скан или обработка).

    Именно из этого множества берётся альтернатива при рокировке.
    """
    m: Dict[str, Set[str]] = defaultdict(set)
    for r in db.all_("SELECT agent_id, targets FROM chunks WHERE agent_id IS NOT NULL"):
        for t in (r["targets"] or []):
            m[t].add(r["agent_id"])
    for c in db.all_("SELECT agent_id, target FROM captures WHERE agent_id IS NOT NULL"):
        m[c["target"]].add(c["agent_id"])
    return m


def _cves_by_host() -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for r in db.all_("SELECT DISTINCT host, cve, cvss, severity FROM findings"):
        out.setdefault(r["host"], []).append(r)
    return out


def _captures_by_host() -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for c in db.all_("SELECT target, phase, success, flag, agent_id FROM captures ORDER BY ts ASC"):
        h = c["target"]
        d = out.setdefault(h, {"captured": False, "checked": False, "flag": None, "exploiter": None})
        if c["phase"] == "capture" and c["success"]:
            d["captured"] = True
            d["flag"] = c.get("flag") or d["flag"]
            d["exploiter"] = c["agent_id"]
        elif c["phase"] == "check" and c["success"]:
            d["checked"] = True
            d["exploiter"] = d["exploiter"] or c["agent_id"]
    return out


# ---------------------------------------------------------------- чистая логика графа
def resolve_routes(nodes: List[Dict], candidates: Dict[str, Set[str]],
                   online: Set[str]) -> None:
    """Проставить каждой цели маршрут/достижимость/рокировку (in-place).

    Чистая функция от (узлы, кандидаты, множество online-агентов) — тестируется
    без БД и без порчи лабы: достаточно передать синтетический `online`.
    """
    for n in nodes:
        ip = n["ip"]
        cands = candidates.get(ip, set())
        assigned = n.get("agent_id")
        chosen: Optional[str] = None
        rerouted_from: Optional[str] = None
        if assigned in online:
            chosen = assigned
        else:
            alt = next((c for c in sorted(cands) if c in online), None)
            if alt:
                chosen = alt
                rerouted_from = assigned            # был другой агент -> это рокировка
        n["candidates"] = sorted(cands)
        n["route_agent"] = chosen
        n["reachable"] = chosen is not None
        n["rerouted_from"] = rerouted_from
        n["route"] = ["host", chosen, ip] if chosen else ["host"]


def single_points(nodes: List[Dict], candidates: Dict[str, Set[str]],
                  online: Set[str]) -> Dict[str, List[str]]:
    """Агент -> цели, для которых он ЕДИНСТВЕННЫЙ живой кандидат (точки отказа).

    Это graph-articulation на уровне агентов: убери такого агента — перечисленные
    цели станут недостижимыми (та самая «узел защитили -> всё за ним потеряно»).
    """
    crit: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        live = [c for c in candidates.get(n["ip"], set()) if c in online]
        if len(live) == 1:
            crit[live[0]].append(n["ip"])
    return dict(crit)


# ---------------------------------------------------------------- сборка
def _target_nodes() -> List[Dict]:
    tmap = _target_agent_map()
    cbh = _cves_by_host()
    caps = _captures_by_host()

    canon = targets_mod.latest() or {}
    all_targets: List[str] = list(canon.get("targets") or [])
    for h in set(list(tmap) + list(cbh) + list(caps)):
        if h not in all_targets:
            all_targets.append(h)

    nodes: List[Dict] = []
    for ip in all_targets:
        cves = cbh.get(ip, [])
        modcount = sum(1 for r in cves if catalog.by_cve(r["cve"]))
        cap = caps.get(ip, {})
        scanned = (ip in tmap) or bool(cves)
        if cap.get("captured"):
            status = "captured"
        elif cap.get("checked") or modcount:
            status = "exploitable"
        elif cves:
            status = "vulnerable"
        elif scanned:
            status = "discovered"
        else:
            status = "pending"
        top_cve = sorted(cves, key=lambda r: -(r.get("cvss") or 0))[0]["cve"] if cves else None
        nodes.append({
            "ip": ip, "agent_id": tmap.get(ip), "exploiter_id": cap.get("exploiter"),
            "status": status, "cve_count": len(cves), "exploit_count": modcount,
            "top_cve": top_cve, "flag": cap.get("flag"),
        })
    return nodes


def build() -> Dict:
    """Целевой слой + маршруты/достижимость (для live-потока и /api/topology)."""
    nodes = _target_nodes()
    cand = _candidates()
    online = {a["id"] for a in agents_mod.list_agents() if a["status"] == "online"}
    resolve_routes(nodes, cand, online)

    counts: Dict[str, int] = defaultdict(int)
    reach = 0
    for n in nodes:
        counts[n["status"]] += 1
        if n["reachable"]:
            reach += 1
    return {
        "targets": nodes,
        "counts": dict(counts),
        "reachable": reach,
        "unreachable": len(nodes) - reach,
        "rerouted": sum(1 for n in nodes if n["rerouted_from"]),
        "single_points": single_points(nodes, cand, online),
    }


def graph() -> Dict:
    """Полный граф (узлы + рёбра) — для /api/graph и графа зависимостей в отчёте."""
    ags = agents_mod.list_agents()
    online = {a["id"] for a in ags if a["status"] == "online"}
    t = build()
    nodes = [{"id": "host", "kind": "host", "label": "HOST"}]
    for a in ags:
        nodes.append({"id": a["id"], "kind": "agent", "label": a["name"],
                      "status": a["status"], "roles": a["roles"], "ip": a["tunnel_ip"]})
    for n in t["targets"]:
        nodes.append({"id": n["ip"], "kind": "target", "label": n["ip"],
                      "status": n["status"], "reachable": n["reachable"]})

    edges: List[Dict] = []
    for a in ags:
        edges.append({"src": "host", "dst": a["id"], "kind": "control",
                      "state": "up" if a["id"] in online else "down"})
    for n in t["targets"]:
        if n.get("agent_id"):
            edges.append({"src": n["agent_id"], "dst": n["ip"], "kind": "scan", "state": "hist"})
        if n.get("exploiter_id"):
            edges.append({"src": n["exploiter_id"], "dst": n["ip"], "kind": "exploit", "state": "hist"})
        if n.get("route_agent") and n["route_agent"] != n.get("agent_id"):
            edges.append({"src": n["route_agent"], "dst": n["ip"], "kind": "reroute", "state": "up"})
    return {"nodes": nodes, "edges": edges, "summary": {
        "reachable": t["reachable"], "unreachable": t["unreachable"],
        "rerouted": t["rerouted"], "single_points": t["single_points"], "counts": t["counts"]}}
