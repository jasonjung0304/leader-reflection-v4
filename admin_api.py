"""관리자 API (/api/admin/*) — SPEC 8장.

권한
- 로그인 쿠키가 없으면 401. 읽기 전용(HR 임원)은 설정·삭제·CSV 내보내기에서 403(서버에서 막음)
- 값을 바꾸는 요청(POST·PUT)은 X-Admin-Request: 1 헤더가 있어야 한다(쿠키 SameSite=Strict 와 함께 사이트 밖 요청 차단)

개인정보
- 참여자 탭은 리더명·구분 번호·진행 상태·점검별 4분면 경향만. 체크 응답·직접 쓴 문장은 리더명과 함께 돌려주지 않는다
- 참여자 ID(UUID)는 리더의 이어 하기 열쇠이므로 관리자에게도 내보내지 않는다(삭제는 리더명 + 구분 번호로)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

import admin_auth
import admin_stats
import db

BASE_DIR = Path(__file__).resolve().parent
router = APIRouter(prefix="/api/admin")


@lru_cache(maxsize=1)
def content() -> dict:
    with open(BASE_DIR / "content" / "content.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def admin_content() -> dict:
    with open(BASE_DIR / "content" / "admin.json", encoding="utf-8") as f:
        return json.load(f)


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


class Admin:
    def __init__(self, role: str, session: Session, settings: dict):
        self.role, self.session, self.settings = role, session, settings


def require_admin(request: Request, session: Session = Depends(get_db)) -> Admin:
    settings = db.get_settings(session)
    role = admin_auth.read_cookie(request.cookies.get(admin_auth.COOKIE), settings)
    if role is None:
        raise error(401, "admin_login_required")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-admin-request") != "1":
        raise error(403, "admin_bad_request")
    return Admin(role, session, settings)


def require_link(request: Request, settings: dict) -> None:
    """비밀 관리자 링크(2026-11-27): 링크 키를 정해 두었으면 그 키가 없는 브라우저에는 로그인 화면도 없는 것처럼(404)."""
    if not admin_auth.link_ok(request.headers.get("x-admin-link"), settings):
        raise error(404, "not_found")


def set_login_cookie(response: Response, role: str, settings: dict) -> None:
    response.set_cookie(admin_auth.COOKIE, admin_auth.make_cookie(role, settings), max_age=admin_auth.MAX_AGE,
                        httponly=True, secure=True, samesite="strict", path="/")


def require_full(a: Admin = Depends(require_admin)) -> Admin:
    if a.role != "full":
        raise error(403, "admin_readonly")
    return a


def no_store(resp: Response) -> None:
    resp.headers["Cache-Control"] = "no-store"


# --------------------------------------------------------------------------- #
# 로그인
# --------------------------------------------------------------------------- #
class LoginIn(BaseModel):
    password: str = Field(max_length=200)


@router.get("/login-text")
def login_text(request: Request, response: Response, session: Session = Depends(get_db)) -> dict:
    """로그인 전에 필요한 문구만(데이터 없음). 비밀 링크로 열지 않았으면 404."""
    response.headers["Cache-Control"] = "no-store"
    require_link(request, db.get_settings(session))
    return {"app": {k: admin_content()["app"][k] for k in ("title", "badge")}, "login": admin_content()["login"]}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, session: Session = Depends(get_db)) -> dict:
    if request.headers.get("x-admin-request") != "1":
        raise error(403, "admin_bad_request")
    settings = db.get_settings(session)
    require_link(request, settings)
    if not admin_auth.configured(settings):
        raise error(503, "admin_not_configured")
    until = admin_auth.locked_until(session)
    if until:
        raise error(423, "admin_locked", until.isoformat())
    role = admin_auth.role_for_password(body.password, settings)
    if role is None:
        until = admin_auth.record_failure(session)
        if until:
            raise error(423, "admin_locked", until.isoformat())
        raise error(401, "admin_wrong_password")
    admin_auth.record_success(session)
    set_login_cookie(response, role, settings)
    no_store(response)
    return {"role": role}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(admin_auth.COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"ok": True}


@router.get("/me")
def me(response: Response, a: Admin = Depends(require_admin)) -> dict:
    no_store(response)
    cycles = sorted(set(a.session.scalars(select(db.Participant.cycle_id).distinct())) | {a.settings["cycle_id"]})
    return {"role": a.role, "cycle_id": a.settings["cycle_id"], "cycle_name": a.settings["cycle_name"], "cycles": cycles,
            "target_count": a.settings["target_count"]}


@router.get("/content")
def admin_texts(response: Response, a: Admin = Depends(require_admin)) -> dict:
    no_store(response)
    return {"admin": admin_content(), "content": content()}


# --------------------------------------------------------------------------- #
# 집계 데이터 불러오기
# --------------------------------------------------------------------------- #
def load(a: Admin, cycle: Optional[str]) -> tuple[list, list, list, list, str]:
    s = a.session
    cycle_id = cycle or a.settings["cycle_id"]
    ps = s.scalars(select(db.Participant).where(db.Participant.cycle_id == cycle_id)).all()
    pids = [p.id for p in ps]
    participants = [{"id": p.id, "leader_name": p.leader_name, "serial": p.serial, "leader_type": p.leader_type,
                     "device": p.device, "created_at": p.created_at, "last_seen_at": p.last_seen_at} for p in ps]
    step1 = [{"participant_id": x.participant_id, "reviews": x.reviews, "keywords": x.keywords, "completed_at": x.completed_at}
             for x in s.scalars(select(db.Step1).where(db.Step1.participant_id.in_(pids))).all()] if pids else []
    checks = [{"participant_id": c.participant_id, "seq": c.seq, "situations": c.situations, "answers": c.answers,
               "judgment": c.judgment, "results": c.results, "action_type": c.action_type, "action_key": c.action_key,
               "action_text": c.action_text, "status": c.status, "created_at": c.created_at, "completed_at": c.completed_at}
              for c in s.scalars(select(db.Check).where(db.Check.participant_id.in_(pids))).all()] if pids else []
    cond = db.Event.cycle_id == cycle_id
    if pids:
        cond = or_(cond, db.Event.participant_id.in_(pids))
    events = [{"id": e.id, "ts": e.ts, "name": e.name, "participant_id": e.participant_id, "device_id": e.device_id,
               "session_id": e.session_id, "check_seq": e.check_seq, "device": e.device, "props": e.props or {}}
              for e in s.scalars(select(db.Event).where(cond)).all()]
    return participants, step1, checks, events, cycle_id


def parse_date(v: Optional[str]) -> Optional[dt.date]:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise error(422, "bad_date")


def data(a: Admin, cycle: Optional[str], type: Optional[str], date_from: Optional[str], date_to: Optional[str]) -> admin_stats.Data:
    if type and type not in admin_stats.TYPES:
        raise error(422, "bad_type")
    participants, step1, checks, events, cycle_id = load(a, cycle)
    settings = dict(a.settings, cycle_id=cycle_id)
    return admin_stats.Data(participants, step1, checks, events, content(), settings, leader_type=type,
                            date_from=parse_date(date_from), date_to=parse_date(date_to))


@router.get("/dashboard")
def dashboard(response: Response, cycle: Optional[str] = None, type: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None, a: Admin = Depends(require_admin)) -> dict:
    no_store(response)
    return admin_stats.dashboard(data(a, cycle, type, date_from, date_to))


@router.get("/items")
def items(response: Response, cycle: Optional[str] = None, type: Optional[str] = None,
          date_from: Optional[str] = None, date_to: Optional[str] = None, a: Admin = Depends(require_admin)) -> dict:
    no_store(response)
    out = admin_stats.items_tab(data(a, cycle, type, date_from, date_to))
    out["custom_text_count"] = len(out["custom_texts"])
    out["can_export"] = a.role == "full"   # 읽기 전용은 목록은 보되 내보내기는 서버에서도 403
    return out


@router.get("/leaders")
def leaders(response: Response, cycle: Optional[str] = None, type: Optional[str] = None,
            date_from: Optional[str] = None, date_to: Optional[str] = None, stage: Optional[str] = None,
            q: Optional[str] = None, sort: str = "last_access", desc: bool = True, page: int = 1, size: int = 20,
            a: Admin = Depends(require_admin)) -> dict:
    no_store(response)
    return admin_stats.leaders_tab(data(a, cycle, type, date_from, date_to), stage=stage, q=q, sort=sort, desc=desc,
                                   page=page, size=size)


@router.get("/flow")
def flow(response: Response, cycle: Optional[str] = None, type: Optional[str] = None,
         date_from: Optional[str] = None, date_to: Optional[str] = None, a: Admin = Depends(require_admin)) -> dict:
    no_store(response)
    return admin_stats.flow_tab(data(a, cycle, type, date_from, date_to))


# --------------------------------------------------------------------------- #
# 설정 (전체 권한만)
# --------------------------------------------------------------------------- #
ENTRY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CYCLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class SettingsIn(BaseModel):
    cycle_id: Optional[str] = None
    cycle_name: Optional[str] = Field(default=None, max_length=60)
    deadline: Optional[str] = None
    target_count: Optional[int] = Field(default=None, ge=1, le=10000)
    entry_key: Optional[str] = None
    dwell_cutoff_sec: Optional[int] = Field(default=None, ge=30, le=3600)
    high_threshold: Optional[float] = Field(default=None, ge=0.25, le=1.0)
    duration_min: Optional[int] = Field(default=None, ge=1, le=120)
    open_entry: Optional[bool] = None   # 입장 키 없이 들어오기(2026-11-27)
    clear: list[str] = Field(default_factory=list)   # 값을 비울 항목(마감일, 안내 '약 n분', 입장 키 없이 들어오기 → 환경 변수 따름)


def settings_view(settings: dict) -> dict:
    """지금 실제로 쓰이는 입장 키와 그 출처(main.configured_entry_key 와 같은 규칙)."""
    import os
    env_key = os.getenv("ENTRY_KEY")
    if settings.get("entry_key"):
        key, source = settings["entry_key"], "settings"
    elif env_key:
        key, source = env_key, "env"
    elif not admin_auth.on_vercel():
        key, source = "dev", "local"
    else:
        key, source = None, "none"
    is_open, open_source = db.open_entry(settings)
    link, link_source = admin_auth.link_key(settings)
    view = {k: v for k, v in settings.items() if k != "admin_password_hashes"}   # 암호 해시는 내보내지 않는다
    return {**view, "entry_key": key, "entry_key_source": source, "open_entry": is_open, "open_entry_source": open_source,
            "admin_link_key": link, "admin_link_source": link_source,
            "admin_password_source": {r: admin_auth.password_source(r, settings) for r in admin_auth.ROLES}}


@router.get("/settings")
def get_settings(response: Response, a: Admin = Depends(require_full)) -> dict:
    no_store(response)
    return settings_view(a.settings)


@router.get("/threshold-preview")
def threshold_preview(response: Response, cycle: Optional[str] = None, a: Admin = Depends(require_full)) -> dict:
    """설정 화면: 판정 기준값을 바꾸면 4분면 분포가 어떻게 달라지는지(지금까지의 응답으로 미리 계산)."""
    no_store(response)
    return admin_stats.threshold_preview(data(a, cycle, None, None, None))


@router.put("/settings")
def put_settings(body: SettingsIn, a: Admin = Depends(require_full)) -> dict:
    values = {k: v for k, v in body.model_dump(exclude={"clear"}).items() if v is not None}
    if "cycle_id" in values and not CYCLE_RE.match(values["cycle_id"]):
        raise error(422, "bad_cycle_id")
    if "cycle_name" in values and not values["cycle_name"].strip():
        raise error(422, "bad_cycle_name")
    if "entry_key" in values and not ENTRY_KEY_RE.match(values["entry_key"]):
        raise error(422, "bad_entry_key")
    if "deadline" in values:
        try:
            dt.datetime.fromisoformat(values["deadline"])
        except ValueError:
            raise error(422, "bad_deadline")
    for k in body.clear:
        if k not in ("deadline", "duration_min", "open_entry"):
            raise error(422, "bad_clear")
        values[k] = None
    db.save_settings(a.session, values)
    return settings_view(db.get_settings(a.session))


# --------------------------------------------------------------------------- #
# 비밀 관리자 링크·관리자 암호 (전체 권한만, 2026-11-27)
# --------------------------------------------------------------------------- #
class LinkIn(BaseModel):
    action: Literal["rotate", "off"]


@router.post("/link")
def admin_link(body: LinkIn, response: Response, a: Admin = Depends(require_full)) -> dict:
    """rotate: 새 관리자 링크 키를 만든다(예전 링크·다른 관리자의 로그인은 모두 무효).
    off: 관리자 화면에서 만든 키를 지운다(환경 변수 ADMIN_LINK_KEY 가 있으면 그 키로, 없으면 링크 확인 안 함)."""
    db.save_settings(a.session, {"admin_link_key": admin_auth.new_link_key() if body.action == "rotate" else None})
    settings = db.get_settings(a.session)
    set_login_cookie(response, a.role, settings)   # 바꾼 사람은 로그인 유지
    no_store(response)
    return settings_view(settings)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    role: Literal["full", "readonly"]
    new: str = Field(max_length=200)


@router.post("/password")
def change_password(body: PasswordIn, response: Response, a: Admin = Depends(require_full)) -> dict:
    """관리자 암호 바꾸기. 지금 전체 권한 암호를 한 번 더 확인하고, 새 암호는 PBKDF2 해시로만 저장한다.
    그 권한으로 로그인해 있던 다른 사람은 다시 로그인해야 한다(바꾼 사람은 로그인 유지)."""
    settings = a.settings
    until = admin_auth.locked_until(a.session)
    if until:
        raise error(423, "admin_locked", until.isoformat())
    if admin_auth.role_for_password(body.current, settings) != "full":
        until = admin_auth.record_failure(a.session)
        if until:
            raise error(423, "admin_locked", until.isoformat())
        raise error(401, "admin_wrong_password")
    new = body.new
    if len(new) < admin_auth.MIN_PASSWORD or new != new.strip():
        raise error(422, "password_too_short")
    other = "readonly" if body.role == "full" else "full"
    if admin_auth.role_for_password(new, settings) == other:
        raise error(422, "password_same_as_other")
    hashes = dict(settings.get("admin_password_hashes") or {})
    hashes[body.role] = admin_auth.hash_password(new)
    db.save_settings(a.session, {"admin_password_hashes": hashes})
    settings = db.get_settings(a.session)
    set_login_cookie(response, a.role, settings)
    no_store(response)
    return {"ok": True, "role": body.role, "admin_password_source": {r: admin_auth.password_source(r, settings) for r in admin_auth.ROLES}}


# --------------------------------------------------------------------------- #
# 삭제 (전체 권한만)
# --------------------------------------------------------------------------- #
class ParticipantRef(BaseModel):
    leader_name: str = Field(max_length=60)
    serial: str = Field(pattern=r"^\d{4}$")


def _delete_participants(session: Session, pids: list[str]) -> None:
    if not pids:
        return
    session.execute(delete(db.Event).where(db.Event.participant_id.in_(pids)))
    session.execute(delete(db.Check).where(db.Check.participant_id.in_(pids)))
    session.execute(delete(db.Step1).where(db.Step1.participant_id.in_(pids)))
    session.execute(delete(db.Participant).where(db.Participant.id.in_(pids)))


@router.post("/participants/delete")
def delete_participant(body: ParticipantRef, a: Admin = Depends(require_full)) -> dict:
    s = a.session
    p = s.scalar(select(db.Participant).where(db.Participant.cycle_id == a.settings["cycle_id"],
                                              db.Participant.leader_name == " ".join(body.leader_name.split()),
                                              db.Participant.serial == body.serial))
    if p is None:
        raise error(404, "participant_not_found")
    _delete_participants(s, [p.id])
    s.commit()
    return {"deleted": 1}


class DataDeleteIn(BaseModel):
    confirm: str = Field(max_length=60)


@router.post("/data/delete")
def delete_data(body: DataDeleteIn, a: Admin = Depends(require_full)) -> dict:
    """지금 차수의 응답·이벤트를 모두 지운다. 확인으로 차수 코드를 그대로 입력해야 한다."""
    s = a.session
    cycle_id = a.settings["cycle_id"]
    if body.confirm != cycle_id:
        raise error(422, "confirm_mismatch")
    pids = list(s.scalars(select(db.Participant.id).where(db.Participant.cycle_id == cycle_id)))
    _delete_participants(s, pids)
    s.execute(delete(db.Event).where(db.Event.cycle_id == cycle_id))
    s.commit()
    return {"deleted_participants": len(pids)}


class TextDeleteIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    cycle: Optional[str] = Field(default=None, max_length=40)


@router.post("/texts/delete")
def delete_text(body: TextDeleteIn, a: Admin = Depends(require_full)) -> dict:
    """직접 쓴 문장 하나를 지운다(구성원 실명처럼 개인을 알아볼 수 있는 말이 들어간 경우, 2026-11-27).

    같은 문장은 모두 지우고, 같은 문장을 담은 이벤트(action_custom_text)도 지운다.
    점검의 실천 유형('직접 썼다')은 남겨 실천 유형 집계(2.6)는 바뀌지 않는다.
    """
    s = a.session
    cycle_id = body.cycle or a.settings["cycle_id"]
    pids = list(s.scalars(select(db.Participant.id).where(db.Participant.cycle_id == cycle_id)))
    checks = 0
    if pids:
        for c in s.scalars(select(db.Check).where(db.Check.participant_id.in_(pids), db.Check.action_type == "custom",
                                                  db.Check.action_text == body.text)).all():
            c.action_text = None
            checks += 1
    cond = db.Event.cycle_id == cycle_id
    if pids:
        cond = or_(cond, db.Event.participant_id.in_(pids))
    events = 0
    for e in s.scalars(select(db.Event).where(db.Event.name == "action_custom_text", cond)).all():
        if (e.props or {}).get("text") == body.text:
            s.delete(e)
            events += 1
    if not checks and not events:
        raise error(404, "text_not_found")
    s.commit()
    return {"deleted": checks, "events": events}


# --------------------------------------------------------------------------- #
# CSV 내보내기 (전체 권한만). 엑셀에서 한글이 깨지지 않게 UTF-8 BOM
# --------------------------------------------------------------------------- #
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(v: Any) -> Any:
    """엑셀이 수식으로 실행하지 않도록 =, +, -, @ 로 시작하는 글자 칸 앞에 ' 를 붙인다(직접 쓴 문장·리더명)."""
    if v is None:
        return ""
    if isinstance(v, str) and v.startswith(FORMULA_START):
        return "'" + v
    return v


def csv_response(name: str, header: list[str], rows: list[list[Any]]) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([csv_cell(v) for v in r])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/export/{kind}.csv")
def export(kind: str, cycle: Optional[str] = None, type: Optional[str] = None, date_from: Optional[str] = None,
           date_to: Optional[str] = None, a: Admin = Depends(require_full)) -> StreamingResponse:
    D = data(a, cycle, type, date_from, date_to)
    cid = D.settings["cycle_id"]
    if kind == "leaders":
        rows = admin_stats.leader_rows(D)
        return csv_response(f"leaders_{cid}.csv",
                            ["leader_name", "serial", "leader_type", "started", "stage", "last_screen", "result_sent",
                             "members_checked", "trends", "last_access"],
                            [[r["leader_name"], r["serial"], r["leader_type"], r["started"], r["stage"], r["last_screen"],
                              "" if r["sent"] is None else ("Y" if r["sent"] else "N"), r["members"],
                              " ".join(t["quadrant"] or "" for t in r["trends"]), r["last_access"]] for r in rows])
    if kind == "checks":
        # 리더명·참여자 ID·시각 없이, 행 순서도 섞는다(참여자 탭과 이어 붙여 개인을 찾지 못하게)
        item_ids = [i["id"] for i in content()["items"]]
        rows = []
        for pid, cs in D.checks.items():
            for c in cs:
                j = c.get("judgment") or {}
                axes = j.get("axes") or {}
                rows.append([D.P[pid]["leader_type"], c["status"], j.get("quadrant"),
                             (axes.get("care") or {}).get("high"), (axes.get("challenge") or {}).get("high"),
                             " ".join(c.get("situations") or []), c.get("action_type")]
                            + [(c.get("answers") or {}).get(i) for i in item_ids])
        random.shuffle(rows)
        return csv_response(f"checks_{cid}.csv", ["leader_type", "status", "quadrant", "care_high", "challenge_high",
                                                  "situations", "action_type"] + item_ids, rows)
    if kind == "texts":
        texts = admin_stats.items_tab(D)["custom_texts"]
        return csv_response(f"custom_texts_{cid}.csv", ["text"], [[t] for t in texts])
    if kind == "screens":
        f = admin_stats.flow_tab(D)
        dwell = {d["screen"]: d for d in f["dwell"]}
        return csv_response(f"screens_{cid}.csv", ["screen", "reached", "stopped", "dwell_median_s", "dwell_p75_s", "dwell_leaders"],
                            [[s["screen"], s["reached"], s["stopped"], dwell[s["screen"]].get("median_s"),
                              dwell[s["screen"]].get("p75_s"), dwell[s["screen"]].get("leaders")] for s in f["screens"]])
    raise error(404, "unknown_export")
