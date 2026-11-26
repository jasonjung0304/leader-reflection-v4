"""DB 연결과 테이블 정의 (SPEC 5.2).

- `DATABASE_URL`(없으면 Supabase 연동이 넣는 `POSTGRES_URL`)이 있으면 Postgres(pooled 주소), 없으면 로컬 SQLite(`.local.db`).
  Vercel 에서 둘 다 없으면 시작하지 않는다(읽기 전용 파일 시스템이라 SQLite 를 쓸 수 없음).
- 서버리스(Vercel Functions)에서는 함수 인스턴스마다 연결 풀을 오래 쥐고 있지 않도록 NullPool 을 쓰고,
  연결 재사용은 DB 제공사의 풀러(pgbouncer 등)에 맡긴다.
- 개인정보(이름·사번·이메일·소속·IP·User-Agent 원문)는 어떤 테이블에도 두지 않는다.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parent
LOCAL_SQLITE_PATH = BASE_DIR / ".local.db"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# 드라이버(libpq)가 모르는 쿼리 값. Supabase 연동 주소의 supa=, Prisma 용 pgbouncer= 등은 지운다
DROP_QUERY_KEYS = {"supa", "pgbouncer", "connection_limit", "pool_timeout", "schema"}


def env_db_url() -> Optional[str]:
    return os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or None


def normalize_db_url(url: Optional[str]) -> str:
    """환경 변수 값을 SQLAlchemy URL 로 바꾼다. 비어 있으면 로컬 SQLite."""
    if not url:
        if os.getenv("VERCEL"):
            raise RuntimeError("DATABASE_URL(또는 POSTGRES_URL)이 설정되지 않았습니다")
        return f"sqlite:///{LOCAL_SQLITE_PATH.as_posix()}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgresql+psycopg://"):
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in DROP_QUERY_KEYS]
        if not any(k == "sslmode" for k, _ in query):
            query.append(("sslmode", "require"))      # 사외 클라우드 DB 는 항상 암호화 연결
        url = urlunsplit(parts._replace(query=urlencode(query)))
    return url


def make_engine(url: Optional[str] = None) -> Engine:
    sa_url = normalize_db_url(url if url is not None else env_db_url())
    if sa_url.startswith("sqlite"):
        return create_engine(sa_url, connect_args={"check_same_thread": False})
    # pgbouncer(transaction 모드)와 함께 쓸 때 서버 측 prepared statement 를 끈다
    return create_engine(sa_url, poolclass=NullPool, connect_args={"prepare_threshold": None})


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Participant(Base):
    """리더 1명(= 한 기기·한 브라우저). 리더명과 서버가 만든 무작위 UUID 로만 식별."""

    __tablename__ = "participant"
    __table_args__ = (UniqueConstraint("cycle_id", "leader_name_norm", "serial", name="uq_leader_serial"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(40), index=True)
    leader_name: Mapped[str] = mapped_column(String(40))          # 저장 형태 = 부르는 형태 = 관리자 표시 형태
    leader_name_norm: Mapped[str] = mapped_column(String(40), index=True)  # 같은 리더명 판단용
    leader_type: Mapped[str] = mapped_column(String(10))           # rc / re / oa / mi / custom
    option_order: Mapped[list[Any]] = mapped_column(default=list)  # 보기 표시 순서
    serial: Mapped[str] = mapped_column(String(4))                 # 같은 리더명 안에서 중복 없는 4자리
    name_changes: Mapped[int] = mapped_column(Integer, default=0)
    device: Mapped[str] = mapped_column(String(10))                # mobile / pc
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Step1(Base):
    __tablename__ = "step1"

    participant_id: Mapped[str] = mapped_column(ForeignKey("participant.id", ondelete="CASCADE"), primary_key=True)
    reviews: Mapped[dict[str, Any]] = mapped_column(default=dict)  # review_1~5: remembered / not_remembered
    keywords: Mapped[list[Any]] = mapped_column(default=list)      # 고른 순서대로
    last_screen: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Check(Base):
    """구성원 1명에 대한 점검. 별명은 저장하지 않는다."""

    __tablename__ = "check_run"
    __table_args__ = (UniqueConstraint("participant_id", "seq", name="uq_check_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participant.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)                      # 몇 번째 구성원
    situations: Mapped[list[Any]] = mapped_column(default=list)
    answers: Mapped[dict[str, Any]] = mapped_column(default=dict)  # 17문항 응답
    judgment: Mapped[dict[str, Any]] = mapped_column(default=dict)  # 축 판정·4분면
    results: Mapped[dict[str, Any]] = mapped_column(default=dict)  # 결과 1~3 선택 결과
    action_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # recommended / other_preset / custom
    action_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)   # 고른 추천 문장: situation:tears / item:c2_1
    action_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # 익명화된 직접 쓴 문장
    status: Mapped[str] = mapped_column(String(12), default="in_progress")       # in_progress / done
    last_screen: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    """SPEC 5.3. 리더명 전 단계는 participant_id 없이 device_id(브라우저 저장소 무작위 값)로 기록."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    cycle_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)  # 기록 당시 차수
    name: Mapped[str] = mapped_column(String(40), index=True)
    participant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    check_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    device: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # mobile / pc
    props: Mapped[dict[str, Any]] = mapped_column(default=dict)


class AdminAuth(Base):
    """관리자 로그인 실패 횟수와 잠금(전체 공통 1행). IP 를 저장하지 않으므로 기기별이 아니라 전체 잠금."""

    __tablename__ = "admin_auth"

    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Setting(Base):
    """관리자 설정(차수명, 운영 기간, 대상 인원, 입장 키, 머문 시간 자르기 기준, 판정 기준값)."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


# --------------------------------------------------------------------------- #
# 관리자 설정 (setting 테이블, 키마다 한 행). 값이 없으면 기본값
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS: dict[str, Any] = {
    "cycle_id": "2026-1",
    "cycle_name": "2026 GC Review",
    "deadline": None,          # ISO 날짜(그날 끝까지) 또는 시각. 지나면 마감
    "target_count": 170,       # 참여율 분모
    "entry_key": None,         # 관리자 설정에서 바꾼 입장 키. 없으면 환경 변수 ENTRY_KEY
    "dwell_cutoff_sec": 180,   # 머문 시간 자르기 기준(SPEC 5.3)
    "high_threshold": 0.5,     # 축 판정 기준값(SPEC 4.3)
    "duration_min": None,      # 안내 화면 '약 {n}분'. None 이면 숨김
}


def get_settings(session: Session) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    for row in session.query(Setting).all():
        if row.key in DEFAULT_SETTINGS:
            merged[row.key] = row.value
    return merged


def save_settings(session: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            raise KeyError(key)
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
    session.commit()


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        engine = make_engine()
        create_tables(engine)
        _engine = engine
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def create_tables(engine: Engine) -> None:
    """없는 테이블만 만든다. 서버리스에서 여러 인스턴스가 동시에 처음 뜨면 서로 겹칠 수 있어 한 번 더 시도한다."""
    for attempt in range(3):
        try:
            Base.metadata.create_all(engine)
            return
        except (IntegrityError, ProgrammingError, OperationalError):
            if attempt == 2:
                raise


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def reset_engine(url: Optional[str] = None) -> Engine:
    """테스트용: 지정한 URL 로 엔진을 새로 만든다."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    return _engine
