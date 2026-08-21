"""agent_api.runner — запуск движка (core.auditor) по одному чанку целей.

Один чанк = под-список целей + флаги аудита, присланные хостом. Раннер:
  * пишет цели в файл, собирает argv из opts,
  * запускает `python3 -m core.auditor ... targets` подпроцессом (не демоном),
  * следит за status.json и results.jsonl в выходном каталоге,
  * отдаёт хосту прогресс и новые строки результатов (тот их сливает в сводный отчёт).

Идемпотентность на уровне системы обеспечивает хост (svc_done), поэтому повторный
прогон того же чанка (после failover) безопасен — раннер просто чистит и гонит заново.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import config


def _argv_from_opts(opts: Dict) -> List[str]:
    """Собрать флаги CLI движка из структуры opts (см. контракт с хостом)."""
    argv: List[str] = []
    ports = opts.get("ports") or {}
    mode, value = ports.get("mode", "top"), str(ports.get("value", "") or "")
    if mode == "all":
        argv.append("-A")
    elif mode == "list" and value:
        argv += ["-p", value]
    else:  # top
        argv += ["--top", value or "100"]
    argv += ["-T", str(opts.get("timing", 4))]
    if opts.get("no_udp"):
        argv.append("--no-udp")
    if opts.get("no_tcp"):
        argv.append("--no-tcp")
    if opts.get("jobs"):
        argv += ["-j", str(opts["jobs"])]
    if opts.get("skip_disc"):
        argv.append("--Pn")
    if opts.get("no_preflight"):
        argv.append("--no-preflight")
    return argv


class ChunkRun:
    """Один выполняющийся (или завершённый) чанк."""

    def __init__(self, job_id: str, chunk_id: str, targets: List[str], opts: Dict):
        self.job_id = job_id
        self.chunk_id = chunk_id
        self.targets = targets
        self.opts = opts
        self.dir = config.JOBS_DIR / job_id / chunk_id
        self.out = self.dir / "out"          # AUDIT_OUT движка
        self.proc: Optional[subprocess.Popen] = None
        self.status = "pending"              # pending|running|done|failed|cancelled
        self.error = ""
        self.started = 0.0
        self.finished = 0.0

    # ---- запуск / остановка -------------------------------------------------
    def start(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)   # чистый повтор (failover)
        self.out.mkdir(parents=True, exist_ok=True)
        tf = self.dir / "targets.txt"
        tf.write_text("\n".join(self.targets) + "\n", encoding="utf-8")

        argv = _argv_from_opts(self.opts) + [str(tf)]
        env = dict(os.environ)
        env["AUDIT_OUT"] = str(self.out)
        env["AUDIT_WORKER"] = "1"            # не трогать ~/.bashrc, цветной лог
        env.setdefault("PINTEST_CHUNK", "8") # мелкий внутренний чанк -> прогресс виден
        env["PYTHONUNBUFFERED"] = "1"
        log = open(self.dir / "run.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "core.auditor", *argv],
            cwd=str(config.PINTEST_ROOT), env=env,
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.status = "running"
        self.started = time.time()
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        assert self.proc is not None
        rc = self.proc.wait()
        self.finished = time.time()
        if self.status == "cancelled":
            return
        if rc == 0:
            self.status = "done"
        else:
            self.status = "failed"
            self.error = f"движок завершился с кодом {rc}"

    def cancel(self) -> None:
        self.status = "cancelled"
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except OSError:
                pass

    # ---- чтение прогресса / результатов -------------------------------------
    def _live_ips(self):
        """Поштучные стадии для живого графа: alive (ответили на discovery) и scanned (глубокий скан)."""
        alive, scanned = [], []
        try:
            for f in self.out.glob("alive_*.txt"):
                alive += [l.strip() for l in f.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
        except OSError:
            pass
        p = self.out / "results.jsonl"
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ip = json.loads(line).get("ip")
                    except ValueError:
                        ip = None
                    if ip:
                        scanned.append(ip)
            except OSError:
                pass
        return alive, scanned

    def progress(self) -> Dict:
        st = {}
        sj = self.out / "status.json"
        if sj.exists():
            try:
                st = json.loads(sj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                st = {}
        done, total = st.get("done", 0), st.get("total", len(self.targets))
        alive, scanned = self._live_ips()
        return {
            "job_id": self.job_id,
            "chunk_id": self.chunk_id,
            "status": self.status,
            "phase": st.get("phase", ""),
            "stage": st.get("stage", 0),
            "done": done,
            "total": total or len(self.targets),
            "openh": st.get("openh", 0),
            "cves": st.get("cves", 0),
            "targets": len(self.targets),
            "alive": alive,          # IP, ответившие на discovery (живые)
            "scanned": scanned,      # IP с завершённым глубоким сканом
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - self.started, 1) if self.started else 0,
        }

    def results_since(self, offset: int) -> Dict:
        """Строки results.jsonl начиная с индекса offset (host-словари для слияния)."""
        p = self.out / "results.jsonl"
        lines: List[str] = []
        if p.exists():
            try:
                all_lines = [l for l in p.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
                lines = all_lines[offset:]
                offset = len(all_lines)
            except OSError:
                pass
        return {"offset": offset, "lines": lines, "status": self.status}


class Registry:
    """Все чанки, что видел агент (по (job_id, chunk_id))."""

    def __init__(self):
        self._runs: Dict[str, ChunkRun] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(job_id: str, chunk_id: str) -> str:
        return f"{job_id}/{chunk_id}"

    def start_chunk(self, job_id: str, chunk_id: str, targets: List[str], opts: Dict) -> ChunkRun:
        with self._lock:
            k = self.key(job_id, chunk_id)
            old = self._runs.get(k)
            if old:
                old.cancel()
            run = ChunkRun(job_id, chunk_id, targets, opts)
            self._runs[k] = run
        run.start()
        return run

    def get(self, job_id: str, chunk_id: str) -> Optional[ChunkRun]:
        return self._runs.get(self.key(job_id, chunk_id))

    def cancel_all(self) -> None:
        for r in list(self._runs.values()):
            r.cancel()

    def summary(self) -> Dict:
        active = [r.progress() for r in self._runs.values() if r.status == "running"]
        return {"active_chunks": len(active), "chunks": active}


registry = Registry()
