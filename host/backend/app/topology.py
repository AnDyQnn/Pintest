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

import re
from collections import defaultdict
from typing import Dict, List, Optional, Set

from exploits import catalog

from . import agents as agents_mod, db, targets as targets_mod


def _subnet(ip: str) -> str:
    m = re.match(r"^(\d+\.\d+\.\d+)\.", ip or "")
    return m.group(1) if m else (ip or "")


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


def _pivot_hosts_map() -> Dict[str, List[Dict]]:
    """pivot_ip -> [{hidden_ip, ports}] — скрытые хосты, найденные через плацдарм."""
    out: Dict[str, List[Dict]] = defaultdict(list)
    for r in db.all_("SELECT pivot, hidden_ip, ports FROM pivot_hosts"):
        out[r["pivot"]].append({"hidden_ip": r["hidden_ip"], "ports": r["ports"] or []})
    return out


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


# ------------------------------------------------ самовосстановление (mesh)
def resolve_mesh(nodes: List[Dict], candidates: Dict[str, Set[str]],
                 online: Set[str], captured: Set[str]) -> Set[str]:
    """Живучая достижимость: BFS от хоста с реле через ЗАХВАЧЕННЫЕ узлы (fixpoint).

    Цель достижима, если её видит живой агент-кандидат ЛИБО в её подсети есть достижимый
    захваченный узел — он становится **реле** (ближайший узел → «агент для хоста»). Так при
    падении агента сеть за ним не теряется, а перестраивается через захваченный плацдарм.
    Возвращает множество IP, ставших реле. Мутирует узлы (reachable/route_agent/relay/…).
    """
    reach: Dict[str, Dict] = {}
    for n in nodes:                                    # прямой доступ через живого агента
        ip = n["ip"]
        live = sorted(c for c in candidates.get(ip, set()) if c in online)
        if live:
            reach[ip] = {"agent": live[0], "relay": None}
    changed = True                                     # fixpoint: реле тянут за собой подсеть
    while changed:
        changed = False
        by_sub: Dict[str, List[str]] = {}
        for ip in list(reach):
            if ip in captured:
                by_sub.setdefault(_subnet(ip), []).append(ip)
        for n in nodes:
            ip = n["ip"]
            if ip in reach:
                continue
            rs = by_sub.get(_subnet(ip))
            if rs:
                r = rs[0]
                reach[ip] = {"agent": reach[r]["agent"], "relay": r}
                changed = True
    relay_ips: Set[str] = set()
    for n in nodes:
        ip = n["ip"]
        info = reach.get(ip)
        assigned = n.get("agent_id")
        n["candidates"] = sorted(candidates.get(ip, set()))
        if info:
            n["reachable"] = True
            n["route_agent"] = info["agent"]
            n["relay"] = info["relay"]
            n["rerouted_from"] = assigned if (assigned and assigned not in online
                                              and (info["relay"] or assigned != info["agent"])) else None
            n["route"] = ["host", info["agent"]] + ([info["relay"], ip] if info["relay"] else [ip])
            if info["relay"]:
                relay_ips.add(info["relay"])
        else:
            n["reachable"] = False
            n["route_agent"] = None
            n["relay"] = None
            n["rerouted_from"] = None
            n["route"] = ["host"]
    for n in nodes:
        n["is_relay"] = n["ip"] in relay_ips
    return relay_ips


def single_points_mesh(nodes: List[Dict], candidates: Dict[str, Set[str]],
                       online: Set[str], captured: Set[str]) -> Dict[str, List[str]]:
    """Агент → цели, которые станут недостижимыми при его выпадении ДАЖЕ с учётом реле.

    Считаем базовую достижимость, затем для каждого агента — достижимость без него; разница =
    цели, которые он держит эксклюзивно (даже захваченные плацдармы не спасают). С mesh таких
    точек отказа заметно меньше — в этом и суть живучести.
    """
    base = [dict(n) for n in nodes]
    resolve_mesh(base, candidates, online, captured)
    base_reach = {n["ip"] for n in base if n["reachable"]}
    crit: Dict[str, List[str]] = {}
    for a in online:
        red = [dict(n) for n in nodes]
        resolve_mesh(red, candidates, online - {a}, captured)
        lost = base_reach - {n["ip"] for n in red if n["reachable"]}
        if lost:
            crit[a] = sorted(lost)
    return crit


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
    """Целевой слой + маршруты/достижимость + живые стадии скана (для live-потока и /api/topology)."""
    nodes = _target_nodes()
    cand = _candidates()
    online = {a["id"] for a in agents_mod.list_agents() if a["status"] == "online"}
    captured = {n["ip"] for n in nodes if n["status"] == "captured"}   # плацдармы = потенциальные реле
    relays = resolve_mesh(nodes, cand, online, captured)               # живучая достижимость (self-heal)

    # живые стадии скана (probing/alive/scanned) — граф строится в реальном времени
    from . import orchestrator                     # лениво: избегаем циклического импорта
    live = orchestrator.live_stages()
    scanning = bool(live)
    for n in nodes:
        n["stage"] = live.get(n["ip"])             # None вне скана

    # скрытые хосты, найденные через pivot — узлами ЗА реле-узлом (агенты их не видят)
    hcaps = _captures_by_host()
    node_by_ip = {n["ip"]: n for n in nodes}
    for pivot_ip, hs in _pivot_hosts_map().items():
        pnode = node_by_ip.get(pivot_ip)
        for h in hs:
            hid = h["hidden_ip"]
            if hid in node_by_ip:
                continue
            reach = bool(pnode and pnode.get("reachable"))
            ra = (pnode or {}).get("route_agent")
            cap = hcaps.get(hid, {})               # захвачена ли скрытая цель (через pivot)
            nodes.append({
                "ip": hid, "agent_id": None, "exploiter_id": None,
                "status": "captured" if cap.get("captured") else "discovered",
                "cve_count": 0, "exploit_count": 0, "top_cve": None,
                "flag": cap.get("flag"), "candidates": [], "hidden": True,
                "relay": pivot_ip, "route_agent": ra, "reachable": reach,
                "rerouted_from": None, "is_relay": False, "stage": None,
                "ports": h.get("ports") or [],
                "route": ["host", ra, pivot_ip, hid] if reach else ["host"],
            })
            node_by_ip[hid] = nodes[-1]
            if pnode:
                pnode["is_relay"] = True           # плацдарм активен как реле
                relays.add(pivot_ip)

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
        "relays": sorted(relays),
        "single_points": single_points_mesh(nodes, cand, online, captured),
        "scanning": scanning,
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
