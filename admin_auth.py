"""관리자 인증 (SPEC 8장 규칙).

- 암호 2종: ADMIN_PASSWORD_FULL(기업문화팀, 전체 권한), ADMIN_PASSWORD_READONLY(HR 임원, 읽기 전용)
- 로그인하면 서명된 쿠키(HttpOnly·Secure·SameSite=Strict, 8시간). 서명 키는 SESSION_SECRET
- 쿠키에는 권한과 '그 암호의 지문(해시 앞부분)'을 넣는다 → 환경 변수에서 암호를 바꾸면 기존 쿠키는 무효
- 로그인 실패 5회면 10분 잠금. IP 를 저장하지 않으므로 기기별이 아니라 전체 잠금
- 로컬(Vercel 밖)에서 환경 변수가 없으면 개발용 값(dev-full / dev-read / 고정 서명 키)을 쓴다.
  Vercel 에서 값이 없으면 관리자 로그인 자체를 막는다
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

import db

COOKIE = "lr4_admin"
MAX_AGE = 8 * 60 * 60
MAX_FAILURES = 5
LOCK_MINUTES = 10
ROLES = ("full", "readonly")
LOCAL_DEFAULTS = {"ADMIN_PASSWORD_FULL": "dev-full", "ADMIN_PASSWORD_READONLY": "dev-read",
                  "SESSION_SECRET": "local-dev-session-secret-not-for-production"}


def on_vercel() -> bool:
    return bool(os.getenv("VERCEL"))


def _env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if not value and not on_vercel():
        value = LOCAL_DEFAULTS[name]
    return value or None


def passwords() -> dict[str, Optional[str]]:
    return {"full": _env("ADMIN_PASSWORD_FULL"), "readonly": _env("ADMIN_PASSWORD_READONLY")}


def configured() -> bool:
    pw = passwords()
    return bool(pw["full"] and pw["readonly"] and _env("SESSION_SECRET"))


def _fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()[:12]


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_env("SESSION_SECRET") or "", salt="lr4-admin")


def role_for_password(given: str) -> Optional[str]:
    """두 암호 모두와 상수 시간 비교(어느 쪽이 맞았는지 시간으로 드러나지 않게)."""
    found = None
    for role, pw in passwords().items():
        if pw and hmac.compare_digest(given.encode("utf-8"), pw.encode("utf-8")):
            found = role
    return found


def make_cookie(role: str) -> str:
    return _serializer().dumps({"r": role, "f": _fingerprint(passwords()[role] or "")})


def read_cookie(value: Optional[str]) -> Optional[str]:
    if not value or not configured():
        return None
    try:
        data = _serializer().loads(value, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    role = data.get("r") if isinstance(data, dict) else None
    if role not in ROLES:
        return None
    if not hmac.compare_digest(str(data.get("f", "")), _fingerprint(passwords()[role] or "")):
        return None
    return role


# --------------------------------------------------------------------------- #
# 로그인 실패 잠금 (전체 공통)
# --------------------------------------------------------------------------- #
def _row(session: Session) -> db.AdminAuth:
    row = session.get(db.AdminAuth, "login")
    if row is None:
        row = db.AdminAuth(key="login", failures=0, locked_until=None)
        session.add(row)
        session.flush()
    return row


def _aware(t: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if t is not None and t.tzinfo is None:   # SQLite 는 시간대 정보를 버린다
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def locked_until(session: Session, now: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    now = now or db.utcnow()
    row = session.get(db.AdminAuth, "login")
    until = _aware(row.locked_until) if row else None
    return until if until and until > now else None


def record_failure(session: Session, now: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    now = now or db.utcnow()
    row = _row(session)
    until = _aware(row.locked_until)
    if until and until <= now:   # 잠금이 끝났으면 새로 센다
        row.failures, row.locked_until = 0, None
    row.failures = (row.failures or 0) + 1
    if row.failures >= MAX_FAILURES:
        row.locked_until = now + dt.timedelta(minutes=LOCK_MINUTES)
        row.failures = 0
    session.commit()
    return _aware(row.locked_until)


def record_success(session: Session) -> None:
    row = _row(session)
    row.failures, row.locked_until = 0, None
    session.commit()
