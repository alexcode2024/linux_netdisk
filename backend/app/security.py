from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .config import Settings
from .database import Session, User, utcnow


password_hasher = PasswordHasher()
login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def is_login_limited(key: str, settings: Settings) -> bool:
    now = time.monotonic()
    attempts = login_attempts[key]
    while attempts and attempts[0] <= now - settings.security.login_rate_limit.window_seconds:
        attempts.popleft()
    return len(attempts) >= settings.security.login_rate_limit.attempts


def register_login_failure(key: str) -> None:
    login_attempts[key].append(time.monotonic())


def create_session(db: DbSession, user: User, ttl_hours: int) -> str:
    session_id = secrets.token_urlsafe(48)
    db.add(Session(id=session_id, user_id=user.id, expires_at=utcnow() + timedelta(hours=ttl_hours)))
    db.commit()
    return session_id


def get_current_user(request: Request, db: DbSession, settings: Settings) -> User:
    session_id = request.cookies.get("nas_session")
    if not session_id or len(session_id) > 128:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    session = db.scalar(select(Session).where(Session.id == session_id))
    if session is None or session.expires_at <= utcnow() or not session.user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    return session.user


def require_user(request: Request, db: DbSession = Depends("get_db")):
    settings: Settings = request.app.state.settings
    return get_current_user(request, db, settings)
