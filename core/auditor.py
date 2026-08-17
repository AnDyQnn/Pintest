#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit.py — учебный аудитор сети (один файл, только stdlib).

Один прогон: разведка целей -> живые хосты -> глубокий скан (TCP+UDP, NSE) ->
отчёты (Markdown / интерактивный HTML / CSV / JSON). Оценка критичности (CVSS),
риск и рекомендации проставляются автоматически. Эксплуатацию НЕ выполняет.

!!! Сканируй только то, на что есть разрешение (свой стенд/VPS/scope задания).
"""
import argparse, csv, html, ipaddress, json, os, re, shutil, signal
import subprocess, sys, tempfile, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

def _env_int(name, default):
    """Оверрайд числовой константы из окружения (оркестратор/лаба). Некорректное -> дефолт.
    Стековая копия движка: правим свободно, оригинал pintest/audit.py остаётся эталоном."""
    try:
        v = int(os.environ.get(name, ""))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default

CHUNK       = _env_int("PINTEST_CHUNK", 256)       # хостов на один проход nmap в глубоком скане (бережёт ОЗУ)
DISC_CHUNK  = _env_int("PINTEST_DISC_CHUNK", 8192) # хостов на проход в host discovery
PARALLEL    = _env_int("PINTEST_PARALLEL", 4)      # сколько чанков сканировать одновременно (пул потоков)
NMAP_TIMEOUT = 3600      # сек: предохранитель — один чанк nmap не висит дольше часа
V6_CAP      = 4096       # макс. размер IPv6-подсети, которую разворачиваем целиком
V4_CAP      = 65536      # макс. размер IPv4-сети, которую разворачиваем в адреса
SCRIPTS     = "vuln,vulners,http-enum,ssl-enum-ciphers,smb-os-discovery,smb-enum-shares"
EXTRA       = ["--max-retries", "1"]   # мёртвый хост не переспрашиваем по многу раз (умный дефолт)
PREFLIGHT_HOSTS = 6      # сколько живых хостов пробуем в префлайте портов (выборка)
PREFLIGHT_MAXP  = 1200   # не пробуем в префлайте больше стольки портов (огромные наборы -> топ)

TTY = sys.stdout.isatty()
_RAW = {"CR": "\033[0m", "CB": "\033[1m", "CCY": "\033[36m", "CGR": "\033[32m",
        "CYE": "\033[33m", "CRD": "\033[31m", "CBL": "\033[34m", "CMA": "\033[35m", "CGY": "\033[90m"}
CR = CB = CCY = CGR = CYE = CRD = CBL = CMA = CGY = ""
def set_colors(on):
    global CR, CB, CCY, CGR, CYE, CRD, CBL, CMA, CGY
    CR  = _RAW["CR"]  if on else ""; CB  = _RAW["CB"]  if on else ""
    CCY = _RAW["CCY"] if on else ""; CGR = _RAW["CGR"] if on else ""
    CYE = _RAW["CYE"] if on else ""; CRD = _RAW["CRD"] if on else ""
    CBL = _RAW["CBL"] if on else ""; CMA = _RAW["CMA"] if on else ""; CGY = _RAW["CGY"] if on else ""
set_colors(TTY)

# ----------------- статус (для --status в реальном времени) --------------------
_STAT = {}
def stat_set(outdir=None, **kw):
    if outdir is not None:
        _STAT["dir"] = str(outdir)
    _STAT.update(kw)
    _STAT["updated"] = time.time()
    d = _STAT.get("dir")
    if d:
        rec = {k: v for k, v in _STAT.items() if k != "dir"}
        try:
            (Path(d) / "status.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

# ----------------------- цветовые хелперы --------------------------------------
def ok(s):   return f"{CGR}{s}{CR}"          # зелёный
def warn(s): return f"{CYE}{s}{CR}"          # жёлтый
def bad(s):  return f"{CRD}{s}{CR}"          # красный
def num(s):  return f"{CB}{CCY}{s}{CR}"      # ярко-циан жирный (числа)
def dim(s):  return f"{CGY}{s}{CR}"          # серый (второстепенное)
def hot(s):  return f"{CB}{CMA}{s}{CR}"      # magenta жирный (акценты)

def logo():
    grad = [CMA, CBL, CCY]
    art = ["  ╔═╗╦ ╦╔╦╗╦╔╦╗", "  ╠═╣║ ║ ║║║ ║ ", "  ╩ ╩╚═╝═╩╝╩ ╩ "]
    print()
    for i, l in enumerate(art):
        print(f"{CB}{grad[i % len(grad)]}{l}{CR}")
    print(f"  {dim('сетевой аудитор · nmap → отчёты (md · html · csv · json)')}\n")

# ----------------------------- оценка -----------------------------------------
def severity(score):
    s = float(score or 0)
    if s >= 9: return "Critical"
    if s >= 7: return "High"
    if s >= 4: return "Medium"
    if s > 0:  return "Low"
    return "Info"

def risk_txt(sev):
    return {
        "Critical": "критично: возможна полная компрометация узла",
        "High":     "высокий: вероятна компрометация сервиса/узла",
        "Medium":   "средний: утечка данных / отказ в обслуживании",
        "Low":      "низкий: ограниченное воздействие",
    }.get(sev, "явных CVE не найдено")

def recommend(sev):
    if sev in ("Critical", "High"):
        return ("срочно обновить затронутый сервис до актуальной версии; до обновления — "
                "ограничить доступ к порту фаерволом или отключить сервис, если не нужен")
    if sev == "Medium":
        return "запланировать обновление сервиса; ограничить доступ к порту доверенными адресами"
    if sev == "Low":
        return "обновить при плановом обслуживании; проверить необходимость публичного доступа к порту"
    return "убедиться, что сервис актуальной версии и порт не открыт без необходимости"

def css_class(sev):
    return {"Critical": "crit", "High": "high", "Medium": "med", "Low": "low"}.get(sev, "info")

# --------------------------- вывод/баннеры -------------------------------------
def banner(title):
    line = "─" * 46
    print(f"\n{CB}{CCY}┌{line}{CR}\n{CB}{CCY}│{CR} {CB}{CYE}▶ {title}{CR}\n{CB}{CCY}└{line}{CR}")

def make_bar(filled, width):
    """Полоса прогресса: filled закрашенных из width (защита от выхода за края)."""
    filled = max(0, min(int(filled), width))
    return f"{CGR}{'█'*filled}{CGY}{'░'*(width-filled)}{CR}"

def overall(stage, total=4):
    w = 30; pct = stage * 100 // total
    print(f"\n{CB}{CBL}ОБЩИЙ ПРОГРЕСС{CR} [{make_bar(stage * w // total, w)}] "
          f"{CB}{CYE}{pct}%{CR}  (этап {stage} из {total})")

_PSTART = [time.time()]      # старт текущего этапа (для расчёта ETA)

def prog(label, done, total, extra=""):
    total = max(total, 1)
    done = min(done, total)
    w = 30; f = done * w // total; pct = done * 100 // total
    bar = f"{CGR}{'█'*f}{CGY}{'░'*(w-f)}{CR}"
    el = time.time() - _PSTART[0]
    eta = "~" + _fmt_dur(el / done * (total - done)) if 0 < done < total else "—"
    msg = (f"   [{bar}] {CB}{CYE}{pct:3d}%{CR}  {CCY}{done}/{total}{CR} хостов  "
           f"прошло {CGR}{_fmt_dur(el)}{CR}  ост {CMA}{eta}{CR}  {extra}")
    if TTY:
        sys.stdout.write("\r" + msg + "      "); sys.stdout.flush()
    else:
        print(f"   ... {label}: {done}/{total}  прошло {_fmt_dur(el)}  ост {eta}  {extra}")

def prog_done():
    if TTY: sys.stdout.write("\n")

# --------------------------- разбор целей --------------------------------------
def _add_net_v4(net, out):
    if net.num_addresses <= V4_CAP:
        for a in net.hosts() if net.prefixlen < 31 else net:
            out.add(str(a))
        if net.prefixlen >= 31:      # /31,/32 — hosts() пуст, берём сами адреса
            for a in net: out.add(str(a))
    else:
        out.add(str(net))            # слишком большая — отдаём nmap как CIDR

def parse_targets(path):
    v4, v6, notes = set(), set(), []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip().replace(" ", "")
            if not line:
                continue
            try:
                if ":" in line:                       # IPv6
                    _parse_v6(line, v6, notes)
                else:                                 # IPv4
                    _parse_v4(line, v4, notes)
            except ValueError:
                notes.append(f"пропущено (не адрес): {line}")
    return sorted(v4, key=lambda x: ipaddress.ip_address(x.split('/')[0].split('-')[0])), \
           sorted(v6), notes

def _parse_v4(line, out, notes):
    if "-" in line:
        a, b = line.split("-", 1)
        if "." in b:                                  # полный диапазон A-B
            for net in ipaddress.summarize_address_range(
                    ipaddress.IPv4Address(a), ipaddress.IPv4Address(b)):
                _add_net_v4(net, out)
        else:                                         # x.x.x.a-b
            prefix, start = a.rsplit(".", 1)
            for i in range(int(start), int(b) + 1):
                out.add(f"{prefix}.{i}")
                ipaddress.IPv4Address(f"{prefix}.{i}")  # валидация
    elif "/" in line:
        _add_net_v4(ipaddress.ip_network(line, strict=False), out)
    else:
        out.add(str(ipaddress.IPv4Address(line)))

def _parse_v6(line, out, notes):
    if "-" in line:
        a, b = line.split("-", 1)
        for net in ipaddress.summarize_address_range(
                ipaddress.IPv6Address(a), ipaddress.IPv6Address(b)):
            if net.num_addresses <= V6_CAP:
                out.add(net.with_prefixlen)
            else:
                out.add(str(net.network_address))
                notes.append(f"IPv6 {line} = {net.with_prefixlen} "
                             f"(2^{128-net.prefixlen} адресов) — беру базовый {net.network_address}")
    elif "/" in line:
        out.add(ipaddress.ip_network(line, strict=False).with_prefixlen)
    else:
        out.add(str(ipaddress.IPv6Address(line)))

# ------------------------------ nmap ------------------------------------------
def _sudo(cmd):
    if hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
        return ["sudo"] + cmd
    return cmd

def nmap_xml(targets, extra, six=False):
    """Запустить nmap по списку целей, вернуть распарсенный XML-корень (или None)."""
    if not targets:
        return None
    tf = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt")
    tf.write("\n".join(targets) + "\n"); tf.close()
    cmd = ["nmap"] + (["-6"] if six else []) + ["-n", "-oX", "-", "-iL", tf.name] + extra + EXTRA
    out = ""
    try:
        res = subprocess.run(_sudo(cmd), capture_output=True, text=True, timeout=NMAP_TIMEOUT)
        out = res.stdout
    except (subprocess.TimeoutExpired, OSError, ValueError):
        out = ""                     # чанк завис/сбой — пропускаем, скан продолжается
    finally:
        try: os.unlink(tf.name)
        except OSError: pass
    try:
        return ET.fromstring(out)
    except ET.ParseError:
        return None

def _extract_cves(script_el, cves):
    for tbl in script_el.iter("table"):
        cid = cvss = None
        for e in tbl.findall("elem"):
            if e.get("key") == "id":   cid = (e.text or "").strip()
            if e.get("key") == "cvss":
                try: cvss = float(e.text)
                except (TypeError, ValueError): pass
        if cid and cid.startswith("CVE") and cvss is not None:
            cves[cid] = max(cvss, cves.get(cid, 0.0))
    for m in re.finditer(r"(CVE-\d{4}-\d+)\s+(\d+\.\d+)", script_el.get("output", "") or ""):
        cid, v = m.group(1), float(m.group(2))
        cves[cid] = max(v, cves.get(cid, 0.0))

def parse_hosts(root):
    hosts = []
    if root is None:
        return hosts
    for h in root.findall("host"):
        ip = None; fam = "IPv4"
        for a in h.findall("address"):
            if a.get("addrtype") == "ipv4": ip, fam = a.get("addr"), "IPv4"
            elif a.get("addrtype") == "ipv6": ip, fam = a.get("addr"), "IPv6"
        if not ip:
            continue
        st = h.find("status")
        up = st is not None and st.get("state") == "up"
        ports, cves = [], {}
        pel = h.find("ports")
        if pel is not None:
            for p in pel.findall("port"):
                stt = p.find("state")
                if stt is None or stt.get("state") != "open":
                    continue
                svc = p.find("service")
                name = svc.get("name", "") if svc is not None else ""
                ver = ((svc.get("product", "") + " " + svc.get("version", "")).strip()
                       if svc is not None else "")
                ports.append((int(p.get("portid")), p.get("protocol"), name, ver))
                for scr in p.findall("script"):
                    _extract_cves(scr, cves)
        for scr in h.findall("hostscript/script"):
            _extract_cves(scr, cves)
        hosts.append({"ip": ip, "fam": fam, "up": up, "ports": ports, "cves": cves})
    return hosts

# --------------------------- этапы скана ---------------------------------------
def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def _disc_chunk(ch, timing, six):
    return parse_hosts(nmap_xml(ch, ["-sn", timing], six=six))

def discover(targets, six, timing, outdir):
    """Живые адреса; параллельно + сохранение прогресса (для --resume)."""
    fam = "IPv6" if six else "IPv4"; tag = "v6" if six else "v4"
    alive_file = outdir / f"alive_{tag}.txt"; done_file = f"disc_done_{tag}.txt"
    prev_done = _read_ipset(outdir / done_file)
    alive = list(_read_ipset(alive_file))               # уже найденные (resume)
    todo = [t for t in targets if t not in prev_done]
    total = len(targets); done = total - len(todo)
    if not todo:
        return alive
    stat_set(stage=2, phase=f"host discovery {fam}", done=done, total=total,
             pstart=time.time(), alive=len(alive))
    _PSTART[0] = time.time()
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        futs = {ex.submit(_disc_chunk, ch, timing, six): ch for ch in _chunks(todo, DISC_CHUNK)}
        for fut in as_completed(futs):
            new = [h["ip"] for h in fut.result() if h["up"]]
            alive.extend(new)
            _append_lines(alive_file, new)
            _append_lines(outdir / done_file, futs[fut])
            done += len(futs[fut])
            stat_set(stage=2, phase=f"host discovery {fam}", done=done, total=total, alive=len(alive))
            prog(f"живость {fam}", done, total, f"живых: {CGR}{len(alive)}{CR}")
    prog_done()
    return alive

def _scan_chunk(ch, specs, six):
    out = []
    for spec in specs:
        out += parse_hosts(nmap_xml(ch, spec, six=six))
    return out

# --------------------- префлайт: проверка доступности портов -------------------
_FILTERED = {"filtered", "open|filtered", "closed|filtered"}   # признак блокировки провайдером/VPN

def _port_states(root):
    """Записи ПО ХОСТАМ: [{'ports': {portid: set(состояний)}, 'extra': {state: count}}].
    ports — порты, перечисленные ПОШТУЧНО; extra — схлопнутые <extraports> (nmap
    сворачивает пачку одинаковых портов в сводку «Not shown: N closed/filtered ports»).
    Держим по хостам, чтобы отличать «набор режется у всех» от «часть хостов молчит»."""
    recs = []
    if root is None:
        return recs
    for h in root.findall("host"):
        pel = h.find("ports")
        if pel is None:
            continue
        pp, ex = {}, {}
        for p in pel.findall("port"):
            stt = p.find("state")
            if stt is None:
                continue
            pp.setdefault(int(p.get("portid")), set()).add(stt.get("state"))
        for ep in pel.findall("extraports"):
            st = ep.get("state") or "?"
            try: cnt = int(ep.get("count", 0) or 0)
            except (TypeError, ValueError): cnt = 0
            ex[st] = ex.get(st, 0) + cnt
        recs.append({"ports": pp, "extra": ex})
    return recs

def _sample(alive, k):
    """Равномерная выборка до k адресов (не только первые подряд)."""
    if len(alive) <= k:
        return list(alive)
    step = max(1, len(alive) // k)
    return alive[::step][:k]

def _ports_compact(ports, cap=20):
    """Список портов -> строка с диапазонами: 22,80,443,3389 (длинные — с '…')."""
    ps = sorted(set(ports)); out = []; i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[j] + 1:
            j += 1
        out.append(str(ps[i]) if j == i else f"{ps[i]}-{ps[j]}")
        i = j + 1
    s = ",".join(out[:cap])
    if len(out) > cap:
        s += f",…(+{len(out) - cap})"
    return s

def preflight_ports(alive4, alive6, pargs, plabel, timing, outdir):
    """Проба доступности портов на выборке живых хостов ДО глубокого скана.
    Порты, зафильтрованные на всей выборке (провайдер/VPN режет), исключаются
    из набора. Возвращает (pargs, plabel, blocked_list). Пишет ports_effective.json."""
    fam_hosts = []
    if alive4: fam_hosts.append((_sample(alive4, PREFLIGHT_HOSTS), False))
    if alive6: fam_hosts.append((_sample(alive6, PREFLIGHT_HOSTS), True))
    if not fam_hosts:
        return pargs, plabel, []
    # какой набор портов пробуем и можно ли по итогам урезать исходный список
    is_all = pargs == ["-p-"]
    is_top = pargs[:1] == ["--top-ports"]
    topn = int(pargs[1]) if is_top else 0
    if is_all:
        probe, prune = ["--top-ports", str(PREFLIGHT_MAXP)], False   # весь /-p-: только диагностика
    elif is_top and topn > PREFLIGHT_MAXP:
        probe, prune = ["--top-ports", str(PREFLIGHT_MAXP)], False   # слишком большой топ: диагностика
    else:
        probe, prune = list(pargs), True                            # -p СПИСОК или небольшой топ: можно урезать
    spec = ["-sT", "-Pn", "-n", timing, *probe, "--host-timeout", "3m"]
    stat_set(stage=3, phase="проба портов (префлайт)", done=0, total=1)
    _PSTART[0] = time.time()
    recs = []
    for hosts, six in fam_hosts:
        recs += _port_states(nmap_xml(hosts, spec, six=six))
    if not recs:                                     # ни один хост не ответил на пробу
        stat_set(stage=3, blocked="", blocked_n=0)
        _save_effective(outdir, pargs, plabel, [], note="хосты не ответили на пробу")
        print(f"   {dim('префлайт: хосты не ответили на пробу — список портов без изменений')}")
        return pargs, plabel, []
    # поштучные порты, объединённые по всем хостам выборки
    states = {}
    for r in recs:
        for pid, sset in r["ports"].items():
            states.setdefault(pid, set()).update(sset)
    probed = sorted(states)
    def _responsive(r):        # хост «ответил», если есть open/closed (поштучно или в сводке)
        if any(s & {"open", "closed", "unfiltered"} for s in r["ports"].values()):
            return True
        return bool(r["extra"].get("closed", 0) or r["extra"].get("open", 0))
    n_hosts = len(recs)
    n_resp = sum(1 for r in recs if _responsive(r))
    n_silent = n_hosts - n_resp                      # хосты, что молчат/фильтруют всё (лежат или дропают)
    collapsed_any = any(r["extra"] for r in recs)    # были ли схлопнутые порты (тогда поштучно видим не всё)
    any_filtered = (any(states[p] <= _FILTERED for p in probed)
                    or any(r["extra"].get("filtered", 0) or r["extra"].get("open|filtered", 0) for r in recs))
    blocked = [p for p in probed if states[p] and states[p] <= _FILTERED]     # filtered у ВСЕХ, кто перечислил поштучно
    reachable = [p for p in probed if p not in blocked]
    open_ports = [p for p in probed if "open" in states[p]]
    total_block = n_resp == 0                        # никто не ответил open/closed -> полный фильтр
    # --- вывод: по ХОСТАМ (честно), а не по «max количеству портов» ---
    print(f"   {dim('проба портов: выборка ' + str(n_hosts) + ' хостов → ответили ')}"
          f"{ok(n_resp)}{dim(', молчат/фильтруют ')}{warn(n_silent) if n_silent else num(0)}")
    if open_ports:
        print(f"   {dim('открытые порты на выборке:')} {CGR}{_ports_compact(open_ports)}{CR}")
    if blocked:
        print(f"   {bad('⨯ заблокированы (filtered у всех в выборке):')} {CYE}{_ports_compact(blocked)}{CR}")
    # статус: показываем только НАДЁЖНЫЙ блок (поштучный filtered), а не по-хостовой шум
    stat_set(stage=3, blocked=_ports_compact(blocked) if blocked else "", blocked_n=len(blocked))
    # урезаем набор только при ПОШТУЧНОЙ видимости (нет схлопнутых) — иначе можно
    # выкинуть порт, закрытый на выборке, но открытый на других хостах
    if prune and blocked and not total_block and not collapsed_any:
        new_pargs = ["-p", _ports_compact(reachable, cap=10 ** 9)]
        new_plabel = f"-p {_ports_compact(reachable)} ({len(reachable)} шт., отсеяно {len(blocked)})"
        _save_effective(outdir, new_pargs, new_plabel, blocked)
        print(f"   {ok('✔ сканирую только доступные порты:')} {num(len(reachable))} шт.")
        return new_pargs, new_plabel, blocked
    if total_block:
        print(f"   {bad('[!] Ни один хост выборки не ответил (open/closed) — только filtered/тихо.')}")
        print(f"   {dim('похоже на полный фильтр (VPN/фаервол) или хосты не слушают порты. набор не меняю; отключить пробу: --no-preflight')}")
    elif any_filtered:
        print(f"   {dim('часть портов/хостов отвечает filtered (фаервол хоста или провайдер) — набор не меняю, только диагностика')}")
    _save_effective(outdir, pargs, plabel, blocked, note=("полный фильтр" if total_block else ""))
    return pargs, plabel, blocked

def _save_effective(outdir, pargs, plabel, blocked, note=""):
    try:
        (outdir / "ports_effective.json").write_text(json.dumps(
            {"pargs": pargs, "plabel": plabel, "blocked": blocked, "note": note},
            ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

def scan_deep(alive, six, pargs, timing, acc, outdir, meta, do_tcp, do_udp):
    """Этап 3: порты/версии + NSE-уязвимости ОДНИМ проходом, чанки ПАРАЛЛЕЛЬНО.
    CVE появляются по ходу — после каждого чанка, а не в самом конце."""
    fam = "IPv6" if six else "IPv4"
    if len(alive) == 0:
        print(f"   {warn('Живых ' + fam + ' нет — пропуск.')}"); return
    prev = _read_ipset(outdir / "svc_done.txt")
    todo = [h for h in alive if h not in prev]
    total, done, ci = len(alive), len(alive) - len(todo), 0
    if not todo:
        return                                    # всё уже просканировано (resume)
    specs = []
    if do_tcp: specs.append(["-Pn", "-sV", timing, *pargs, "--script", SCRIPTS, "--open", "--host-timeout", "20m"])
    if do_udp: specs.append(["-Pn", "-sU", "-sV", timing, "--top-ports", "50", "--script", SCRIPTS, "--open", "--host-timeout", "20m"])
    nchunks = (len(todo) + CHUNK - 1) // CHUNK
    _STAT.pop("alive", None)                     # убрать залипшее «живых» из discovery
    stat_set(stage=3, phase=f"порты+CVE {fam}", done=done, total=total,
             pstart=time.time(), chunk=0, chunks=nchunks)
    _PSTART[0] = time.time()
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        futs = {ex.submit(_scan_chunk, ch, specs, six): ch for ch in _chunks(todo, CHUNK)}
        for fut in as_completed(futs):
            hosts = fut.result()
            for h in hosts:
                _merge(acc, h)
            _persist_chunk(outdir, "svc_done.txt", futs[fut], hosts)
            done += len(futs[fut]); ci += 1
            _safe_build(outdir, meta, acc)
            nopen = sum(1 for h in acc.values() if h["ports"])
            ncve = sum(len(h["cves"]) for h in acc.values())
            stat_set(stage=3, phase=f"порты+CVE {fam}", done=done, total=total,
                     openh=nopen, cves=ncve, chunk=ci, chunks=nchunks)
            prog(f"порты+CVE {fam}", done, total,
                 f"чанк {CB}{ci}/{nchunks}{CR} · с портами: {num(nopen)} · CVE: {num(ncve)} · {dim('отчёт ⟳')}")
    prog_done()

def _merge(acc, h):
    cur = acc.get(h["ip"])
    if cur is None:
        acc[h["ip"]] = {"ip": h["ip"], "fam": h["fam"],
                        "ports": list(h["ports"]), "cves": dict(h["cves"])}
        return
    seen = {(p, pr) for p, pr, _, _ in cur["ports"]}
    for p in h["ports"]:
        if (p[0], p[1]) not in seen:
            cur["ports"].append(p)
    for cid, v in h["cves"].items():
        cur["cves"][cid] = max(v, cur["cves"].get(cid, 0.0))

# ---------------------------- отчёты ------------------------------------------
def _findings(acc):
    rows = []
    for h in acc.values():
        if not h["ports"]:
            continue
        for cid, v in h["cves"].items():
            rows.append((h["fam"], h["ip"], cid, v, severity(v)))
    return rows

# ------------------- состояние для паузы/продолжения (--resume) ---------------
def _read_ipset(path):
    try:    return set(Path(path).read_text(encoding="utf-8").split())
    except OSError: return set()

def _append_lines(path, lines):
    lines = list(lines)
    if not lines: return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def _dump_host(h):
    return json.dumps({"ip": h["ip"], "fam": h["fam"], "ports": h["ports"], "cves": h["cves"]},
                      ensure_ascii=False)

def _persist_chunk(outdir, done_file, chunk_ips, hosts):
    _append_lines(outdir / done_file, chunk_ips)
    _append_lines(outdir / "results.jsonl", [_dump_host(h) for h in hosts])

def _load_results(outdir):
    acc = {}
    p = outdir / "results.jsonl"
    if not p.exists(): return acc
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip(): continue
        try:
            d = json.loads(line)
            d["ports"] = [tuple(x) for x in d.get("ports", [])]
            d["up"] = True
            _merge(acc, d)
        except (ValueError, KeyError, TypeError):
            continue
    return acc

def _set_phase(outdir, ph):
    try: (outdir / "phase.txt").write_text(ph, encoding="utf-8")
    except OSError: pass

def _get_phase(outdir):
    try: return (outdir / "phase.txt").read_text(encoding="utf-8").strip()
    except OSError: return ""

def _safe_build(outdir, meta, acc):
    """Сбой записи отчёта не должен ронять скан — гасим исключение."""
    try:
        build_reports(outdir, meta, acc)
    except Exception:
        pass

def build_reports(outdir, meta, acc):
    rows = _findings(acc)
    counts = {s: sum(1 for r in rows if r[4] == s) for s in ("Critical", "High", "Medium", "Low")}
    vuln_hosts = len({r[1] for r in rows})
    top = sorted({(r[1], r[0]): 0 for r in rows}.keys(),
                 key=lambda k: max((r[3] for r in rows if r[1] == k[0]), default=0), reverse=True)[:5]
    top = [(max(r[3] for r in rows if r[1] == ip), ip, fam) for ip, fam in top]
    withports = sorted((h for h in acc.values() if h["ports"]),
                       key=lambda h: max(h["cves"].values(), default=0), reverse=True)
    _write_md(outdir, meta, counts, vuln_hosts, top, withports)
    _write_csv_json(outdir, rows)
    _write_html(outdir, meta, counts, vuln_hosts, top, withports, rows)

def _host_sev(h):
    m = max(h["cves"].values(), default=0.0)
    return (severity(m), m if h["cves"] else "—")

def _write_md(outdir, m, counts, vuln_hosts, top, withports):
    L = []
    L.append("# Отчёт аудита безопасности\n")
    L.append(f"**Дата:** {m['stamp']}  ")
    L.append(f"**Источник целей:** `{m['src']}`  ")
    L.append(f"**Охват портов:** `{m['ports']}`\n\n---\n")
    L.append("## Сводка\n")
    L.append("| Показатель | IPv4 | IPv6 |")
    L.append("|:-----------|:----:|:----:|")
    L.append(f"| Целей в списке | {m['v4n']} | {m['v6n']} |")
    L.append(f"| Живых хостов   | {m['a4n']} | {m['a6n']} |\n")
    L.append("## Итог (executive summary)\n")
    L.append("| Уровень | Находок |")
    L.append("|:--------|:-------:|")
    L.append(f"| 🔴 Critical | {counts['Critical']} |")
    L.append(f"| 🟠 High | {counts['High']} |")
    L.append(f"| 🟡 Medium | {counts['Medium']} |")
    L.append(f"| 🟢 Low | {counts['Low']} |\n")
    L.append(f"**Хостов с уязвимостями:** {vuln_hosts}\n")
    if top:
        L.append("**Топ-риск хосты:**\n")
        L.append("| CVSS | Хост | Семейство |")
        L.append("|:----:|:-----|:---------:|")
        for cvss, ip, fam in top:
            L.append(f"| {cvss} | {ip} | {fam} |")
        L.append("")
    L.append("**Шкала критичности (CVSS):** Critical ≥ 9.0 · High 7.0–8.9 · Medium 4.0–6.9 · Low < 4.0\n\n---\n")
    L.append("# Находки по хостам\n")
    for fam in ("IPv4", "IPv6"):
        L.append(f"## Семейство {fam}\n")
        fam_hosts = [h for h in withports if h["fam"] == fam]
        if not fam_hosts:
            L.append("_хостов с открытыми портами нет_\n\n---\n"); continue
        for h in fam_hosts:
            sev, mx = _host_sev(h)
            L.append(f"### {h['ip']} — критичность: {sev} (CVSS {mx})\n")
            L.append("**Открытые порты и сервисы**\n\n```")
            for p in sorted(h["ports"]):
                L.append(f"{p[0]}/{p[1]:<3} open  {p[2]:<8} {p[3]}")
            L.append("```\n")
            if h["cves"]:
                L.append("**Уязвимости (CVE / CVSS)**\n")
                L.append("| CVE | CVSS | Критичность |")
                L.append("|:----|:----:|:-----------:|")
                for cid, v in sorted(h["cves"].items(), key=lambda kv: kv[1], reverse=True):
                    L.append(f"| {cid} | {v} | {severity(v)} |")
                L.append("")
            else:
                L.append("_Явных CVE не найдено._\n")
            L.append("**Автооценка**\n")
            L.append(f"- Критичность: **{sev}** (макс. CVSS {mx})")
            L.append(f"- Риск: {risk_txt(sev)}")
            L.append(f"- Рекомендация: {recommend(sev)}\n\n---\n")
    L.append("## Проверка перед сдачей\n")
    L.append("- CVE, CVSS и критичность проставлены **автоматически** (nmap-скрипт `vulners`).")
    L.append("- Рекомендуется подтвердить находки вручную: у сетевых сканеров бывают ложные срабатывания.")
    (outdir / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

def _write_csv_json(outdir, rows):
    with open(outdir / "findings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["family", "host", "cve", "cvss", "severity"])
        for r in rows: w.writerow(r)
    data = [{"family": r[0], "host": r[1], "cve": r[2], "cvss": r[3], "severity": r[4]} for r in rows]
    (outdir / "findings.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _write_html(outdir, m, counts, vuln_hosts, top, withports, rows):
    e = html.escape
    P = []
    P.append(HTML_HEAD)
    P.append(f"<div class='hero'><h1>🛡 Отчёт аудита безопасности</h1>"
             f"<div class='meta'>Дата: {e(m['stamp'])} · Источник: {e(str(m['src']))} · Порты: {e(m['ports'])}</div></div>")
    P.append("<h2>Сводка</h2><table><tr><th>Показатель</th><th>IPv4</th><th>IPv6</th></tr>"
             f"<tr><td>Целей в списке</td><td>{m['v4n']}</td><td>{m['v6n']}</td></tr>"
             f"<tr><td>Живых хостов</td><td>{m['a4n']}</td><td>{m['a6n']}</td></tr></table>")
    P.append("<h2>Итог</h2><p>"
             f"<span class='stat'><span class='badge crit'>Critical</span> {counts['Critical']}</span>"
             f"<span class='stat'><span class='badge high'>High</span> {counts['High']}</span>"
             f"<span class='stat'><span class='badge med'>Medium</span> {counts['Medium']}</span>"
             f"<span class='stat'><span class='badge low'>Low</span> {counts['Low']}</span></p>")
    P.append(f"<p>Хостов с уязвимостями: <b>{vuln_hosts}</b></p>")
    if top:
        P.append("<h3>Топ-риск хосты</h3><table><tr><th>CVSS</th><th>Хост</th><th>Тип</th></tr>")
        for cvss, ip, fam in top:
            P.append(f"<tr><td>{cvss}</td><td>{e(ip)}</td><td>{fam}</td></tr>")
        P.append("</table>")
    # индекс всех CVE
    cve_agg = {}
    for fam, ip, cid, v, sev in rows:
        cve_agg.setdefault(cid, [v, 0])[1] += 1
        cve_agg[cid][0] = max(cve_agg[cid][0], v)
    if cve_agg:
        P.append(f"<details class='cvebox' open><summary>Все найденные CVE ({len(cve_agg)}) — клик по CVE фильтрует хосты ниже</summary><div class='cvelist'>")
        for cid, (v, n) in sorted(cve_agg.items(), key=lambda kv: kv[1][0], reverse=True):
            P.append(f"<button class='chip cve' data-cve='{e(cid)}'>{e(cid)} "
                     f"<span class='badge {css_class(severity(v))}'>CVSS {v}</span> · {n} хост.</button>")
        P.append("</div></details>")
    P.append(HTML_FILTERBAR)
    for fam in ("IPv4", "IPv6"):
        for h in (x for x in withports if x["fam"] == fam):
            P.append(_html_card(h, e))
    P.append("<div class='empty' id='nomatch' style='display:none'>ничего не найдено под фильтр</div></div>")
    P.append("<h2>Проверка перед сдачей</h2><ul>"
             "<li>CVE/CVSS/критичность проставлены автоматически (nmap vulners).</li>"
             "<li>Подтвердить вручную — возможны ложные срабатывания.</li></ul>")
    P.append(HTML_TAIL)
    (outdir / "report.html").write_text("\n".join(P), encoding="utf-8")

def _html_card(h, e):
    sev, mx = _host_sev(h)
    out = [f"<div class='card host-card' data-fam='{h['fam']}' data-sev='{sev}'>"]
    out.append(f"<h3><span class='hname'>{e(h['ip'])}</span> <span class='badge fam'>{h['fam']}</span> "
               f"<span class='badge {css_class(sev)}'>{sev} · CVSS {mx}</span></h3>")
    out.append("<h4>Открытые порты и сервисы</h4><pre>")
    for p in sorted(h["ports"]):
        out.append(e(f"{p[0]}/{p[1]:<3} open  {p[2]:<8} {p[3]}"))
    out.append("</pre>")
    if h["cves"]:
        out.append("<h4>Уязвимости (CVE / CVSS)</h4><table><tr><th>CVE</th><th>CVSS</th><th>Критичность</th></tr>")
        for cid, v in sorted(h["cves"].items(), key=lambda kv: kv[1], reverse=True):
            out.append(f"<tr><td><a href='https://nvd.nist.gov/vuln/detail/{e(cid)}' target='_blank' "
                       f"rel='noopener'>{e(cid)}</a></td><td>{v}</td>"
                       f"<td><span class='badge {css_class(severity(v))}'>{severity(v)}</span></td></tr>")
        out.append("</table>")
    else:
        out.append("<p class='muted'>Явных CVE не найдено.</p>")
    out.append(f"<h4>Автооценка</h4><ul><li>Критичность: <b>{sev}</b> (макс. CVSS {mx})</li>"
               f"<li>Риск: {risk_txt(sev)}</li><li>Рекомендация: {recommend(sev)}</li></ul></div>")
    return "\n".join(out)

# ---------------------- демон / статус / стоп ----------------------------------
def latest_dir():
    d = sorted(Path(".").glob("audit_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return d[0] if d else None

def _fmt_dur(sec):
    sec = int(sec); h, sec = divmod(sec, 3600); mm, ss = divmod(sec, 60)
    return f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"

def _proc_running(d):
    pidf = d / "audit.pid"
    if not pidf.exists():
        return False, None
    try:
        pv = int(pidf.read_text()); os.kill(pv, 0); return True, pv
    except (OSError, ValueError):
        try: return False, int(pidf.read_text())
        except (OSError, ValueError): return False, None

def _render_status(d):
    L = [f"{CB}══ Статус аудита: {d} ══{CR}"]
    running, pv = _proc_running(d)
    L.append("Состояние: " + (f"{CGR}● РАБОТАЕТ{CR} (PID {pv})" if running
                              else f"{CYE}○ завершён / не запущен{CR}"))
    st = {}
    sj = d / "status.json"
    if sj.exists():
        try: st = json.loads(sj.read_text(encoding="utf-8"))
        except (OSError, ValueError): st = {}
    if st:
        stage = st.get("stage", "?"); phase = st.get("phase", "")
        done, total = st.get("done", 0), st.get("total", 0)
        w = 30; t = max(total, 1); f = min(done, t) * w // t; pct = min(done, t) * 100 // t
        if phase == "готово": pct, f = 100, w
        bar = f"{CGR}{'█'*f}{CGY}{'░'*(w-f)}{CR}"
        now = time.time()
        if st.get("finished"):
            el_val = st["finished"] - st.get("started", now); eta = "0:00"
        else:
            ps = st.get("pstart", st.get("started", now)); el_val = now - ps
            eta = "~" + _fmt_dur(el_val / done * (total - done)) if 0 < done < total else "—"
        idle = int(now - st.get("updated", now))
        extra = []
        if "alive" in st: extra.append(f"живых: {CGR}{st['alive']}{CR}")
        if "openh" in st: extra.append(f"с портами: {CCY}{st['openh']}{CR}")
        if "cves"  in st: extra.append(f"CVE: {CYE}{st['cves']}{CR}")
        blk = st.get("blocked")
        try: sn = int(stage)
        except (ValueError, TypeError): sn = 0
        L.append(f"{CB}Этап {stage}/4{CR} · {phase}")
        # (1) общий прогресс по этапам
        L.append(f"  {dim('общий')} [{make_bar(sn * w // 4, w)}] {CB}{CYE}{sn*100//4}%{CR}  этап {CCY}{stage}/4{CR}")
        # (2) прогресс текущего этапа (хосты, время, ETA)
        L.append(f"  {dim('этап ')} [{bar}] {CB}{CYE}{pct}%{CR}  {CCY}{done}/{total}{CR}  "
                 f"прошло {CGR}{_fmt_dur(el_val)}{CR}  ост {CMA}{eta}{CR}"
                 + ("  " + "  ".join(extra) if extra else ""))
        # (3) прогресс по чанкам текущего этапа
        if st.get("chunks"):
            ck, cks = st.get("chunk", 0), max(st.get("chunks", 1), 1)
            L.append(f"  {dim('чанки')} [{make_bar(min(ck, cks) * w // cks, w)}] "
                     f"{CB}{CYE}{min(ck, cks) * 100 // cks}%{CR}  {CCY}{ck}/{cks}{CR} чанков")
            if running:
                L.append(f"  {dim('⟳ отчёт обновляется после каждого чанка → report.html')}")
        if blk:
            L.append(f"  {CRD}⨯ порты режет провайдер/фаервол ({st.get('blocked_n', '?')}):{CR} {CYE}{blk}{CR}")
        if running and idle > 60:
            L.append(f"  {CYE}(последнее обновление {idle}с назад){CR}")
    rep = d / "report.md"; fc = d / "findings.csv"
    if rep.exists():
        nf = max(0, sum(1 for _ in open(fc, encoding="utf-8")) - 1) if fc.exists() else 0
        L.append(f"{CGR}Отчёт готов:{CR} {rep}  (находок CVE: {nf})  ·  html: {d}/report.html")
    else:
        L.append("Отчёт ещё не готов")
    log = d / "run.log"
    if log.exists():
        raw = [l for l in log.read_text(errors="ignore").splitlines()
               if l.strip() and not re.fullmatch(r"[─━═│┌┐└┘▶\s]+", l)]
        if raw:
            L.append(f"{CGY}── лог ──{CR}")
            L.extend(raw[-6:])
    return L

def do_status(arg):
    d = Path(arg) if arg else latest_dir()
    if not d or not d.is_dir():
        print("[!] Папка прогона не найдена. Укажи: --status audit_<дата>"); return
    print("\n".join(_render_status(d)))

def do_status_live(arg):
    d = Path(arg) if arg else latest_dir()
    if not d or not d.is_dir():
        print("[!] Папка прогона не найдена. Укажи: --status-live audit_<дата>"); return
    set_colors(True)                      # в живом просмотре цвета всегда включены
    # альтернативный экран терминала (как top/htop) — чистая перерисовка без спама
    sys.stdout.write("\033[?1049h\033[?25l"); sys.stdout.flush()
    try:
        while True:
            frame = _render_status(d)
            frame.append("")
            frame.append(dim("(живой просмотр · обновление 2с · Ctrl+C — выйти, демон продолжит)"))
            sys.stdout.write("\033[H\033[2J" + "\n".join(frame) + "\n")   # домой + очистка + кадр
            sys.stdout.flush()
            running, _ = _proc_running(d)
            if (d / "status.json").exists() and not running:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[?1049l"); sys.stdout.flush()   # вернуть курсор и обычный экран
    running, _ = _proc_running(d)
    if (d / "status.json").exists() and not running:
        print(f"{CB}{CGR}[✓] Прогон завершён.{CR}")
    else:
        print(dim("[i] Просмотр закрыт — демон продолжает работать. Остановить: python3 audit.py --stop"))

def do_stop(arg):
    d = Path(arg) if arg else latest_dir()
    if not d or not (d / "audit.pid").exists():
        print("[!] pid-файл не найден."); return
    pid = int((d / "audit.pid").read_text())
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        print(f"[✓] Остановлен фоновый аудит PID {pid} ({d})")
    except OSError:
        print(f"[i] Процесс {pid} уже не активен ({d})")

ALIAS_MARK = ("# >>> audit.py aliases >>>", "# <<< audit.py aliases <<<")

def _alias_block():
    sp = str(Path(__file__).resolve())
    return (f"{ALIAS_MARK[0]}\n"
            f"alias audit='python3 \"{sp}\"'\n"
            f"alias astatus='python3 \"{sp}\" --status'\n"
            f"alias awatch='python3 \"{sp}\" --status-live'\n"
            f"alias astop='python3 \"{sp}\" --stop'\n"
            f"{ALIAS_MARK[1]}")

def _strip_aliases(text):
    return re.sub(re.escape(ALIAS_MARK[0]) + r".*?" + re.escape(ALIAS_MARK[1]) + r"\n?",
                  "", text, flags=re.S)

def ensure_aliases():
    rc = Path.home() / ".bashrc"
    try:
        cur = rc.read_text(encoding="utf-8") if rc.exists() else ""
    except OSError:
        return
    want = _alias_block()
    if want in cur:                                   # уже актуальны
        return
    new = _strip_aliases(cur).rstrip("\n")            # снести старый блок (если есть)
    new = (new + "\n\n" + want + "\n") if new.strip() else (want + "\n")
    try:
        rc.write_text(new, encoding="utf-8")
        print(f"[i] Алиасы audit/astatus/awatch/astop записаны в {rc} (примени: source ~/.bashrc)")
    except OSError:
        pass

def remove_aliases():
    rc = Path.home() / ".bashrc"
    if not rc.exists() or ALIAS_MARK[0] not in rc.read_text(encoding="utf-8"):
        print("[i] Алиасы audit.py в ~/.bashrc не найдены."); return
    rc.write_text(_strip_aliases(rc.read_text(encoding="utf-8")).rstrip("\n") + "\n", encoding="utf-8")
    print(f"[✓] Алиасы audit.py удалены из {rc} (примени: source ~/.bashrc)")

def do_install():
    print(f"{CB}{CCY}▶ Установка зависимостей (nmap)...{CR}")
    sudo = [] if (hasattr(os, "geteuid") and os.geteuid() == 0) else (["sudo"] if shutil.which("sudo") else [])
    if shutil.which("apt-get"):
        subprocess.run(sudo + ["apt-get", "update", "-y"])
        subprocess.run(sudo + ["apt-get", "install", "-y", "nmap"])
    elif shutil.which("dnf"):
        subprocess.run(sudo + ["dnf", "install", "-y", "nmap"])
    elif shutil.which("pacman"):
        subprocess.run(sudo + ["pacman", "-Sy", "--noconfirm", "nmap"])
    else:
        print(f"{bad('[!]')} Пакетный менеджер не распознан — поставь nmap вручную.")
    ensure_aliases()
    print(f"{CB}{CGR}✔ Готово.{CR} Проверь: {num('nmap --version')} ; примени алиасы: {num('source ~/.bashrc')}")

def check_deps():
    if not shutil.which("nmap"):
        print(f"{CRD}[!] nmap не найден — установи: python3 audit.py --install{CR}"); sys.exit(1)
    ver = subprocess.run(["nmap", "--version"], capture_output=True, text=True).stdout.splitlines()
    print(f"{CGR}[✓]{CR} nmap: {ver[0] if ver else 'ok'}")

# ------------------------------ CLI / main -------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="audit.py", add_help=True,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Учебный аудитор сети: разведка -> сервисы -> уязвимости -> отчёты.",
        epilog=(
            "ВЫБОР ПОРТОВ:\n"
            "  (без ключа)   топ-100 популярных портов\n"
            "  --top N       топ-N популярных портов\n"
            "  -A, --all     ВСЕ 65535 портов (долго)\n"
            "  -p СПИСОК     только указанные, напр. -p 22,80,443\n\n"
            "СКОРОСТЬ:\n"
            "  -T 0..5       тайминг nmap (по умолч. 4); меньше = тише/медленнее\n"
            "  -j, --jobs N  сколько чанков сканировать параллельно (по умолч. 4)\n"
            "  (мёртвые хосты по умолчанию не переспрашиваются: --max-retries 1)\n\n"
            "ПРОТОКОЛЫ (что сканировать):\n"
            "  (без ключа)   TCP + UDP\n"
            "  --no-udp      только TCP (заметно быстрее — UDP самый долгий)\n"
            "  --no-tcp      только UDP\n"
            "  пример «все TCP без UDP»:  --all --no-udp\n\n"
            "РАЗВЕДКА:\n"
            "  --Pn          пропустить host discovery — все цели считать живыми\n\n"
            "ПОРТЫ (проба доступности):\n"
            "  (по умолчанию) перед сканом пробуем порты на выборке живых хостов\n"
            "                 и отсеиваем те, что режет провайдер/VPN (filtered)\n"
            "  --no-preflight отключить пробу (сканировать весь набор как есть)\n\n"
            "УЯЗВИМОСТИ (CVE):\n"
            "  порты и уязвимости ищутся ОДНИМ проходом (этап 3): CVE появляются\n"
            "  по ходу — после каждого чанка, а не в самом конце\n\n"
            "ФОН (демон):\n"
            "  -d              запустить в фоне\n"
            "  --status [п]    показать статус один раз\n"
            "  --status-live   живой просмотр (онлайн, Ctrl+C выходит, демон продолжает)\n"
            "  --stop [п]      остановить фоновый аудит\n"
            "  --resume [п]    продолжить прерванный прогон с последнего места\n\n"
            "ПРОЧЕЕ:\n"
            "  --install     поставить nmap + алиасы (audit/astatus/awatch/astop)\n"
            "  --uninstall   убрать алиасы audit.py из ~/.bashrc\n\n"
            "ФОРМАТЫ ЦЕЛЕЙ (файл, по одной на строку, '#'=комментарий):\n"
            "  192.0.2.10 · 192.0.2.0/24 · 192.0.2.10-50 · 192.0.2.0-192.0.2.255\n"
            "  2001:db8::1 · 2a09:d280::-2a09:d287:ffff:... (огромные IPv6 → базовый адрес)\n\n"
            "РЕЗУЛЬТАТ: папка audit_<дата>/ (report.md, report.html, findings.csv/json)\n"
            "ВНИМАНИЕ: сканируй только то, на что есть разрешение.\n"),
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--top", type=int, nargs="?", const=100, metavar="N")
    g.add_argument("-A", "--all", action="store_true")
    g.add_argument("-p", "--ports", metavar="СПИСОК")
    p.add_argument("-T", "--timing", type=int, choices=range(0, 6), default=4, metavar="0..5")
    p.add_argument("--no-udp", dest="no_udp", action="store_true", help="не сканировать UDP")
    p.add_argument("--no-tcp", dest="no_tcp", action="store_true", help="не сканировать TCP (только UDP)")
    p.add_argument("-j", "--jobs", type=int, default=PARALLEL, metavar="N",
                   help=f"сколько чанков сканировать параллельно (по умолчанию {PARALLEL})")
    p.add_argument("--Pn", "--skip-discovery", dest="skip_disc", action="store_true",
                   help="пропустить host discovery — считать все цели живыми (как nmap -Pn)")
    p.add_argument("--no-preflight", dest="no_preflight", action="store_true",
                   help="не проверять доступность портов перед сканом (без отсева фильтрации)")
    p.add_argument("--resume", nargs="?", const="", metavar="ПАПКА",
                   help="продолжить прерванный прогон (папка audit_<дата> или последний)")
    p.add_argument("-d", "--daemon", action="store_true")
    p.add_argument("--status", nargs="?", const="", metavar="ПАПКА")
    p.add_argument("--status-live", dest="status_live", nargs="?", const="", metavar="ПАПКА")
    p.add_argument("--stop", nargs="?", const="", metavar="ПАПКА")
    p.add_argument("--install", action="store_true")
    p.add_argument("--uninstall", action="store_true", help="убрать алиасы audit.py из ~/.bashrc")
    p.add_argument("targets", nargs="?", metavar="файл_целей")
    return p

def port_args(a):
    if a.all:  return ["-p-"], "-p-"
    if a.ports: return ["-p", a.ports], f"-p {a.ports}"
    n = a.top if a.top else 100
    return ["--top-ports", str(n)], f"--top-ports {n}"

def main():
    try:                                   # живой лог в файл (демон) без буферизации
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    a = build_parser().parse_args()
    if a.install: do_install(); return
    if a.uninstall: remove_aliases(); return
    if a.status is not None: do_status(a.status); return
    if a.status_live is not None: do_status_live(a.status_live); return
    if a.stop   is not None: do_stop(a.stop); return

    worker = os.environ.get("AUDIT_WORKER") == "1"
    resume = (a.resume is not None) or os.environ.get("AUDIT_RESUME") == "1"
    if not worker:
        ensure_aliases()
    else:
        set_colors(True)          # цветной run.log → цветной хвост в --status-live
    check_deps()
    if a.jobs and a.jobs > 0:
        globals()["PARALLEL"] = a.jobs

    if resume:
        rdir = os.environ.get("AUDIT_OUT") or a.resume
        outdir = Path(rdir) if rdir else latest_dir()
        if not outdir or not outdir.is_dir() or not (outdir / "opts.json").exists():
            print(f"{bad('[!]')} для --resume нужна папка прогона с opts.json (укажи: --resume audit_<дата>)"); sys.exit(1)
        o = json.loads((outdir / "opts.json").read_text(encoding="utf-8"))
        src, pargs, plabel = o["src"], o["pargs"], o["plabel"]
        timing, do_tcp, do_udp, skip_disc = o["timing"], o["do_tcp"], o["do_udp"], o["skip_disc"]
        do_preflight = o.get("preflight", True)
    else:
        src = a.targets
        if not src:
            if a.daemon or not sys.stdin.isatty():
                print(f"{bad('[!]')} укажи файл целей: python3 audit.py [ключи] targets.txt"); sys.exit(1)
            src = input("Укажи путь до файла со списком целей: ").strip()
        while not src or not Path(src).is_file():
            if not sys.stdin.isatty():
                print(f"{bad('[!]')} файл целей '{src}' не найден"); sys.exit(1)
            src = input(f"[!] Файл '{src}' не найден. Введи путь заново: ").strip()
        pargs, plabel = port_args(a)
        timing = f"-T{a.timing}"
        do_tcp, do_udp = not a.no_tcp, not a.no_udp
        if not do_tcp and not do_udp:
            print(f"{bad('[!]')} нельзя одновременно --no-tcp и --no-udp — нечего сканировать"); sys.exit(1)
        skip_disc = a.skip_disc
        do_preflight = not a.no_preflight
        outdir = Path(os.environ.get("AUDIT_OUT") or f"audit_{datetime.now():%Y%m%d_%H%M%S}")
        outdir.mkdir(exist_ok=True)
        (outdir / "opts.json").write_text(json.dumps({
            "src": str(src), "pargs": pargs, "plabel": plabel, "timing": timing,
            "do_tcp": do_tcp, "do_udp": do_udp, "skip_disc": skip_disc,
            "preflight": do_preflight}, ensure_ascii=False), encoding="utf-8")

    # --- демон: перезапуск в фоне ---
    if a.daemon and not worker:
        env = dict(os.environ, AUDIT_WORKER="1", AUDIT_OUT=str(outdir), AUDIT_RESUME=("1" if resume else "0"))
        log = open(outdir / "run.log", "ab" if resume else "wb")
        subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__)] + sys.argv[1:],
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=env)
        tag = "Продолжен" if resume else "Запущен"
        print(f"{CB}{CGR}✔ {tag} в фоне.{CR} Папка: {hot(str(outdir))}")
        print(f"    живой просмотр: {num('python3 audit.py --status-live')}")
        print(f"    стоп:           {num('python3 audit.py --stop ' + str(outdir))}")
        print(f"    продолжить:     {num('python3 audit.py -d --resume ' + str(outdir))}")
        print(f"    {dim('можно закрывать терминал — аудит продолжится.')}")
        return
    if worker:
        (outdir / "audit.pid").write_text(str(os.getpid()))

    run_pipeline(src, outdir, pargs, plabel, timing, do_tcp, do_udp, skip_disc, do_preflight, resume)

def run_pipeline(src, outdir, pargs, plabel, timing, do_tcp, do_udp, skip_disc, do_preflight=True, resume=False):
    stamp = outdir.name.replace("audit_", "")
    phase = _get_phase(outdir) if resume else ""
    acc = _load_results(outdir) if resume else {}
    stat_set(outdir=outdir, started=time.time(), stage=1,
             phase="подготовка списка", done=0, total=0)

    logo()
    if resume:
        print(f"   {warn('▶ ПРОДОЛЖЕНИЕ (resume) с фазы: ' + (phase or 'начало') + f', загружено хостов: {len(acc)}')}")
    banner("Этап 1/4 — подготовка списка (валидация + деление v4/v6)")
    v4, v6, notes = parse_targets(src)
    for note in notes[:10]:
        print(f"   {dim('[i] ' + note)}")
    print(f"   Валидных целей к сканированию: IPv4 {num(len(v4))} {dim('|')} IPv6 {num(len(v6))}")
    print(f"   {dim('оптимизация: --max-retries 1 + параллельные чанки (jobs=' + str(PARALLEL) + ')')}")
    overall(1)

    banner("Этап 2/4 — host discovery (живые / мёртвые)")
    if skip_disc:
        alive4, alive6 = list(v4), list(v6)
        print(f"   {warn('--Pn: discovery пропущен — все ' + str(len(alive4) + len(alive6)) + ' целей считаем живыми')}")
    elif resume and phase in ("services", "vulns", "done"):
        alive4 = list(_read_ipset(outdir / "alive_v4.txt"))
        alive6 = list(_read_ipset(outdir / "alive_v6.txt"))
        print(f"   {dim('discovery из прошлого прогона: живых IPv4 ' + str(len(alive4)) + ' | IPv6 ' + str(len(alive6)))}")
    else:
        _set_phase(outdir, "discovery")
        alive4 = discover(v4, False, timing, outdir)
        alive6 = discover(v6, True, timing, outdir)
        print(f"   Живых: IPv4 {ok(len(alive4))} {dim('|')} IPv6 {ok(len(alive6))}")
    overall(2)

    meta = {"stamp": stamp, "src": str(src), "ports": plabel,
            "v4n": len(v4), "v6n": len(v6), "a4n": len(alive4), "a6n": len(alive6)}
    _safe_build(outdir, meta, acc)                 # отчёт сразу (с тем, что уже накоплено)

    # --- префлайт: отсев портов, которые режет провайдер/VPN (только TCP) ---
    eff = outdir / "ports_effective.json"
    if resume and eff.exists():
        o = json.loads(eff.read_text(encoding="utf-8"))
        pargs, plabel = o["pargs"], o["plabel"]
        meta["ports"] = plabel
        if o.get("blocked"):
            stat_set(blocked=_ports_compact([int(x) for x in o["blocked"]]), blocked_n=len(o["blocked"]))
            print(f"   {dim('префлайт из прошлого прогона: заблокировано портов ' + str(len(o['blocked'])) + ' — учтено')}")
    elif do_preflight and do_tcp and (alive4 or alive6):
        banner("Проба портов — что реально доступно (отсев фильтрации провайдера/VPN)")
        pargs, plabel, blocked = preflight_ports(alive4, alive6, pargs, plabel, timing, outdir)
        meta["ports"] = plabel                         # в отчёт попадёт фактический набор
        _safe_build(outdir, meta, acc)

    mode = "TCP + UDP" if do_tcp and do_udp else ("только TCP" if do_tcp else "только UDP")
    # этап 3: порты/версии + NSE-уязвимости одним проходом — CVE идут по ходу
    banner(f"Этап 3/4 — порты + уязвимости (CVE по ходу) ({mode}), чанками по {CHUNK}")
    if resume and phase == "done":
        print(f"   {dim('этап уже пройден (resume) — пропуск')}")
    else:
        _set_phase(outdir, "services")
        scan_deep(alive4, False, pargs, timing, acc, outdir, meta, do_tcp, do_udp)
        scan_deep(alive6, True,  pargs, timing, acc, outdir, meta, do_tcp, do_udp)
    nopen = sum(1 for h in acc.values() if h["ports"])
    ncve = sum(len(h["cves"]) for h in acc.values())
    print(f"   Хостов с портами: {num(nopen)} {dim('·')} CVE-находок: {num(ncve)}")
    overall(3)

    banner("Этап 4/4 — сборка отчёта (md · html · csv · json)")
    _set_phase(outdir, "done")
    build_reports(outdir, meta, acc)
    stat_set(stage=4, phase="готово", done=1, total=1, cves=ncve, finished=time.time())
    overall(4)

    print(f"\n{CB}{CGR}✔ Готово!{CR} Артефакты в {hot(str(outdir) + '/')} :")
    print(f"    {num('report.md')}     {dim('— отчёт (Markdown)')}")
    print(f"    {num('report.html')}   {dim('— отчёт (браузер: фильтры + список CVE)')}")
    print(f"    {num('findings.csv')}  {dim('— находки (таблица)')}")
    print(f"    {num('findings.json')} {dim('— находки (JSON)')}")

# --------------------------- HTML-шаблоны -------------------------------------
HTML_HEAD = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Отчёт аудита</title>
<style>
:root{--bg:#f6f7fb;--fg:#16181d;--card:#fff;--bd:#e2e5ec;--muted:#6b7280;--accent:#5b7cfa}
@media(prefers-color-scheme:dark){:root{--bg:#0f1116;--fg:#e7e9ee;--card:#171a21;--bd:#2a2f3a;--muted:#9aa1ad;--accent:#7c95ff}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,Segoe UI,Roboto,sans-serif;margin:0;padding:0 20px 60px}
.wrap{max-width:1040px;margin:auto}
.hero{background:linear-gradient(120deg,#5b7cfa,#9b5bfa 55%,#e4572e);color:#fff;border-radius:0 0 18px 18px;padding:26px 24px;margin:0 -20px 18px;box-shadow:0 8px 30px rgba(0,0,0,.18)}
.hero h1{margin:0 0 6px;font-size:1.7em}.hero .meta{opacity:.92;font-size:.9em}
h2{border-bottom:2px solid var(--bd);padding-bottom:.25em;margin-top:1.7em}
table{border-collapse:collapse;width:100%;margin:.5em 0}th,td{border:1px solid var(--bd);padding:6px 10px;text-align:left}
th{background:rgba(128,128,128,.12)}
pre{background:rgba(128,128,128,.10);padding:10px;border-radius:8px;overflow-x:auto;font-size:.9em}
.muted{color:var(--muted)}a{color:var(--accent)}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.78em;font-weight:700;color:#fff;vertical-align:middle}
.crit{background:#b10f2e}.high{background:#e4572e}.med{background:#c99700}.low{background:#3a8a3a}.info{background:#6c7684}.fam{background:#3d5a80}
.stat{display:inline-flex;gap:6px;align-items:center;margin-right:12px}
.filterbar{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--bd);padding:12px 0;margin:6px 0 14px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;backdrop-filter:blur(6px)}
.fgroup{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.lbl{color:var(--muted);font-size:.82em}
.chip{border:1px solid var(--bd);background:var(--card);color:var(--fg);padding:5px 13px;border-radius:999px;cursor:pointer;font-size:.83em;opacity:.5;transition:.15s;user-select:none}
.chip:hover{transform:translateY(-1px)}.chip.on{opacity:1;font-weight:700}
.chip.crit.on{background:#b10f2e;color:#fff;border-color:#b10f2e}.chip.high.on{background:#e4572e;color:#fff;border-color:#e4572e}
.chip.med.on{background:#c99700;color:#fff;border-color:#c99700}.chip.low.on{background:#3a8a3a;color:#fff;border-color:#3a8a3a}.chip.info.on{background:#6c7684;color:#fff;border-color:#6c7684}
.search{flex:1;min-width:200px;padding:7px 13px;border:1px solid var(--bd);border-radius:10px;background:var(--card);color:var(--fg);font-size:.9em}
.count{color:var(--muted);font-size:.83em;margin-left:auto;white-space:nowrap}
.card{background:var(--card);border:1px solid var(--bd);border-left:5px solid var(--accent);border-radius:12px;padding:16px 18px;margin:14px 0;transition:.15s;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.card:hover{transform:translateY(-2px);box-shadow:0 6px 22px rgba(0,0,0,.13)}
.host-card[data-sev="Critical"]{border-left-color:#b10f2e}.host-card[data-sev="High"]{border-left-color:#e4572e}
.host-card[data-sev="Medium"]{border-left-color:#c99700}.host-card[data-sev="Low"]{border-left-color:#3a8a3a}.host-card[data-sev="Info"]{border-left-color:#6c7684}
.card h3{margin:.1em 0 .6em}.card h4{margin:.8em 0 .3em;font-size:.9em;color:var(--muted)}
.hname{font-family:ui-monospace,Consolas,monospace}.empty{padding:24px;text-align:center;color:var(--muted)}
.cvebox{margin:14px 0;border:1px solid var(--bd);border-radius:12px;background:var(--card);padding:10px 14px}
.cvebox summary{cursor:pointer;font-weight:700}.cvelist{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip.cve{opacity:1;font-weight:600}.chip.cve.act{outline:2px solid var(--accent);border-color:var(--accent)}
</style></head><body><div class="wrap">"""

HTML_FILTERBAR = """<h2>Хосты</h2>
<div class="filterbar">
  <div class="fgroup"><span class="lbl">Критичность:</span>
    <button class="chip crit on" data-sev="Critical">Critical</button>
    <button class="chip high on" data-sev="High">High</button>
    <button class="chip med on" data-sev="Medium">Medium</button>
    <button class="chip low on" data-sev="Low">Low</button>
    <button class="chip info on" data-sev="Info">Info</button>
  </div>
  <div class="fgroup"><span class="lbl">Тип:</span>
    <button class="chip on" data-fam="IPv4">IPv4</button>
    <button class="chip on" data-fam="IPv6">IPv6</button>
  </div>
  <input id="q" class="search" placeholder="поиск: хост / CVE / сервис…">
  <button id="reset" class="chip on">сброс</button>
  <span class="count">показано <b id="shown">0</b> из <b id="total">0</b></span>
</div>
<div id="cards">"""

HTML_TAIL = """</div>
<script>
(function(){
  var cards=[].slice.call(document.querySelectorAll('.host-card'));
  var sev={Critical:1,High:1,Medium:1,Low:1,Info:1}, fam={IPv4:1,IPv6:1}, cve={}, q='';
  var shown=document.getElementById('shown'), nomatch=document.getElementById('nomatch');
  document.getElementById('total').textContent=cards.length;
  function cveOn(){for(var k in cve)if(cve[k])return true;return false;}
  function apply(){
    var n=0, ca=cveOn();
    cards.forEach(function(c){
      var t=c.textContent, tl=t.toLowerCase();
      var okc=!ca; if(ca){for(var k in cve){if(cve[k]&&t.indexOf(k)>-1){okc=true;break;}}}
      var ok=sev[c.dataset.sev]&&fam[c.dataset.fam]&&okc&&(q===''||tl.indexOf(q)>-1);
      c.style.display=ok?'':'none'; if(ok)n++;
    });
    shown.textContent=n; nomatch.style.display=n?'none':'';
  }
  function bind(sel,map){document.querySelectorAll(sel).forEach(function(b){
    b.addEventListener('click',function(){var k=b.dataset.sev||b.dataset.fam; map[k]=map[k]?0:1; b.classList.toggle('on'); apply();});
  });}
  bind('.chip[data-sev]',sev); bind('.chip[data-fam]',fam);
  document.querySelectorAll('.chip.cve').forEach(function(b){
    b.addEventListener('click',function(){var k=b.dataset.cve; cve[k]=cve[k]?0:1; b.classList.toggle('act'); apply();});
  });
  document.getElementById('q').addEventListener('input',function(ev){q=ev.target.value.toLowerCase().trim(); apply();});
  document.getElementById('reset').addEventListener('click',function(){
    Object.keys(sev).forEach(function(k){sev[k]=1;}); Object.keys(fam).forEach(function(k){fam[k]=1;}); cve={}; q='';
    document.getElementById('q').value='';
    document.querySelectorAll('.chip[data-sev],.chip[data-fam]').forEach(function(b){b.classList.add('on');});
    document.querySelectorAll('.chip.cve').forEach(function(b){b.classList.remove('act');});
    apply();
  });
  apply();
})();
</script>
</body></html>"""

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Прервано пользователем."); sys.exit(130)
