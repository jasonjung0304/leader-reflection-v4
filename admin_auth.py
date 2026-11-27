"""관리자 인증 (SPEC 8장 규칙).

- 암호 2종: ADMIN_PASSWORD_FULL(기업문화팀, 전체 권한), ADMIN_PASSWORD_READONLY(HR 임원, 읽기 전용)
- 로그인하면 서명된 쿠키(HttpOnly·Secure·SameSite=Strict, 8시간). 서명 키는 SESSION_SECRET
- 쿠키에는 권한과 '그 암호의 지문(해시 앞부분)'을 넣는다 → 환경 변수에서 암호를 바꾸면 기존 쿠키는 무효
- 로그인 실패 5회면 10분 잠금. IP 를 저장하지 않으므로 기기별이 아니라 전체 잠금
- 로컬(Vercel 밖)에서 환경 변수가 없으면 개발용 값(dev-full / dev-read / 고정 서명 키)을 쓴다.
  Vercel 에서 값이 없으면 관리자 로그인 자체를 막는다
- (2026-11-27) 관리자 화면에서 바꾼 암호는 PBKDF2 해시로 DB 설정(admin_password_hashes)에 두고, 있으면 환경 변수보다 우선
- (2026-11-27) 비밀 관리자 링크: 관리자 링크 키(DB 설정 admin_link_key, 없으면 환경 변수 ADMIN_LINK_KEY)를 정해 두면
  그 키가 붙은 링크(/admin?a=키)로 연 브라우저만 로그인 화면을 볼 수 있다. 키가 없으면 이 확인은 하지 않는다
- 쿠키의 지문은 '그 권한의 암호(또는 해시) + 관리자 링크 키'로 만든다 → 암호나 링크를 바꾸면 기존 로그인은 모두 풀린다
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets
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
    """환경 변수의 암호(관리자 화면에서 바꾸기 전의 첫 암호)."""
    return {"full": _env("ADMIN_PASSWORD_FULL"), "readonly": _env("ADMIN_PASSWORD_READONLY")}


# --------------------------------------------------------------------------- #
# 관리자 화면에서 바꾼 암호(PBKDF2-SHA256, 원문은 저장하지 않음)
# --------------------------------------------------------------------------- #
PBKDF2_ITERATIONS = 200_000
MIN_PASSWORD = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_hash(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), digest)


def _hashes(settings: Optional[dict]) -> dict:
    value = (settings or {}).get("admin_password_hashes")
    return value if isinstance(value, dict) else {}


def password_source(role: str, settings: Optional[dict]) -> str:
    """'settings'(관리자 화면에서 바꿈) 또는 'env'(Vercel 환경 변수)."""
    return "settings" if _hashes(settings).get(role) else "env"


def _secret(role: str, settings: Optional[dict]) -> Optional[str]:
    """그 권한의 현재 비밀(해시가 있으면 해시 문자열, 없으면 환경 변수 암호) — 쿠키 지문용."""
    stored = _hashes(settings).get(role)
    return f"h:{stored}" if stored else (f"e:{passwords()[role]}" if passwords()[role] else None)


def _matches(role: str, given: str, settings: Optional[dict]) -> bool:
    stored = _hashes(settings).get(role)
    if stored:
        return _verify_hash(given, stored)
    pw = passwords()[role]
    return bool(pw) and hmac.compare_digest(given.encode("utf-8"), pw.encode("utf-8"))


# --------------------------------------------------------------------------- #
# 비밀 관리자 링크
# --------------------------------------------------------------------------- #
def link_key(settings: Optional[dict]) -> tuple[Optional[str], str]:
    """(관리자 링크 키, 출처: settings / env / none). 없으면 링크 확인을 하지 않는다."""
    value = (settings or {}).get("admin_link_key")
    if value:
        return value, "settings"
    env = os.getenv("ADMIN_LINK_KEY")
    if env:
        return env, "env"
    return None, "none"


def link_ok(given: Optional[str], settings: Optional[dict]) -> bool:
    expected, _ = link_key(settings)
    if not expected:
        return True
    return bool(given) and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def new_link_key() -> str:
    return secrets.token_urlsafe(24)


# --------------------------------------------------------------------------- #
# 로그인 쿠키
# --------------------------------------------------------------------------- #
def configured(settings: Optional[dict] = None) -> bool:
    return bool(_secret("full", settings) and _secret("readonly", settings) and _env("SESSION_SECRET"))


def _fingerprint(role: str, settings: Optional[dict]) -> str:
    base = f"{_secret(role, settings) or ''}|{link_key(settings)[0] or ''}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_env("SESSION_SECRET") or "", salt="lr4-admin")


def role_for_password(given: str, settings: Optional[dict] = None) -> Optional[str]:
    """두 권한 모두 확인(어느 쪽이 맞았는지 시간으로 덜 드러나게 둘 다 계산)."""
    found = None
    for role in ROLES:
        if _matches(role, given, settings):
            found = role
    return found


def make_cookie(role: str, settings: Optional[dict] = None) -> str:
    return _serializer().dumps({"r": role, "f": _fingerprint(role, settings)})


def read_cookie(value: Optional[str], settings: Optional[dict] = None) -> Optional[str]:
    if not value or not configured(settings):
        return None
    try:
        data = _serializer().loads(value, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    role = data.get("r") if isinstance(data, dict) else None
    if role not in ROLES:
        return None
    if not hmac.compare_digest(str(data.get("f", "")), _fingerprint(role, settings)):
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
