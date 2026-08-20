"""backend.loot — сбор «лута» из успешных закреплений и отдельный лут-отчёт.

Лут — это то, что реально снято при capture: флаг, вывод шелла/RCE (id/hostname/uname),
любые находки модуля (creds/sessions/файлы — что вернул модуль в res.loot), маркер.
Данные лежат в captures.data; здесь собираем в удобный вид + генерим отчёт (md/html/json).
"""
from __future__ import annotations

import html
import json
import time
from typing import Dict, List

from . import db


def items() -> List[Dict]:
    """Успешные закрепления с распакованным лутом (свежие сверху)."""
    rows = db.all_("SELECT * FROM captures WHERE phase='capture' AND success=true ORDER BY ts DESC")
    out: List[Dict] = []
    for r in rows:
        d = r.get("data") or {}
        out.append({
            "ts": r["ts"], "target": r["target"], "cve": r["cve"], "port": r["port"],
            "agent_id": r["agent_id"], "flag": r["flag"] or d.get("flag"),
            "marker": d.get("marker"), "loot": d.get("loot") or {}, "log": d.get("log") or [],
        })
    return out


def summary() -> Dict:
    it = items()
    hosts = sorted({i["target"] for i in it})
    flags = [i["flag"] for i in it if i["flag"]]
    return {"captures": len(it), "hosts": len(hosts), "flags": len(flags), "items": it}


def _ts(t) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    except Exception:  # noqa: BLE001
        return str(t)


def report_md() -> str:
    it = items()
    lines = ["# Лут-отчёт Pintest", "",
             f"Закреплений: **{len(it)}** · хостов: **{len({i['target'] for i in it})}** · "
             f"флагов: **{len([i for i in it if i['flag']])}**", ""]
    for i in it:
        lines.append(f"## {i['target']}:{i['port']} — {i['cve']}")
        lines.append(f"- время: {_ts(i['ts'])}")
        if i["flag"]:
            lines.append(f"- **флаг:** `{i['flag']}`")
        if i["marker"]:
            lines.append(f"- маркер: `{i['marker']}`")
        for k, v in (i["loot"] or {}).items():
            lines.append(f"\n**{k}:**\n\n```\n{str(v).strip()}\n```")
        if i["log"]:
            lines.append("\n<details><summary>лог</summary>\n\n```\n" + "\n".join(i["log"]) + "\n```\n</details>")
        lines.append("")
    return "\n".join(lines)


def report_html() -> str:
    it = items()
    esc = html.escape
    cards = []
    for i in it:
        loot_blocks = "".join(
            f'<div class="k">{esc(str(k))}</div><pre>{esc(str(v).strip())}</pre>'
            for k, v in (i["loot"] or {}).items())
        log_block = ('<details><summary>лог</summary><pre>' + esc("\n".join(i["log"])) + "</pre></details>") if i["log"] else ""
        flag = f'<div class="flag">🏳 {esc(i["flag"])}</div>' if i["flag"] else ""
        cards.append(
            f'<div class="card"><div class="h">🚩 {esc(i["target"])}:{i["port"]} '
            f'<span class="cve">{esc(i["cve"])}</span><span class="t">{_ts(i["ts"])}</span></div>'
            f'{flag}{loot_blocks}'
            f'{("<div class=m>маркер: "+esc(i["marker"])+"</div>") if i["marker"] else ""}{log_block}</div>')
    body = "".join(cards) or '<div class="empty">лута пока нет — закрепись на цели во вкладке «Эксплуатация»</div>'
    return (
        "<!doctype html><html lang=ru><head><meta charset=utf-8><title>Лут — Pintest</title>"
        "<style>body{background:#0e1116;color:#c9d1d9;font:15px/1.5 system-ui,Segoe UI,Arial;margin:0;padding:24px}"
        "h1{color:#a371f7}.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 16px;margin:0 0 14px}"
        ".h{font-weight:700;font-size:1.05rem;display:flex;gap:.6rem;align-items:center}"
        ".cve{color:#f0883e;font-family:ui-monospace,Consolas,monospace;font-size:.9rem}"
        ".t{margin-left:auto;color:#8b949e;font-size:.82rem}"
        ".flag{color:#c4b5fd;font-weight:700;margin:.5rem 0}.k{color:#58a6ff;font-weight:700;margin:.7rem 0 .2rem}"
        "pre{background:#01040b;border:1px solid #30363d;border-radius:8px;padding:10px;overflow:auto;"
        "font:12px ui-monospace,Consolas,monospace;color:#adbac7;white-space:pre-wrap}"
        ".m{color:#8b949e;font-size:.82rem;margin-top:.5rem}.empty{color:#8b949e;text-align:center;padding:40px}"
        "summary{cursor:pointer;color:#8b949e;margin-top:.5rem}</style></head><body>"
        f"<h1>◈ Лут-отчёт Pintest</h1><p style='color:#8b949e'>закреплений {len(it)} · "
        f"хостов {len({i['target'] for i in it})} · флагов {len([i for i in it if i['flag']])}</p>{body}</body></html>")
