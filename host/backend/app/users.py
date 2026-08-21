"""backend.users — учётные записи для входа в вебку.

Хранит логины + хэш пароля в БД. При первом старте заводит админа из ENV
(ADMIN_USER/ADMIN_PASSWORD). Позволяет менять свои креды и создавать аккаунты.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from . import config, db


def _hash(password: str) -> str:
    return hashlib.sha256((config.SESSION_SECRET + ":" + password).encode()).hexdigest()


def seed_admin() -> None:
    """config.env — ИСТОЧНИК ИСТИНЫ для админ-аккаунта. На КАЖДОМ старте гарантируем,
    что логин ADMIN_USER существует с паролем ADMIN_PASSWORD (UPSERT). Значит:
    поменял креды в config.env + рестарт контейнеров = входишь под новыми кредами.
    Аккаунты, созданные в вебке (другие логины), не трогаем."""
    db.q("INSERT INTO users(login, pw_hash) VALUES(%s,%s) "
         "ON CONFLICT (login) DO UPDATE SET pw_hash = EXCLUDED.pw_hash",
         (config.ADMIN_USER, _hash(config.ADMIN_PASSWORD)))


def verify(login: str, password: str) -> bool:
    row = db.one("SELECT pw_hash FROM users WHERE login=%s", (login,))
    return bool(row) and row["pw_hash"] == _hash(password)


def exists(login: str) -> bool:
    return db.one("SELECT 1 FROM users WHERE login=%s", (login,)) is not None


def create(login: str, password: str) -> None:
    login = (login or "").strip()
    if not login or not password:
        raise ValueError("нужны логин и пароль")
    if exists(login):
        raise ValueError("такой логин уже есть")
    db.q("INSERT INTO users(login, pw_hash) VALUES(%s,%s)", (login, _hash(password)))


def change_credentials(old_login: str, new_login: str, new_password: str) -> str:
    """Сменить логин и/или пароль текущего аккаунта. Возвращает актуальный логин."""
    new_login = (new_login or old_login).strip() or old_login
    if new_login != old_login and exists(new_login):
        raise ValueError("такой логин уже занят")
    pw = _hash(new_password) if new_password else db.one("SELECT pw_hash FROM users WHERE login=%s", (old_login,))["pw_hash"]
    db.q("UPDATE users SET login=%s, pw_hash=%s WHERE login=%s", (new_login, pw, old_login))
    return new_login


def delete(login: str) -> None:
    n = db.one("SELECT count(*) c FROM users")["c"]
    if n <= 1:
        raise ValueError("нельзя удалить последний аккаунт")
    db.q("DELETE FROM users WHERE login=%s", (login,))


def list_users() -> List[dict]:
    return db.all_("SELECT login, created_at FROM users ORDER BY created_at")
