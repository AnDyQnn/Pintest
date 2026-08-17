"""backend.orchestrator — распределение чанков, failover, слияние отчёта.

Джоба: канон-цели режутся на чанки, чанки раздаются онлайн-агентам (последовательно
или параллельно). Агент сканирует чанк и отдаёт results.jsonl; хост сливает их в
МАСТЕР-каталог отчёта и пересобирает отчёт ПОСЛЕ КАЖДОГО влитого чанка (дописывается,
а не с нуля — reuse механики --resume: results.jsonl + svc_done + build_reports).

Failover: если агент выпал (status != online) во время чанка — чанк возвращается в
pending и достаётся другому. Идемпотентно: svc_done/merge отсекают уже сделанные хосты.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Dict, List, Optional

import httpx

from core import reporting

from . import agents, config, db

JOBS: Dict[str, "JobRunner"] = {}


def _plabel(opts: Dict) -> str:
    p = opts.get("ports") or {}
    mode, val = p.get("mode", "top"), str(p.get("value", "") or "")
    if mode == "all":
        return "-p-"
    if mode == "list" and val:
        return f"-p {val}"
    return f"--top-ports {val or '100'}"


def _chunk(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


class JobRunner:
    def __init__(self, job_id: str, opts: Dict, mode: str, canon: Dict):
        self.job_id = job_id
        self.opts = opts
        self.mode = mode                      # sequential|parallel
        self.canon = canon
        self.targets: List[str] = canon.get("targets", [])
        self.report_dir = config.REPORTS_DIR / job_id
        self.acc: dict = {}
        self.offsets: Dict[str, int] = {}     # chunk_id -> сколько строк результатов уже слито
        self.meta = {}
        self._stop = False

    # ---- запись состояния чанков в БД ---------------------------------------
    def _set_chunk(self, cid: str, status: str, agent_id: str = None, progress: Dict = None):
        db.q("""UPDATE chunks SET status=%s, agent_id=%s, progress=%s
                WHERE job_id=%s AND chunk_id=%s""",
             (status, agent_id, db.js(progress or {}), self.job_id, cid))

    def _pending_chunks(self) -> List[Dict]:
        return db.all_("SELECT * FROM chunks WHERE job_id=%s AND status IN ('pending','failed')",
                       (self.job_id,))

    def _incomplete(self) -> int:
        r = db.one("SELECT count(*) c FROM chunks WHERE job_id=%s AND status!='done'", (self.job_id,))
        return r["c"] if r else 0

    # ---- основной цикл ------------------------------------------------------
    async def run(self):
        config.ensure_dirs()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.meta = {
            "stamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "src": "web-upload", "ports": _plabel(self.opts),
            "v4n": self.canon.get("v4n", 0), "v6n": self.canon.get("v6n", 0),
            "a4n": 0, "a6n": 0,
        }
        # нарезать чанки
        chunks = list(_chunk(self.targets, config.CHUNK_SIZE))
        for i, ch in enumerate(chunks):
            cid = f"c{i:03d}"
            db.q("""INSERT INTO chunks(job_id,chunk_id,targets,status) VALUES(%s,%s,%s,'pending')
                    ON CONFLICT (job_id,chunk_id) DO NOTHING""", (self.job_id, cid, db.js(ch)))
        db.q("UPDATE jobs SET status='running', total=%s WHERE id=%s", (len(self.targets), self.job_id))
        reporting.rebuild(self.report_dir, self.meta, self.acc)   # пустой отчёт сразу

        try:
            while self._incomplete() > 0 and not self._stop:
                online = agents.online_ids()          # сканирует любой онлайн-агент
                pend = self._pending_chunks()
                if not online or not pend:
                    await asyncio.sleep(1)
                    continue
                slots = 1 if self.mode == "sequential" else len(online)
                # занять свободные слоты пендинг-чанками на онлайн-агентах
                busy = {r["agent_id"] for r in
                        db.all_("SELECT agent_id FROM chunks WHERE job_id=%s AND status='assigned'", (self.job_id,))}
                free_agents = [a for a in online if a not in busy][:slots]
                tasks = []
                for a in free_agents:
                    if not pend:
                        break
                    ch = pend.pop(0)
                    tasks.append(asyncio.create_task(self._run_chunk(a, ch)))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    await asyncio.sleep(1)
            self._finalize()
        except Exception as e:  # noqa: BLE001
            db.q("UPDATE jobs SET status='failed', stats=%s WHERE id=%s",
                 (db.js({"error": str(e)}), self.job_id))

    async def _run_chunk(self, agent_id: str, ch: Dict):
        a = agents.get(agent_id)
        if not a:
            return
        cid, tip = ch["chunk_id"], a["tunnel_ip"]
        targets = ch["targets"] if isinstance(ch["targets"], list) else []
        base = f"http://{tip}:{config.AGENT_API_PORT}"
        self._set_chunk(cid, "assigned", agent_id)
        try:
            async with httpx.AsyncClient(timeout=15) as cli:
                await cli.post(base + "/chunk", json={
                    "job_id": self.job_id, "chunk_id": cid,
                    "targets": targets, "opts": self.opts})
                # опрос до завершения/провала
                while True:
                    if agents.get(agent_id)["status"] != "online":
                        raise RuntimeError("агент выпал")
                    st = (await cli.get(f"{base}/chunk/{self.job_id}/{cid}")).json()
                    # подтянуть новые строки результатов и слить в отчёт
                    off = self.offsets.get(cid, 0)
                    rr = (await cli.get(f"{base}/chunk/{self.job_id}/{cid}/results",
                                        params={"since": off})).json()
                    if rr.get("lines"):
                        reporting.merge_result_lines(self.acc, rr["lines"], self.report_dir)
                        self.offsets[cid] = rr["offset"]
                        reporting.mark_hosts_done(self.report_dir,
                                                  [self.acc[k]["ip"] for k in self.acc])
                        self._rebuild_and_persist()
                    self._set_chunk(cid, "assigned", agent_id, st)
                    if st["status"] in ("done", "failed", "cancelled"):
                        break
                    await asyncio.sleep(config.POLL_INTERVAL)
            if st["status"] == "done":
                self._set_chunk(cid, "done", agent_id, st)
            else:
                self._set_chunk(cid, "pending", None)      # failover: вернуть в очередь
        except Exception:  # noqa: BLE001 — агент выпал/ошибка -> failover
            self._set_chunk(cid, "pending", None)

    def _rebuild_and_persist(self):
        self.meta["a4n"] = sum(1 for h in self.acc.values() if h["fam"] == "IPv4")
        self.meta["a6n"] = sum(1 for h in self.acc.values() if h["fam"] == "IPv6")
        reporting.rebuild(self.report_dir, self.meta, self.acc)
        # находки -> БД (для UI/эксплуатации), перезаписью
        db.q("DELETE FROM findings WHERE job_id=%s", (self.job_id,))
        for fam, host, cve, cvss, sev in reporting.findings(self.acc):
            db.q("""INSERT INTO findings(job_id,host,family,cve,cvss,severity)
                    VALUES(%s,%s,%s,%s,%s,%s)""", (self.job_id, host, fam, cve, cvss, sev))
        nopen = sum(1 for h in self.acc.values() if h["ports"])
        ncve = sum(len(h["cves"]) for h in self.acc.values())
        db.q("UPDATE jobs SET stats=%s WHERE id=%s",
             (db.js({"hosts": len(self.acc), "with_ports": nopen, "cves": ncve}), self.job_id))

    def _finalize(self):
        self._rebuild_and_persist()
        done = self._incomplete() == 0 and not self._stop
        db.q("UPDATE jobs SET status=%s, finished_at=%s WHERE id=%s",
             ("done" if done else "cancelled", time.time(), self.job_id))

    def stop(self):
        self._stop = True


def start_job(opts: Dict, mode: str, canon: Dict, diff_against: str = None) -> str:
    job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    db.q("""INSERT INTO jobs(id,opts,mode,status,report_dir,diff_against)
            VALUES(%s,%s,%s,'pending',%s,%s)""",
         (job_id, db.js(opts), mode, str(config.REPORTS_DIR / job_id), diff_against))
    runner = JobRunner(job_id, opts, mode, canon)
    JOBS[job_id] = runner
    asyncio.create_task(runner.run())
    return job_id
