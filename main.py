"""팀장 리더십 리플렉션 v4 — FastAPI 진입점 (Vercel Python 런타임).

로컬 실행:
    uvicorn main:app --reload        # DATABASE_URL 이 없으면 .local.db(SQLite), 입장 키는 ENTRY_KEY 또는 'dev'

- Vercel 에서는 `public/` 정적 파일을 CDN 이 제공한다(이 앱은 /api/* 만 처리).
- 로컬(uvicorn)에서는 Vercel CDN 이 없으므로 이 앱이 `public/` 을 직접 내려준다.
- 요청 본문(응답·문장)·입장 키·User-Agent 원문은 DB와 로그에 남기지 않는다.

사용자 API 요청 헤더
    X-Entry-Key       안내 링크의 입장 키(브라우저 저장소에 보관해 매 요청에 보냄)
    X-Participant-Id  서버가 만든 참여자 UUID(리더명을 정한 뒤)
    X-Device-Id       브라우저 저장소의 무작위 UUID(리더명 전 단계 이벤트용)
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import math
import os
import re
import secrets
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import anonymize
import db
import rules

BASE_DIR = Path(__file__).resolve().parent
CONTENT_PATH = BASE_DIR / "content" / "content.json"
PUBLIC_DIR = BASE_DIR / "public"
ON_VERCEL = bool(os.getenv("VERCEL"))
LOCAL_DEV_ENTRY_KEY = "dev"

PRESET_TYPES = ("rc", "re", "oa", "mi")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
MOBILE_UA_RE = re.compile(r"Mobi|Android|iPhone|iPad|iPod", re.I)


def app_version() -> str:
    """화면 하단 버전. APP_VERSION(Git 태그와 맞춤)이 없으면 Vercel 커밋 앞 7자리, 로컬은 dev."""
    v = os.getenv("APP_VERSION")
    if v:
        return v
    sha = os.getenv("VERCEL_GIT_COMMIT_SHA")
    return f"dev-{sha[:7]}" if sha else "dev"


# 보안 헤더 — vercel.json 의 정적 파일 헤더와 같은 값(로컬에서도 같은 조건으로 확인하려고 여기서도 붙인다)
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
                                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@lru_cache(maxsize=1)
def load_content() -> dict:
    with open(CONTENT_PATH, encoding="utf-8") as f:
        return json.load(f)


app = FastAPI(title="leader-reflection-v4", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path in LOCAL_NO_CACHE:
        # 로컬에서만 쓰는 경로(Vercel 은 vercel.json 같은 규칙). 없으면 브라우저가 고치기 전 app.js 를 계속 씀
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


LOCAL_NO_CACHE = {"/", "/admin", "/index.html", "/admin.html", "/app.js", "/app.css", "/admin.js", "/admin.css", "/result-pdf.js"}


# --------------------------------------------------------------------------- #
# 공통 도우미
# --------------------------------------------------------------------------- #
def get_db():
    session = db.get_session()
    try:
        yield session
    finally:
        session.close()


def error(status: int, code: str, message: Optional[str] = None) -> HTTPException:
    detail: dict[str, Any] = {"error": code}
    if message:
        detail["message"] = message
    return HTTPException(status_code=status, detail=detail)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
    return JSONResponse(body, status_code=exc.status_code, headers={"Cache-Control": "no-store"})


def valid_uuid(value: Optional[str]) -> Optional[str]:
    if value and UUID_RE.match(value.strip().lower()):
        return value.strip().lower()
    return None


def device_kind(request: Request) -> str:
    """User-Agent 원문은 저장하지 않고 모바일/PC 값만 쓴다."""
    return "mobile" if MOBILE_UA_RE.search(request.headers.get("user-agent", "")) else "pc"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def is_closed(settings: dict, now: Optional[dt.datetime] = None) -> bool:
    deadline = settings.get("deadline")
    if not deadline:
        return False
    try:
        d = dt.datetime.fromisoformat(str(deadline))
    except ValueError:
        return False
    if len(str(deadline)) <= 10:  # 날짜만 주면 그날 끝까지(한국 시간)
        d = d + dt.timedelta(days=1)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
    return (now or utcnow()) >= d


def configured_entry_key(settings: dict) -> Optional[str]:
    """관리자 설정 값이 우선, 없으면 환경 변수. 로컬에서 둘 다 없으면 'dev'. Vercel에서 없으면 None(전부 막음)."""
    key = settings.get("entry_key") or os.getenv("ENTRY_KEY")
    if not key and not ON_VERCEL:
        key = LOCAL_DEV_ENTRY_KEY
    return key or None


def entry_key_ok(request: Request, settings: dict) -> bool:
    expected = configured_entry_key(settings)
    given = request.headers.get("x-entry-key", "")
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def record_event(session: Session, name: str, request: Request, *, participant_id: Optional[str] = None,
                 props: Optional[dict] = None, cycle_id: Optional[str] = None) -> None:
    session.add(db.Event(
        name=name,
        cycle_id=cycle_id,
        participant_id=participant_id,
        device_id=None if participant_id else valid_uuid(request.headers.get("x-device-id")),
        device=device_kind(request),
        props=props or {},
    ))


# --------------------------------------------------------------------------- #
# 접근 확인 (Depends)
# --------------------------------------------------------------------------- #
class Ctx:
    def __init__(self, session: Session, settings: dict):
        self.session = session
        self.settings = settings


def require_entry(request: Request, session: Session = Depends(get_db)) -> Ctx:
    """입장 키가 맞고 마감 전이어야 한다."""
    settings = db.get_settings(session)
    if not entry_key_ok(request, settings):
        raise error(403, "entry_denied", load_content()["notice"]["entry_denied"])
    if is_closed(settings):
        raise error(403, "closed", load_content()["notice"]["closed"])
    return Ctx(session, settings)


def require_participant(request: Request, ctx: Ctx = Depends(require_entry)) -> tuple[Ctx, db.Participant]:
    pid = valid_uuid(request.headers.get("x-participant-id"))
    participant = ctx.session.get(db.Participant, pid) if pid else None
    if participant is None or participant.cycle_id != ctx.settings["cycle_id"]:
        raise error(404, "participant_not_found")
    return ctx, participant


# --------------------------------------------------------------------------- #
# 공통 API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> JSONResponse:
    """배포 확인용. DB 종류만 알려 주고 연결 주소·오류 내용은 내보내지 않는다."""
    try:
        engine = db.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — 상세 오류는 로그에도 남기지 않는다(주소에 암호가 있을 수 있음)
        return JSONResponse({"ok": False, "version": app_version(), "db": "unavailable"}, status_code=503)
    return JSONResponse({"ok": True, "version": app_version(), "db": engine.dialect.name})


@app.get("/api/content")
def content(request: Request, session: Session = Depends(get_db)) -> JSONResponse:
    """입장 확인 겸 문구 내려받기. 키가 없거나 틀리면 entry_denied 문구만, 마감이면 closed 문구만."""
    settings = db.get_settings(session)
    notice = load_content()["notice"]
    headers = {"Cache-Control": "no-store"}
    if not entry_key_ok(request, settings):
        reason = "missing" if not request.headers.get("x-entry-key") else "wrong"
        record_event(session, "entry_denied", request, props={"reason": reason}, cycle_id=settings["cycle_id"])
        session.commit()
        return JSONResponse({"error": "entry_denied", "message": notice["entry_denied"], "version": app_version()},
                            status_code=403, headers=headers)
    if is_closed(settings):
        return JSONResponse({"closed": True, "message": notice["closed"], "version": app_version()},
                            headers=headers)
    return JSONResponse({
        "version": app_version(),
        "closed": False,
        "settings": {"cycle_name": settings["cycle_name"], "duration_min": settings["duration_min"],
                     "dwell_cutoff_sec": settings["dwell_cutoff_sec"]},
        "content": load_content(),
    }, headers=headers)


# --------------------------------------------------------------------------- #
# 참여자(리더명) — SPEC 3.1, 5.2
# --------------------------------------------------------------------------- #
class LeaderNameIn(BaseModel):
    leader_type: Literal["rc", "re", "oa", "mi", "custom"]
    custom_name: Optional[str] = Field(default=None, max_length=100)
    option_order: list[str] = Field(default_factory=list, max_length=4)


def resolve_leader_name(body: LeaderNameIn) -> str:
    ln = load_content()["leader_name"]
    if body.leader_type == "custom":
        custom = ln["custom"]
        try:
            return anonymize.validate_custom_leader_name(body.custom_name or "", custom["min"], custom["max"])
        except anonymize.LeaderNameError as e:
            if e.code == "blocked":
                raise error(422, "leader_name_blocked", custom["blocked_message"])
            raise error(422, "leader_name_length", custom["help"])
    labels = {o["type"]: o["label"] for o in ln["options"]}
    return anonymize.to_call_form(labels[body.leader_type])


def check_option_order(order: list[str]) -> list[str]:
    if sorted(order) != sorted(PRESET_TYPES):
        raise error(422, "bad_option_order")
    return order


def assign_serial(session: Session, cycle_id: str, name_norm: str) -> str:
    with session.no_autoflush:  # 아직 번호가 없는(또는 바뀌는) 참여자를 먼저 쓰지 않도록
        taken = set(session.scalars(
            select(db.Participant.serial).where(db.Participant.cycle_id == cycle_id,
                                                db.Participant.leader_name_norm == name_norm)
        ))
    for _ in range(50):
        serial = f"{secrets.randbelow(9000) + 1000}"
        if serial not in taken:
            return serial
    raise error(503, "serial_exhausted")


def commit_with_serial(session: Session, participant: db.Participant, cycle_id: str, name_norm: str) -> None:
    """구분 번호가 동시에 겹치면(유니크 제약) 다시 뽑는다."""
    for _ in range(5):
        participant.serial = assign_serial(session, cycle_id, name_norm)
        try:
            session.commit()
            return
        except IntegrityError:
            session.rollback()
            session.add(participant)
    raise error(503, "serial_conflict")


def participant_view(p: db.Participant) -> dict:
    # 구분 번호는 관리자 화면에만 표시하므로 사용자에게 돌려주지 않는다
    return {"participant_id": p.id, "leader_name": p.leader_name, "leader_type": p.leader_type,
            "option_order": p.option_order}


@app.post("/api/participants", status_code=201)
def create_participant(body: LeaderNameIn, request: Request, ctx: Ctx = Depends(require_entry)) -> dict:
    """리더명 화면의 화살표 → 참여자 생성. 참여자 ID 는 서버가 만든 무작위 UUID."""
    session = ctx.session
    name = resolve_leader_name(body)
    norm = anonymize.name_key(name)
    p = db.Participant(
        id=str(uuid.uuid4()),
        cycle_id=ctx.settings["cycle_id"],
        leader_name=name,
        leader_name_norm=norm,
        leader_type=body.leader_type,
        option_order=check_option_order(body.option_order),
        device=device_kind(request),
    )
    session.add(p)
    commit_with_serial(session, p, ctx.settings["cycle_id"], norm)
    return participant_view(p)


@app.put("/api/participants/me")
def update_leader_name(body: LeaderNameIn, pc=Depends(require_participant)) -> dict:
    """리더명 다시 고르기. STEP 1 완료 전까지만."""
    ctx, p = pc
    session = ctx.session
    step1 = session.get(db.Step1, p.id)
    if step1 is not None and step1.completed_at is not None:
        raise error(409, "leader_name_locked")
    name = resolve_leader_name(body)
    norm = anonymize.name_key(name)
    if body.option_order:
        p.option_order = check_option_order(body.option_order)
    changed = name != p.leader_name or body.leader_type != p.leader_type
    if changed:
        p.name_changes = (p.name_changes or 0) + 1
    p.leader_type = body.leader_type
    p.leader_name = name
    if norm != p.leader_name_norm:
        p.leader_name_norm = norm
        commit_with_serial(session, p, ctx.settings["cycle_id"], norm)
    else:
        session.commit()
    return participant_view(p)


# --------------------------------------------------------------------------- #
# 화면 ID·답 코드 (문구가 아닌 식별자라 코드에 둔다)
# --------------------------------------------------------------------------- #
SCREEN_IDS = (
    "notice", "leader_name", "intro",
    "review_1", "review_2", "review_3", "review_4", "review_5", "keyword",
    "member", "check_1", "check_2", "check_3", "check_4", "check_5",
    "analyzing", "result_1", "result_2", "result_3", "action", "done",
)
STEP1_SCREENS = ("intro", "review_1", "review_2", "review_3", "review_4", "review_5", "keyword")
STEP2_SCREENS = ("member", "check_1", "check_2", "check_3", "check_4", "check_5",
                 "analyzing", "result_1", "result_2", "result_3", "action")
RECALL_VALUES = ("remembered", "not_remembered")
ANSWER_VALUES = {"behavior": ("done", "not_done"), "signal": ("yes", "no")}
KEYWORD_NONE = "kw_none"   # '잘 떠오르지 않아요'(단독 선택)
SITUATION_NONE = "none"    # '특별한 상황은 없었어요'(단독 선택)
ACTION_TYPES = ("recommended", "other_preset", "custom")


def review_ids() -> list[str]:
    return [r["id"] for r in load_content()["reviews"]]


def items_by_id() -> dict[str, dict]:
    return {i["id"]: i for i in load_content()["items"]}


def check_keywords(keywords: list[str]) -> list[str]:
    c = load_content()["keywords"]
    valid = {k["id"] for k in c["items"]}
    if keywords == [KEYWORD_NONE]:
        return keywords
    if not (1 <= len(keywords) <= 3) or len(set(keywords)) != len(keywords) or not set(keywords) <= valid:
        raise error(422, "bad_keywords")
    return keywords


def check_situations(situations: list[str]) -> list[str]:
    valid = {s["id"] for s in load_content()["member"]["situations"]}
    if situations == [SITUATION_NONE]:
        return situations
    if (not situations or len(set(situations)) != len(situations) or not set(situations) <= valid
            or SITUATION_NONE in situations):
        raise error(422, "bad_situations")
    return situations


def check_answers(answers: dict[str, str]) -> dict[str, str]:
    items = items_by_id()
    for item_id, value in answers.items():
        item = items.get(item_id)
        if item is None or value not in ANSWER_VALUES[item["type"]]:
            raise error(422, "bad_answer", item_id)
    return answers


def check_action_key(key: str) -> str:
    kind, _, ref = key.partition(":")
    if kind == "situation" and ref in load_content()["situation_phrases"]:
        return key
    if kind == "item" and ref in items_by_id() and items_by_id()[ref].get("phrase"):   # 행동 문항·부족 신호(2026-11-27)
        return key
    if kind == "general" and ref in (load_content().get("general_phrases") or {}) and not ref.startswith("_"):
        return key
    raise error(422, "bad_action_key")


# --------------------------------------------------------------------------- #
# STEP 1 저장 — 핵심 메시지 1~5 기억 여부, 키워드. '다음'을 누를 때마다 저장
# --------------------------------------------------------------------------- #
class Step1In(BaseModel):
    reviews: dict[str, Literal["remembered", "not_remembered"]] = Field(default_factory=dict, max_length=5)
    keywords: Optional[list[str]] = Field(default=None, max_length=3)
    last_screen: Optional[str] = None
    complete: bool = False


def step1_view(s: Optional[db.Step1]) -> Optional[dict]:
    if s is None:
        return None
    return {"done": s.completed_at is not None, "last_screen": s.last_screen,
            "reviews": s.reviews, "keywords": s.keywords}


@app.put("/api/step1")
def save_step1(body: Step1In, pc=Depends(require_participant)) -> dict:
    ctx, p = pc
    session = ctx.session
    s = session.get(db.Step1, p.id)
    if s is not None and s.completed_at is not None:
        raise error(409, "step1_done")
    if s is None:
        s = db.Step1(participant_id=p.id, reviews={}, keywords=[])
        session.add(s)
    if not set(body.reviews) <= set(review_ids()):
        raise error(422, "bad_review")
    if body.last_screen is not None and body.last_screen not in STEP1_SCREENS:
        raise error(422, "bad_screen")
    s.reviews = {**(s.reviews or {}), **body.reviews}
    if body.keywords is not None:
        s.keywords = check_keywords(body.keywords)
    if body.last_screen:
        s.last_screen = body.last_screen
    if body.complete:
        if set(s.reviews) != set(review_ids()):
            raise error(422, "step1_incomplete", "reviews")
        check_keywords(s.keywords or [])
        s.completed_at = utcnow()
    session.commit()
    return step1_view(s)


# --------------------------------------------------------------------------- #
# STEP 2 점검 — 구성원마다 1건. 별명은 받지 않는다
# --------------------------------------------------------------------------- #
class CheckStartIn(BaseModel):
    situations: list[str] = Field(max_length=8)


class CheckPatchIn(BaseModel):
    situations: Optional[list[str]] = Field(default=None, max_length=8)
    answers: dict[str, str] = Field(default_factory=dict, max_length=17)
    last_screen: Optional[str] = None
    action_type: Optional[Literal["recommended", "other_preset", "custom"]] = None
    action_key: Optional[str] = Field(default=None, max_length=40)
    action_text: Optional[str] = Field(default=None, max_length=300)
    complete: bool = False


def check_view(c: db.Check) -> dict:
    return {"seq": c.seq, "status": c.status, "last_screen": c.last_screen, "situations": c.situations,
            "answers": c.answers, "results": c.results, "action_type": c.action_type,
            "action_key": c.action_key, "action_text": c.action_text,
            "quadrant": (c.judgment or {}).get("quadrant")}


# 점검 순번(경로 값). 범위 밖 숫자는 DB 에 닿기 전에 422 로 막는다(아주 큰 수는 DB 정수 범위를 넘어 500 이 남)
Seq = Annotated[int, PathParam(ge=1, le=1000)]


def get_check(session: Session, participant_id: str, seq: int) -> db.Check:
    c = session.scalar(select(db.Check).where(db.Check.participant_id == participant_id, db.Check.seq == seq))
    if c is None:
        raise error(404, "check_not_found")
    return c


@app.post("/api/checks", status_code=201)
def start_check(body: CheckStartIn, pc=Depends(require_participant)) -> dict:
    """구성원 떠올리기 '다음' → 새 점검. STEP 1 을 끝낸 뒤에만, 진행 중인 점검이 없을 때만."""
    ctx, p = pc
    session = ctx.session
    s = session.get(db.Step1, p.id)
    if s is None or s.completed_at is None:
        raise error(409, "step1_not_done")
    checks = session.scalars(select(db.Check).where(db.Check.participant_id == p.id)).all()
    current = next((c for c in checks if c.status == "in_progress"), None)
    if current is not None:
        raise error(409, "check_in_progress", str(current.seq))
    c = db.Check(id=str(uuid.uuid4()), participant_id=p.id, seq=max((x.seq for x in checks), default=0) + 1,
                 situations=check_situations(body.situations), answers={}, judgment={}, results={},
                 status="in_progress", last_screen="member")
    session.add(c)
    try:
        session.commit()
    except IntegrityError:  # 같은 순번을 동시에 만든 경우
        session.rollback()
        raise error(409, "check_conflict")
    return check_view(c)


@app.patch("/api/checks/{seq}")
def save_check(seq: Seq, body: CheckPatchIn, pc=Depends(require_participant)) -> dict:
    """체크 페이지 '다음', 결과 화면 이동, 실천 '완료'마다 저장."""
    ctx, p = pc
    session = ctx.session
    c = get_check(session, p.id, seq)
    if c.status == "done":
        raise error(409, "check_done")
    if body.last_screen is not None and body.last_screen not in STEP2_SCREENS:
        raise error(422, "bad_screen")
    changed = False
    if body.situations is not None:
        situations = check_situations(body.situations)
        changed = changed or situations != (c.situations or [])
        c.situations = situations
    if body.answers:
        answers = {**(c.answers or {}), **check_answers(body.answers)}
        changed = changed or answers != (c.answers or {})
        c.answers = answers
    if changed and c.results:
        # 결과를 본 뒤 답·상황이 바뀌면 예전 결과를 지운다. 이어 하기에서 예전 결과·예전 실천 보기가 보이지 않고
        # 결과 정리(GET /result)에서 다시 계산된다
        c.judgment, c.results = {}, {}
    if body.last_screen:
        c.last_screen = body.last_screen
    if body.action_type is not None:
        c.action_type = body.action_type
        if body.action_type == "custom":
            A = load_content()["action"]
            text_ = anonymize.scrub_text(body.action_text or "")
            # 화면은 입력을 custom_maxlength 자로 막지만, 한 글자 별명을 ○○(두 글자)로 바꾸면 길어질 수 있어 두 배까지 받는다
            if not text_ or len(text_) > A["custom_maxlength"] * 2:
                emptied = not text_ and bool(anonymize.collapse_spaces(body.action_text or ""))
                raise error(422, "bad_action_text", A["scrubbed_empty"] if emptied else A["blocked_hint"])
            c.action_text, c.action_key = text_, None
        else:
            c.action_key, c.action_text = check_action_key(body.action_key or ""), None
    if body.complete:
        if set(c.answers or {}) != set(items_by_id()):
            raise error(422, "check_incomplete", "answers")
        if c.action_type is None:
            raise error(422, "check_incomplete", "action")
        apply_rules(session, ctx.settings, p, c)
        # 실천 보기는 결과 계산이 정한 (유형, 문장) 짝 중 하나여야 한다(관리자 '꼭 해야 하는 말 유형' 집계)
        options = {(o["type"], o["key"]) for o in c.results["action_options"]}
        if c.action_type != "custom" and (c.action_type, c.action_key) not in options:
            raise error(422, "bad_action_key")
        c.status = "done"
        c.last_screen = "done"
        c.completed_at = utcnow()
    session.commit()
    return check_view(c)


def apply_rules(session: Session, settings: dict, p: db.Participant, c: db.Check) -> None:
    """17문항이 모두 있을 때 판정·결과 1~3 을 계산해 점검에 저장한다(SPEC 4장)."""
    if set(c.answers or {}) != set(items_by_id()):
        raise error(422, "check_incomplete", "answers")
    step1 = session.get(db.Step1, p.id)
    out = rules.evaluate(c.answers, c.situations or [], (step1.keywords if step1 else []) or [],
                         load_content(), float(settings.get("high_threshold") or 0.5))
    c.judgment, c.results = out["judgment"], out["results"]


@app.get("/api/checks/{seq}/result")
def check_result(seq: Seq, pc=Depends(require_participant)) -> dict:
    """결과 정리 화면에서 호출. 계산 결과를 저장하고 돌려준다(완료한 점검은 저장된 값)."""
    ctx, p = pc
    c = get_check(ctx.session, p.id, seq)
    if c.status != "done":
        apply_rules(ctx.session, ctx.settings, p, c)
        ctx.session.commit()
    return {"seq": c.seq, "quadrant": c.judgment["quadrant"], "results": c.results}


@app.delete("/api/checks/{seq}")
def restart_check(seq: Seq, pc=Depends(require_participant)) -> dict:
    """'처음부터 다시': 진행 중인 그 구성원 점검을 지운다."""
    ctx, p = pc
    c = get_check(ctx.session, p.id, seq)
    if c.status == "done":
        raise error(409, "check_done")
    ctx.session.delete(c)
    ctx.session.commit()
    return {"deleted": seq}


# --------------------------------------------------------------------------- #
# 이벤트 — SPEC 5.3. 정해 둔 이벤트·속성만 저장하고 나머지는 버린다(별명·문구 유입 방지)
# --------------------------------------------------------------------------- #
EVENT_PROPS: dict[str, tuple[str, ...]] = {
    # visit: 화면에 들어올 때마다 새로 만드는 무작위 ID. 탭을 숨길 때 중간값(final=false)을, 화면을 떠날 때
    # 최종값(final=true)을 보낸다. 집계는 visit 별 마지막 값 기준(창을 그냥 닫아도 중간값이 남음)
    "screen_view": ("screen", "entered_at", "left_at", "visible_ms", "visit", "final"),
    "leader_name_pick": ("leader_type", "option_order", "changes"),
    "review_recall": ("review", "value"),
    "keyword_pick": ("keyword", "order", "position"),   # position: 화면에 보인 자리(2026-11-27 보기 순서 섞기)
    "nickname_method": ("method", "regenerate_count"),
    "answer_changed": ("item",),
    "answer_changed_after_result": ("item", "before", "after"),
    "blocked_next_tap": ("screen",),
    "result_back_to_checklist": (),
    "action_pick": ("kind",),
    "action_custom_abandoned": (),
    "action_custom_text": ("text",),
    "result_share_opened": (), "result_shared": (), "result_share_cancelled": (), "result_pdf_saved": (),
    "recommend_opened": (), "recommend_shared": (), "recommend_cancelled": (), "recommend_copied": (),
    "visit_from_recommend": (),
    "another_member_started": (),
    # 완료 화면 3칸을 누를 때마다(성공 여부와 별개). button: again / pdf / recommend
    "done_button_click": ("button",),
    "back_nav": ("from", "to"),
    "resume": ("screen",),
    "revisit_after_done": (),
    # 이어 하기 안내에서 '다른 리더예요'(공용 기기). 이 브라우저의 기록만 지운다
    "leader_switch": (),
    # 메신저·메일 앱 안 브라우저로 연 세션(브라우저 종류만: kakaotalk, teams, webview, ios_webview …)
    "inapp_browser": ("browser",),
    "error": ("kind", "browser"),
}
SHORT_STR = 40


def clean_prop(name: str, key: str, value: Any) -> Any:
    if name == "action_custom_text" and key == "text":
        return anonymize.scrub_text(str(value))[:100] if isinstance(value, str) else None
    if key == "screen" and value not in SCREEN_IDS:
        return None
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None   # Infinity·NaN 은 JSON 으로 저장할 수 없다
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:SHORT_STR]
    if isinstance(value, list) and len(value) <= 10 and all(isinstance(v, str) for v in value):
        return [v[:SHORT_STR] for v in value]
    return None


class EventIn(BaseModel):
    name: str = Field(max_length=40)
    props: dict[str, Any] = Field(default_factory=dict, max_length=10)
    session_id: Optional[str] = None
    check_seq: Optional[int] = Field(default=None, ge=1, le=1000)


class EventsIn(BaseModel):
    events: list[EventIn] = Field(max_length=50)


@app.post("/api/events")
def save_events(body: EventsIn, request: Request, ctx: Ctx = Depends(require_entry)) -> dict:
    """리더명 전에는 기기 ID 로, 이후에는 참여자 ID 로 기록한다."""
    session = ctx.session
    pid = valid_uuid(request.headers.get("x-participant-id"))
    if pid and session.get(db.Participant, pid) is None:
        pid = None
    # 기기 ID(브라우저 저장소의 무작위 값)는 늘 함께 남긴다: 안내 방문·추천 유입을 기기 수로 세기 위해
    device_id = valid_uuid(request.headers.get("x-device-id"))
    device = device_kind(request)
    saved = 0
    for ev in body.events:
        allowed = EVENT_PROPS.get(ev.name)
        if allowed is None:
            continue
        props = {k: clean_prop(ev.name, k, v) for k, v in ev.props.items() if k in allowed}
        session.add(db.Event(name=ev.name, cycle_id=ctx.settings["cycle_id"], participant_id=pid, device_id=device_id,
                             session_id=valid_uuid(ev.session_id), check_seq=ev.check_seq,
                             device=device, props=props))
        saved += 1
    session.commit()
    return {"saved": saved, "dropped": len(body.events) - saved}


# --------------------------------------------------------------------------- #
# 이어 하기 — SPEC 6장
# --------------------------------------------------------------------------- #
@app.get("/api/me")
def me(pc=Depends(require_participant)) -> dict:
    """같은 기기·브라우저(브라우저 저장소의 참여자 ID)로 다시 들어왔을 때 멈춘 곳을 알려 준다."""
    ctx, p = pc
    session = ctx.session
    step1 = session.get(db.Step1, p.id)
    checks = session.scalars(
        select(db.Check).where(db.Check.participant_id == p.id).order_by(db.Check.seq)
    ).all()
    in_progress = next((c for c in reversed(checks) if c.status == "in_progress"), None)
    p.last_seen_at = utcnow()
    session.commit()
    return {
        **participant_view(p),
        "leader_name_locked": bool(step1 and step1.completed_at),
        "step1": step1_view(step1),
        "checks_done": sum(1 for c in checks if c.status == "done"),
        "check_in_progress": None if in_progress is None else check_view(in_progress),
    }


# --------------------------------------------------------------------------- #
# 관리자 API (/api/admin/*)
# --------------------------------------------------------------------------- #
import admin_api  # noqa: E402

app.include_router(admin_api.router)


# --------------------------------------------------------------------------- #
# 로컬 전용: public/ 정적 파일 제공 (Vercel 에서는 CDN 이 처리)
# --------------------------------------------------------------------------- #
if not ON_VERCEL:

    @app.get("/", include_in_schema=False)
    def local_index() -> FileResponse:
        return FileResponse(PUBLIC_DIR / "index.html")

    @app.get("/admin", include_in_schema=False)
    def local_admin() -> FileResponse:
        return FileResponse(PUBLIC_DIR / "admin.html")

    # app.js, app.css 등. API 라우트 뒤에 두어 /api/* 를 가리지 않게 한다
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=PUBLIC_DIR), name="public")
