"""backend.monitor — телеметрия ХОСТА (control plane) через /proc, без psutil.

Backend крутится в контейнере, но /proc/meminfo, /proc/loadavg и statvfs volume'а
отражают РЕАЛЬНЫЙ хост (контейнеры делят ядро) — значит это метрики самой машины.
Агентская телеметрия приходит с их /health (LIVE), тут только хост.
"""
from __future__ import annotations

import os
from typing import Dict

from . import config


def _meminfo() -> Dict[str, int]:
    out = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, rest = line.partition(":")
                out[k.strip()] = int(rest.strip().split()[0])   # kB
    except OSError:
        pass
    return out


def host_stats() -> Dict:
    st: Dict = {}
    m = _meminfo()
    total, avail = m.get("MemTotal", 0), m.get("MemAvailable", 0)
    swt, swf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    st["mem_total_mb"] = round(total / 1024)
    st["mem_used_mb"] = round((total - avail) / 1024)
    st["mem_pct"] = round((total - avail) / total * 100, 1) if total else 0.0
    st["swap_total_mb"] = round(swt / 1024)
    st["swap_used_mb"] = round((swt - swf) / 1024)
    st["swap_pct"] = round((swt - swf) / swt * 100, 1) if swt else 0.0

    st["cpu_count"] = os.cpu_count() or 1
    try:
        with open("/proc/loadavg", encoding="utf-8") as f:
            st["loadavg"] = [float(x) for x in f.read().split()[:3]]
    except OSError:
        st["loadavg"] = [0.0, 0.0, 0.0]
    # нагрузка CPU в % (load1 / ядра, с потолком 100)
    st["cpu_pct"] = round(min(100.0, st["loadavg"][0] / st["cpu_count"] * 100), 1)

    try:
        s = os.statvfs(str(config.DATA_DIR))
        tot = s.f_blocks * s.f_frsize
        free = s.f_bavail * s.f_frsize
        st["disk_total_gb"] = round(tot / 1e9, 1)
        st["disk_free_gb"] = round(free / 1e9, 1)
        st["disk_pct"] = round((1 - free / tot) * 100, 1) if tot else 0.0
    except OSError:
        st["disk_total_gb"] = st["disk_free_gb"] = st["disk_pct"] = 0

    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            st["uptime"] = round(float(f.read().split()[0]))
    except OSError:
        st["uptime"] = 0
    return st
