"""backend.provisioner — доступ к агенту по SSH (paramiko).

Хост подключается к удалённой ноде по SSH (логин/пароль/порт вводятся в вебке),
ВБРАСЫВАЕТ клиентский AWG-конфиг и поднимает туннель. Тот же канал используется для
доставки обновлений агентам (scp бандла + apply-update.sh) — это ОТДЕЛЬНАЯ механика
от обновления самого хоста (git/scp), как требует ТЗ.
"""
from __future__ import annotations

import io
import os
import shlex
import tarfile
from dataclasses import dataclass
from typing import List, Tuple

import paramiko

REMOTE_CONF = "/etc/amnezia/amneziawg/awg0.conf"
APPLY_AWG = "/opt/pintest/agent/scripts/apply-awg.sh"
APPLY_UPDATE = "/opt/pintest/agent/scripts/apply-update.sh"
REMOTE_BUNDLE = "/tmp/pintest-update.tgz"
REPO_ROOT = os.environ.get("PINTEST_ROOT", "/opt/pintest")
DEPLOY_SCRIPT = REPO_ROOT + "/agent/scripts/deploy.sh"
REMOTE_DEPLOY_SH = "/tmp/pintest-deploy.sh"
REMOTE_DEPLOY_TGZ = "/tmp/pintest-deploy.tgz"
REMOTE_DEPLOY_AWG = "/tmp/pintest-awg0.conf"
_DEPLOY_DIRS = ("agent", "core", "exploits", "awg-base")
_DEPLOY_SKIP = ("/state", "/.git", "config.env", "__pycache__", ".armed")


@dataclass
class SSHTarget:
    host: str
    port: int
    user: str
    password: str


def _connect(t: SSHTarget) -> paramiko.SSHClient:
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(t.host, port=t.port, username=t.user, password=t.password,
                timeout=15, allow_agent=False, look_for_keys=False)
    return cli


def _exec(cli: paramiko.SSHClient, cmd: str, timeout: int = 200) -> Tuple[int, str]:
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "ignore") + stderr.read().decode("utf-8", "ignore")
    rc = stdout.channel.recv_exit_status()
    return rc, out


def _put_text(cli: paramiko.SSHClient, remote_path: str, text: str) -> None:
    sftp = cli.open_sftp()
    try:
        # каталог гарантирован в образе; на всякий случай создаём
        _exec(cli, f"mkdir -p {remote_path.rsplit('/', 1)[0]}")
        with sftp.file(remote_path, "w") as f:
            f.write(text)
    finally:
        sftp.close()


def provision(t: SSHTarget, client_conf: str) -> Tuple[bool, List[str]]:
    """Вбросить AWG-конфиг и поднять туннель на агенте. Возврат (успех, лог)."""
    log: List[str] = []
    try:
        cli = _connect(t)
    except Exception as e:  # noqa: BLE001
        return False, [f"SSH к {t.host}:{t.port} не удался: {e}"]
    try:
        log.append(f"SSH подключён к {t.user}@{t.host}:{t.port}")
        _put_text(cli, REMOTE_CONF, client_conf)
        log.append(f"вброшен AWG-конфиг -> {REMOTE_CONF}")
        rc, out = _exec(cli, f"bash {APPLY_AWG}")
        log.extend(out.strip().splitlines())
        ok = rc == 0
        log.append(f"apply-awg.sh завершён с кодом {rc}")
        return ok, log
    finally:
        cli.close()


def push_update(t: SSHTarget, bundle: bytes, version: str = "") -> Tuple[bool, List[str]]:
    """Доставить агенту бандл обновления по SSH и применить (механика агентов)."""
    log: List[str] = []
    try:
        cli = _connect(t)
    except Exception as e:  # noqa: BLE001
        return False, [f"SSH к {t.host}:{t.port} не удался: {e}"]
    try:
        sftp = cli.open_sftp()
        with sftp.file(REMOTE_BUNDLE, "wb") as f:
            f.write(bundle)
        sftp.close()
        log.append(f"бандл доставлен -> {REMOTE_BUNDLE} ({len(bundle)} байт)")
        rc, out = _exec(cli, f"bash {APPLY_UPDATE} {REMOTE_BUNDLE} {version}")
        log.extend(out.strip().splitlines())
        return rc == 0, log
    finally:
        cli.close()


def _scrub(ti: "tarfile.TarInfo"):
    """Не тащить на ноду состояние/секреты/скомпилированное (и .key-ключи)."""
    name = ti.name
    if name.endswith(".key") or any(b in name for b in _DEPLOY_SKIP):
        return None
    return ti


def build_deploy_tarball() -> bytes:
    """Собрать tgz исходников агента (agent/core/exploits/awg-base) для отправки на ноду.

    БЕЗ git и без секретов — код едет с ХОСТА, репозиторий на ноде не клонируется."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for d in _DEPLOY_DIRS:
            path = os.path.join(REPO_ROOT, d)
            if os.path.isdir(path):
                tar.add(path, arcname=d, filter=_scrub)
    return buf.getvalue()


def deploy(t: SSHTarget, tarball: bytes, awg_conf: str, agent_name: str,
           host_tunnel_ip: str, agent_ssh_pass: str = "pintest") -> Tuple[bool, List[str]]:
    """ПОЛНЫЙ деплой агента на ноду с хоста: копируем исходники + awg-конфиг + deploy.sh,
    ставим/собираем/поднимаем контейнер, вбрасываем туннель, взводим dead-man. Без git."""
    log: List[str] = []
    try:
        cli = _connect(t)
    except Exception as e:  # noqa: BLE001
        return False, [f"SSH к {t.host}:{t.port} не удался: {e}"]
    try:
        log.append(f"SSH подключён к {t.user}@{t.host}:{t.port}")
        try:
            with open(DEPLOY_SCRIPT, encoding="utf-8") as f:
                script = f.read()
        except OSError as e:
            return False, log + [f"нет deploy.sh на хосте ({DEPLOY_SCRIPT}): {e}"]
        sftp = cli.open_sftp()
        with sftp.file(REMOTE_DEPLOY_SH, "w") as f:
            f.write(script)
        with sftp.file(REMOTE_DEPLOY_TGZ, "wb") as f:
            f.write(tarball)
        with sftp.file(REMOTE_DEPLOY_AWG, "w") as f:
            f.write(awg_conf)
        sftp.close()
        log.append(f"доставлены: исходники ({len(tarball)} байт), awg-конфиг, deploy.sh")
        cmd = (f"bash {REMOTE_DEPLOY_SH} {REMOTE_DEPLOY_TGZ} {REMOTE_DEPLOY_AWG} "
               f"{shlex.quote(agent_name)} {shlex.quote(host_tunnel_ip)} {shlex.quote(agent_ssh_pass)} "
               f"{int(t.port)}")   # SSH-порт ноды — для fail2ban/ufw хардненинга в deploy.sh
        rc, out = _exec(cli, cmd, timeout=1200)   # apt+docker build могут быть долгими
        log.extend(out.strip().splitlines())
        log.append(f"deploy.sh завершён с кодом {rc}")
        return rc == 0, log
    finally:
        cli.close()
