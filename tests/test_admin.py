"""8단계: 관리자 인증·권한·개인정보·집계."""
import datetime as dt
import json
import uuid

import pytest
from conftest import KEY, ORDER, hdr
from fastapi.testclient import TestClient

import admin_auth
import db
import main

C = main.load_content()
REVIEWS = [r["id"] for r in C["reviews"]]
BEHAVIOR = [i["id"] for i in C["items"] if i["type"] == "behavior"]
CARE = ["c1_3", "c2_1", "c3_1", "c4_2"]
CHAL = ["c3_2", "c3_3", "c3_4", "c4_1"]
AH = {"X-Admin-Request": "1"}


@pytest.fixture()
def web(tmp_path, monkeypatch):
    """Secure 쿠키가 오가도록 https 로 부르는 클라이언트."""
    monkeypatch.setenv("APP_VERSION", "test-1")
    monkeypatch.setenv("ENTRY_KEY", KEY)
    for k in ("VERCEL", "ADMIN_PASSWORD_FULL", "ADMIN_PASSWORD_READONLY", "SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    db.reset_engine(f"sqlite:///{(tmp_path / 'a.db').as_posix()}")
    return TestClient(main.app, base_url="https://testserver")


def login(web, pw="dev-full"):
    r = web.post("/api/admin/login", headers=AH, json={"password": pw})
    assert r.status_code == 200, r.text
    return r


def answers(done=(), signals=()):
    a = {i: ("done" if i in done else "not_done") for i in BEHAVIOR}
    a.update({s: ("yes" if s in signals else "no") for s in ("s1", "s2", "s3", "s4")})
    return a


def leader(web, leader_type="rc", custom=None, keywords=("kw_listening",), step1=True):
    pid = web.post("/api/participants", headers=hdr(), json={"leader_type": leader_type, "custom_name": custom,
                                                               "option_order": ORDER}).json()["participant_id"]
    if step1:
        web.put("/api/step1", headers=hdr(pid=pid), json={"reviews": {r: "remembered" for r in REVIEWS},
                                                          "keywords": list(keywords), "complete": True})
    return pid


def finish_check(web, pid, ans, situations=("none",), custom_text=None):
    seq = web.post("/api/checks", headers=hdr(pid=pid), json={"situations": list(situations)}).json()["seq"]
    web.patch(f"/api/checks/{seq}", headers=hdr(pid=pid), json={"answers": ans})
    res = web.get(f"/api/checks/{seq}/result", headers=hdr(pid=pid)).json()["results"]
    if custom_text:
        body = {"action_type": "custom", "action_text": custom_text, "complete": True}
    else:
        opt = res["action_options"][0]
        body = {"action_type": opt["type"], "action_key": opt["key"], "complete": True}
    r = web.patch(f"/api/checks/{seq}", headers=hdr(pid=pid), json=body)
    assert r.status_code == 200, r.text
    return seq


@pytest.fixture()
def dataset(web):
    """A: rc 를 고르고 점검 2건 완료(re, rc) / B: 직접 작성, STEP 1 미완료 / C: mi, 점검 진행 중"""
    a = leader(web, "rc", keywords=("kw_listening", "kw_aid"))
    finish_check(web, a, answers(CARE + ["c1_1"]), situations=("pushback",), custom_text="○○님 다음엔 끝까지 들을게요")
    finish_check(web, a, answers(CARE + CHAL), situations=("none",))
    b = leader(web, "custom", custom="묻고 듣는", step1=False)
    c = leader(web, "mi", keywords=("kw_none",))
    web.post("/api/checks", headers=hdr(pid=c), json={"situations": ["low"]})
    web.patch("/api/checks/1", headers=hdr(pid=c), json={"answers": {"c1_1": "done", "c2_1": "done"}})
    return {"a": a, "b": b, "c": c}


# ---------------- 인증 ----------------
def test_login_needs_header_and_password(web):
    assert web.post("/api/admin/login", json={"password": "dev-full"}).status_code == 403
    r = web.post("/api/admin/login", headers=AH, json={"password": "nope"})
    assert r.status_code == 401 and r.json()["error"] == "admin_wrong_password"
    r = login(web)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie and "max-age=28800" in cookie
    assert web.get("/api/admin/me").json()["role"] == "full"


def test_readonly_login(web):
    login(web, "dev-read")
    assert web.get("/api/admin/me").json()["role"] == "readonly"


def test_lockout_after_five_failures(web, monkeypatch):
    for _ in range(4):
        assert web.post("/api/admin/login", headers=AH, json={"password": "x"}).status_code == 401
    assert web.post("/api/admin/login", headers=AH, json={"password": "x"}).status_code == 423
    # 잠긴 동안은 맞는 암호도 막힌다
    assert web.post("/api/admin/login", headers=AH, json={"password": "dev-full"}).status_code == 423
    later = db.utcnow() + dt.timedelta(minutes=11)
    monkeypatch.setattr(admin_auth.db, "utcnow", lambda: later)
    assert web.post("/api/admin/login", headers=AH, json={"password": "dev-full"}).status_code == 200


@pytest.mark.parametrize("path", ["/api/admin/me", "/api/admin/content", "/api/admin/dashboard", "/api/admin/items",
                                  "/api/admin/leaders", "/api/admin/flow", "/api/admin/settings", "/api/admin/export/leaders.csv"])
def test_all_admin_apis_need_login(web, path):
    assert web.get(path).status_code == 401


def test_tampered_or_rotated_cookie_rejected(web, monkeypatch):
    login(web)
    good = web.cookies.get(admin_auth.COOKIE)
    web.cookies.set(admin_auth.COOKIE, good[:-2] + "xx", domain="testserver.local")
    web.cookies.clear()
    web.cookies.set(admin_auth.COOKIE, good[:-2] + ("aa" if not good.endswith("aa") else "bb"))
    assert web.get("/api/admin/me").status_code == 401
    web.cookies.clear()
    web.cookies.set(admin_auth.COOKIE, good)
    assert web.get("/api/admin/me").status_code == 200
    monkeypatch.setenv("ADMIN_PASSWORD_FULL", "rotated-password")   # 암호를 바꾸면 예전 쿠키는 무효
    assert web.get("/api/admin/me").status_code == 401


def test_vercel_without_env_cannot_login(web, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert web.post("/api/admin/login", headers=AH, json={"password": "dev-full"}).status_code == 503


def test_user_entry_key_does_not_open_admin(web):
    assert web.get("/api/admin/dashboard", headers=hdr()).status_code == 401


# ---------------- 권한: 읽기 전용 ----------------
@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/admin/settings", None),
    ("put", "/api/admin/settings", {"target_count": 10}),
    ("get", "/api/admin/export/leaders.csv", None),
    ("get", "/api/admin/export/checks.csv", None),
    ("get", "/api/admin/export/texts.csv", None),
    ("post", "/api/admin/participants/delete", {"leader_name": "x", "serial": "1234"}),
    ("post", "/api/admin/data/delete", {"confirm": "2026-1"}),
    ("post", "/api/admin/texts/delete", {"text": "x"}),
    ("get", "/api/admin/threshold-preview", None),
])
def test_readonly_blocked(web, method, path, body):
    login(web, "dev-read")
    kw = {"headers": AH}
    if body is not None:
        kw["json"] = body
    r = getattr(web, method)(path, **kw)
    assert r.status_code == 403 and r.json()["error"] == "admin_readonly"


@pytest.mark.parametrize("path", ["/api/admin/dashboard", "/api/admin/items", "/api/admin/leaders", "/api/admin/flow"])
def test_readonly_can_view(web, dataset, path):
    login(web, "dev-read")
    assert web.get(path).status_code == 200


def test_mutation_needs_admin_header(web):
    login(web)
    assert web.put("/api/admin/settings", json={"target_count": 10}).status_code == 403
    assert web.put("/api/admin/settings", headers=AH, json={"target_count": 10}).status_code == 200


# ---------------- 개인정보 ----------------
def test_leaders_tab_has_no_answers_texts_or_ids(web, dataset):
    login(web, "dev-read")
    body = web.get("/api/admin/leaders").json()
    raw = json.dumps(body, ensure_ascii=False)
    for pid in dataset.values():
        assert pid not in raw
    assert "끝까지 들을게요" not in raw and "not_done" not in raw and "answers" not in raw
    row = next(r for r in body["rows"] if r["leader_type"] == "rc")
    assert set(row) == {"leader_name", "serial", "leader_type", "started", "stage", "last_screen", "sent", "members",
                        "trends", "last_access", "same_name", "name_suspect"}
    assert [t["quadrant"] for t in row["trends"]] == ["re", "rc"]


def test_custom_texts_listed_without_author(web, dataset):
    login(web)
    body = web.get("/api/admin/items").json()
    assert body["custom_texts"] == ["○○님 다음엔 끝까지 들을게요"]
    raw = json.dumps(body, ensure_ascii=False)
    assert "따뜻하고 솔직한 리더" not in raw and dataset["a"] not in raw


def test_delete_custom_text(web, dataset):
    """2026-11-27: 실명 등이 들어간 직접 쓴 문장은 관리자가 지울 수 있다. 같은 문장·같은 문장을 담은 이벤트도 함께."""
    a = dataset["a"]
    web.post("/api/events", headers=hdr(pid=a), json={"events": [
        {"name": "action_custom_text", "props": {"text": "○○님 다음엔 끝까지 들을게요"}, "check_seq": 1},
        {"name": "action_custom_text", "props": {"text": "다른 문장"}, "check_seq": 2}]})
    login(web)
    before = web.get("/api/admin/items").json()
    r = web.post("/api/admin/texts/delete", headers=AH, json={"text": "○○님 다음엔 끝까지 들을게요"})
    assert r.status_code == 200 and r.json() == {"deleted": 1, "events": 1}
    after = web.get("/api/admin/items").json()
    assert after["custom_texts"] == [] and after["custom_text_count"] == 0
    assert after["actions"]["custom"] == before["actions"]["custom"]          # 실천 유형 집계(2.6)는 그대로
    with db.get_session() as s:
        texts = [e.props.get("text") for e in s.query(db.Event).filter(db.Event.name == "action_custom_text")]
    assert texts == ["다른 문장"]
    again = web.post("/api/admin/texts/delete", headers=AH, json={"text": "○○님 다음엔 끝까지 들을게요"})
    assert again.status_code == 404 and again.json()["error"] == "text_not_found"
    assert web.post("/api/admin/texts/delete", json={"text": "다른 문장"}).status_code == 403   # 관리자 요청 헤더 필수


def test_threshold_preview_and_skew(web):
    """2026-11-27: 설정의 판정 기준값 미리보기 — 저장된 응답을 기준값별로 다시 판정. 1.14 에는 쏠림 표시."""
    for _ in range(4):                                   # 케어·쓴소리 3/4 → 0.75 까지는 rc, 1.0 이면 mi
        finish_check(web, leader(web), answers(CARE[:3] + CHAL[:3]))
    finish_check(web, leader(web), answers(CARE + CHAL))  # 4/4 → 늘 rc
    login(web)
    pv = web.get("/api/admin/threshold-preview").json()
    assert pv["current"] == 0.5 and pv["leaders"] == 5 and pv["skew_pct"] == 70.0
    rows = {r["threshold"]: r for r in pv["rows"]}
    assert set(rows) == {0.25, 0.5, 0.75, 1.0} and rows[0.5]["current"] is True
    assert rows[0.75]["cells"]["rc"] == 100.0 and rows[0.75]["skewed"] is True
    assert rows[1.0]["cells"] == {"rc": 20.0, "re": 0.0, "oa": 0.0, "mi": 80.0} and rows[1.0]["top"] == "mi"
    skew = web.get("/api/admin/dashboard").json()["application"]["quadrant_skew"]
    assert skew == {"quadrant": "rc", "pct": 100.0, "skewed": True}
    # 미리보기는 저장된 결과를 바꾸지 않는다
    assert web.get("/api/admin/dashboard").json()["application"]["quadrants"][0] == {"quadrant": "rc", "pct": 100.0}


def test_duplicate_and_spread_signals(web):
    """2026-11-27: 직접 지은 리더명이 겹치면 3.1 '같은 이름', 등록이 대상 인원을 넘으면 1.1 경고, 앱 안 브라우저 기기 수(4.5)."""
    a = leader(web, "custom", custom="묻고 듣는", step1=False)
    leader(web, "custom", custom="묻고 듣는", step1=False)
    leader(web, "custom", custom="혼자인", step1=False)
    leader(web, "rc", step1=False)
    leader(web, "rc", step1=False)
    import uuid as _u
    d1, d2 = str(_u.uuid4()), str(_u.uuid4())
    web.post("/api/events", headers=hdr(pid=a, device=d1), json={"events": [{"name": "inapp_browser", "props": {"browser": "kakaotalk"}}]})
    web.post("/api/events", headers=hdr(device=d2), json={"events": [{"name": "screen_view", "props": {"screen": "notice"}}]})
    login(web)
    rows = web.get("/api/admin/leaders").json()["rows"]
    same = {(r["leader_name"], r["same_name"]) for r in rows}
    assert ("묻고 듣는 리더", 2) in same and ("혼자인 리더", None) in same
    assert not any(r["name_suspect"] for r in rows)
    assert all(r["same_name"] is None for r in rows if r["leader_type"] == "rc")      # 보기 리더명은 여럿이 고르므로 표시 안 함
    assert web.put("/api/admin/settings", headers=AH, json={"target_count": 4}).status_code == 200
    assert web.get("/api/admin/dashboard").json()["participation"]["over_target"] is True
    assert web.put("/api/admin/settings", headers=AH, json={"target_count": 5}).status_code == 200
    assert web.get("/api/admin/dashboard").json()["participation"]["over_target"] is False
    ia = web.get("/api/admin/flow").json()["devices"]["inapp"]
    assert ia["devices"] == 1 and ia["by_browser"] == [{"browser": "kakaotalk", "devices": 1}] and ia["pct"] == 50.0


def test_name_like_leader_names_flagged(web):
    """2026-11-27: 직접 지은 리더명이 사람 이름처럼 보이면 3.1 에 '실명일 수 있음'(막지는 않음)."""
    leader(web, "custom", custom="홍길동", step1=False)
    leader(web, "custom", custom="정직한", step1=False)
    login(web)
    rows = {r["leader_name"]: r["name_suspect"] for r in web.get("/api/admin/leaders").json()["rows"]}
    assert rows == {"홍길동 리더": True, "정직한 리더": False}


def test_checks_csv_has_no_names_ids_or_times(web, dataset):
    login(web)
    r = web.get("/api/admin/export/checks.csv")
    assert r.status_code == 200 and r.text.startswith("﻿")
    for s in ("리더", "묻고", *dataset.values(), "2026-"):
        assert s not in r.text
    lines = r.text.strip().splitlines()
    assert lines[0].lstrip("﻿").startswith("leader_type,status,quadrant") and len(lines) == 4  # 점검 3건(완료 2 + 진행 1)


def test_leaders_csv(web, dataset):
    login(web)
    text = web.get("/api/admin/export/leaders.csv").text
    assert "묻고 듣는 리더" in text and "re rc" in text


# ---------------- 집계 ----------------
def test_dashboard_numbers(web, dataset):
    login(web)
    d = web.get("/api/admin/dashboard").json()
    p = d["participation"]
    assert (p["target"], p["registered"], p["step1_done"], p["check_done"]) == (170, 3, 2, 1)
    assert p["members_median"] == 2
    assert {x["type"]: x["count"] for x in p["leader_types"]} == {"rc": 1, "re": 0, "oa": 0, "mi": 1, "custom": 1}
    assert {x["bucket"]: x["count"] for x in p["members_dist"]} == {"1": 0, "2": 1, "3": 0, "4+": 0, "0": 1}
    assert {x["type"]: x["pct"] for x in p["type_completion"]}["rc"] == 100.0
    rec = d["recall"]
    assert rec["leaders"] == 2 and rec["no_keyword_pct"] == 50.0 and rec["avg_remember_pct"] == 100.0
    app = d["application"]
    cross = {r["type"]: r for r in app["cross"]}
    assert cross["rc"]["checks"] == 2 and cross["rc"]["cells"] == {"rc": 50.0, "re": 50.0, "oa": 0.0, "mi": 0.0}
    assert app["care_high_pct"] == 100.0 and app["challenge_high_pct"] == 50.0
    # 행동 실행률은 리더별로 먼저: A = (5+8)/26 = 0.5, C = 2/2 = 1.0 (진행 중 점검도 답한 문항까지 포함) → 75%
    assert app["behavior_rate_pct"] == 75.0
    # 2→3: kw_listening(c2_1)을 고른 A 는 100%. 고르지 않은 리더는 kw_none 인 C 가 빠져서 없음
    tr = {t["keyword"]: t for t in d["transfer"]}
    assert tr["kw_listening"]["picked_pct"] == 100.0 and tr["kw_listening"]["not_picked_n"] == 0


def test_items_numbers(web, dataset):
    login(web)
    it = web.get("/api/admin/items").json()
    b = {x["item"]: x for x in it["behavior"]}
    assert b["c1_1"] == {"item": "c1_1", "axis": None, "rate": 75.0, "leaders": 2}   # A 0.5, C 1.0
    assert b["c3_2"]["rate"] == 50.0 and b["c3_2"]["leaders"] == 1
    assert it["actions"]["custom"] == 50.0 and it["actions"]["recommended"] == 50.0
    sit = {x["situation"]: x["pct"] for x in it["situations"]}
    assert sit["pushback"] == 50.0 and sit["none"] == 50.0


def test_leaders_filters_sort_page(web, dataset):
    login(web)
    body = web.get("/api/admin/leaders").json()
    assert body["counts"] == {"all": 3, "step1": 1, "checking": 1, "done": 1}
    assert web.get("/api/admin/leaders?stage=done").json()["total"] == 1
    assert web.get("/api/admin/leaders", params={"q": "묻고"}).json()["rows"][0]["leader_name"] == "묻고 듣는 리더"
    names = [r["leader_name"] for r in web.get("/api/admin/leaders?sort=leader_name&desc=false").json()["rows"]]
    assert names == sorted(names)
    paged = web.get("/api/admin/leaders?size=20&page=9").json()
    assert paged["page"] == 1 and paged["pages"] == 1


def test_type_filter(web, dataset):
    login(web)
    d = web.get("/api/admin/dashboard?type=rc").json()
    assert d["participation"]["registered"] == 1
    assert web.get("/api/admin/dashboard?type=boss").status_code == 422


def test_flow_counts_from_events(web, dataset):
    a = dataset["a"]
    dev = str(uuid.uuid4())
    other = str(uuid.uuid4())
    def sv(screen, ms, pid=None, device=dev, seq=None, final=True):
        h = hdr(pid=pid, device=device)
        ev = {"name": "screen_view", "props": {"screen": screen, "visit": str(uuid.uuid4()), "visible_ms": ms, "final": final,
                                               "entered_at": "2026-11-26T01:00:00.000Z", "left_at": "2026-11-26T01:00:05.000Z"}}
        if seq:
            ev["check_seq"] = seq
        web.post("/api/events", headers=h, json={"events": [ev]})
    sv("notice", 4000)
    sv("notice", 2000, device=other)                      # 안내만 보고 나간 기기
    sv("leader_name", 3000, pid=a)
    sv("intro", 1000, pid=a)
    sv("review_1", 5000, pid=a)
    sv("analyzing", 3000, pid=a, seq=1)
    web.post("/api/events", headers=hdr(pid=a, device=dev), json={"events": [
        {"name": "back_nav", "props": {"from": "result_1", "to": "check_5"}},
        {"name": "blocked_next_tap", "props": {"screen": "keyword"}},
        {"name": "error", "props": {"kind": "pdf", "browser": "safari"}},
    ]})
    login(web)
    f = web.get("/api/admin/flow").json()
    scr = {s["screen"]: s for s in f["screens"]}
    assert scr["notice"]["reached"] == 2 and scr["notice"]["stopped"] == 1   # 다른 기기는 안내에서 멈춤
    assert scr["leader_name"]["reached"] == 1
    dw = {d["screen"]: d for d in f["dwell"]}
    assert dw["review_1"]["median_s"] == 5.0 and dw["analyzing"] == {"screen": "analyzing", "excluded": True}
    assert f["entry"]["notice_devices"] == 2 and f["entry"]["to_leader_name"] == 50.0
    assert f["back_top"][0]["screen"] == "result_1"
    assert f["errors"]["counts"]["pdf"] == 1 and f["errors"]["table"][0]["browser"] == "safari"
    assert {b["screen"]: b for b in f["blocked"]}["keyword"]["per_leader"] == round(1 / 3, 2)


def test_every_numbered_item_has_insight():
    """관리자 항목마다 '이 데이터로 알 수 있는 것'(300자 내외)이 있고, admin.js 가 쓰는 키와 맞는다(2026-11-26)."""
    import re
    from pathlib import Path
    root = Path(main.__file__).resolve().parent
    A = json.loads((root / "content" / "admin.json").read_text(encoding="utf-8"))
    js = (root / "public" / "admin.js").read_text(encoding="utf-8")
    used = set(re.findall(r"insight: IN\.(\w+)", js))
    have = {k for tab in ("dashboard", "items", "flow") for k in A["insights"][tab]}
    assert used == have
    assert js.count("no: num()") == 38 and "insightEl(A.insights.leaders.table)" in js   # 38 + 참여자 1 = 39
    for tab in ("dashboard", "items", "leaders", "flow"):
        for k, text in A["insights"][tab].items():
            assert 200 <= len(text) <= 350, (tab, k, len(text))


def test_recall_practice_split_by_remembered(web):
    """2→3 기억한 리더와 실천한 리더(2026-11-26): 핵심 메시지마다 기억난 리더 비율과 실천한 리더 비율.
    메시지 3 = 오프닝·경청·확대·미래 질문 키워드의 절반 이상 '했어요'. 메시지 1 = 경향이 권위 있는 리더(rc)."""
    def make(recall3, ans):
        pid = web.post("/api/participants", headers=hdr(), json={"leader_type": "rc", "option_order": ORDER}).json()["participant_id"]
        reviews = {r: "remembered" for r in REVIEWS}
        reviews["review_3_listening_questions"] = recall3
        web.put("/api/step1", headers=hdr(pid=pid), json={"reviews": reviews, "keywords": ["kw_ait"], "complete": True})
        finish_check(web, pid, ans)
    practiced3 = answers(["c1_1", "c1_2", "c2_1", "c2_2"])          # 오프닝(2/3)·경청·확대 질문 → 4개 중 3개
    for _ in range(5):
        make("remembered", practiced3)
    for _ in range(4):
        make("not_remembered", answers())
    make("not_remembered", practiced3)
    login(web)
    rp = {x["review"]: x for x in web.get("/api/admin/dashboard").json()["recall_practice"]}
    r3 = rp["review_3_listening_questions"]
    assert r3["leaders"] == 10 and r3["basis"] == "keywords" and r3["small"] is False
    assert r3["remembered"] == 5 and r3["remembered_pct"] == 50.0      # 기억난 리더
    assert r3["practiced"] == 6 and r3["practiced_pct"] == 60.0        # 실천한 리더(기억 안 난 1명 포함)
    assert r3["both"] == 5 and r3["remembered_not_practiced"] == 0 and r3["practiced_of_remembered_pct"] == 100.0
    # 메시지 1: 모두 '기억나요'지만 아무도 권위 있는 리더 경향이 아님 → 기억했지만 실천 못 함 10명
    r1 = rp["review_1_authority"]
    assert r1["basis"] == "quadrant" and r1["remembered_pct"] == 100.0 and r1["practiced_pct"] == 0.0
    assert r1["remembered_not_practiced"] == 10


def test_recall_practice_hides_small_groups(web):
    pid = web.post("/api/participants", headers=hdr(), json={"leader_type": "rc", "option_order": ORDER}).json()["participant_id"]
    web.put("/api/step1", headers=hdr(pid=pid), json={"reviews": {r: "remembered" for r in REVIEWS}, "keywords": ["kw_ait"], "complete": True})
    finish_check(web, pid, answers(BEHAVIOR))
    login(web)
    r3 = {x["review"]: x for x in web.get("/api/admin/dashboard").json()["recall_practice"]}["review_3_listening_questions"]
    # 대상 5명 미만은 비율을 숨김(인원은 표에 남김)
    assert r3["leaders"] == 1 and r3["small"] is True and r3["remembered"] == 1 and r3["practiced"] == 1
    assert r3["remembered_pct"] is None and r3["practiced_pct"] is None and r3["practiced_of_remembered_pct"] is None


def test_done_button_clicks_counted(web, dataset):
    """완료 화면 3칸: 누를 때마다 1회, 누른 리더 수, 완료 화면 온 리더 대비(2026-11-26 요청)."""
    a, b = dataset["a"], dataset["b"]
    def evs(pid, *items):
        web.post("/api/events", headers=hdr(pid=pid), json={"events": [
            {"name": "screen_view", "check_seq": 1, "props": {"screen": "done", "visit": str(uuid.uuid4()), "visible_ms": 1000, "final": True}}]
            + [{"name": "done_button_click", "props": {"button": x}, "check_seq": 1} for x in items]})
    evs(a, "pdf", "pdf", "recommend")      # 같은 리더가 PDF 를 두 번
    evs(b, "pdf", "again", "bogus")        # 정해 두지 않은 버튼 값은 세지 않음
    login(web)
    da = web.get("/api/admin/flow").json()["done_actions"]
    clicks = {x["button"]: x for x in da["clicks"]}
    assert [x["button"] for x in da["clicks"]] == ["again", "pdf", "recommend"]
    assert clicks["pdf"]["clicks"] == 3 and clicks["pdf"]["leaders"] == 2
    assert clicks["recommend"]["clicks"] == 1 and clicks["again"]["clicks"] == 1
    assert da["at_done"] == 2 and clicks["pdf"]["leaders_pct"] == 100.0 and clicks["recommend"]["leaders_pct"] == 50.0
    assert da["nothing"] == 0.0


def test_visit_uses_last_value(web, dataset):
    """같은 visit 의 중간값(final=false)과 최종값이 있으면 최종값만 센다."""
    a = dataset["a"]
    v = str(uuid.uuid4())
    for ms, final in ((1000, False), (4000, True)):
        web.post("/api/events", headers=hdr(pid=a, device=str(uuid.uuid4())), json={"events": [
            {"name": "screen_view", "props": {"screen": "review_2", "visit": v, "visible_ms": ms, "final": final}}]})
    login(web)
    dw = {d["screen"]: d for d in web.get("/api/admin/flow").json()["dwell"]}
    assert dw["review_2"]["median_s"] == 4.0 and dw["review_2"]["leaders"] == 1


# ---------------- 설정·삭제 ----------------
def test_settings_entry_key_change(web):
    login(web)
    s = web.get("/api/admin/settings").json()
    assert s["entry_key"] == KEY and s["entry_key_source"] == "env"
    assert web.put("/api/admin/settings", headers=AH, json={"entry_key": "short"}).status_code == 422
    r = web.put("/api/admin/settings", headers=AH, json={"entry_key": "New-Key_2026x", "deadline": "2026-12-31", "duration_min": 7})
    assert r.status_code == 200 and r.json()["entry_key_source"] == "settings"
    assert web.get("/api/content", headers=hdr()).status_code == 403
    body = web.get("/api/content", headers=hdr(key="New-Key_2026x")).json()
    assert body["settings"]["duration_min"] == 7
    r = web.put("/api/admin/settings", headers=AH, json={"clear": ["deadline", "duration_min"]})
    assert r.json()["deadline"] is None and r.json()["duration_min"] is None
    assert web.put("/api/admin/settings", headers=AH, json={"clear": ["entry_key"]}).status_code == 422


def test_secret_admin_link(web):
    """2026-11-27: 관리자 링크를 만들면 그 키(X-Admin-Link)가 없는 브라우저에는 로그인 화면·로그인이 404.
    새 링크로 바꾸면 바꾼 사람은 로그인 유지, 예전 로그인 쿠키는 무효. 끄면 다시 /admin 만으로."""
    assert web.get("/api/admin/login-text").status_code == 200          # 링크를 만들기 전에는 확인 안 함
    login(web)
    old_cookie = web.cookies.get(admin_auth.COOKIE)
    r = web.post("/api/admin/link", headers=AH, json={"action": "rotate"})
    assert r.status_code == 200 and r.json()["admin_link_source"] == "settings"
    key = r.json()["admin_link_key"]
    assert len(key) >= 24
    assert web.get("/api/admin/me").status_code == 200                  # 바꾼 사람은 그대로
    assert web.get("/api/admin/login-text").json()["error"] == "not_found"
    assert web.get("/api/admin/login-text", headers={"X-Admin-Link": "wrong"}).status_code == 404
    assert web.get("/api/admin/login-text", headers={"X-Admin-Link": key}).status_code == 200
    assert web.post("/api/admin/login", headers=AH, json={"password": "dev-full"}).status_code == 404
    assert web.post("/api/admin/login", headers={**AH, "X-Admin-Link": key}, json={"password": "dev-full"}).status_code == 200
    other = TestClient(main.app, base_url="https://testserver")
    other.cookies.set(admin_auth.COOKIE, old_cookie)
    assert other.get("/api/admin/me").status_code == 401                # 링크를 바꾸기 전의 로그인은 풀림
    r = web.post("/api/admin/link", headers=AH, json={"action": "off"})
    assert r.json()["admin_link_key"] is None and r.json()["admin_link_source"] == "none"
    assert web.get("/api/admin/login-text").status_code == 200


def test_admin_link_from_env(web, monkeypatch):
    monkeypatch.setenv("ADMIN_LINK_KEY", "env-admin-link-key")
    assert web.get("/api/admin/login-text").status_code == 404
    assert web.get("/api/admin/login-text", headers={"X-Admin-Link": "env-admin-link-key"}).status_code == 200


def test_change_admin_password(web):
    """2026-11-27: 관리자 화면에서 암호 바꾸기 — 지금 전체 권한 암호 확인, 10자 이상, 두 권한 서로 다르게, 해시로만 저장."""
    login(web)
    reader = TestClient(main.app, base_url="https://testserver")
    login(reader, "dev-read")
    bad = lambda body: web.post("/api/admin/password", headers=AH, json=body)  # noqa: E731
    assert bad({"current": "nope", "role": "readonly", "new": "Readonly-2026!"}).status_code == 401
    assert bad({"current": "dev-full", "role": "readonly", "new": "short"}).json()["error"] == "password_too_short"
    assert bad({"current": "dev-full", "role": "readonly", "new": " padded-pass "}).json()["error"] == "password_too_short"
    r = web.post("/api/admin/password", headers=AH, json={"current": "dev-full", "role": "readonly", "new": "Readonly-2026!"})
    assert r.status_code == 200 and r.json()["admin_password_source"] == {"full": "env", "readonly": "settings"}
    assert reader.get("/api/admin/me").status_code == 401               # 읽기 전용으로 로그인해 있던 사람은 다시 로그인
    assert web.get("/api/admin/me").status_code == 200
    assert web.post("/api/admin/password", headers=AH, json={"current": "dev-full", "role": "full", "new": "Readonly-2026!"}).json()["error"] == "password_same_as_other"
    r = web.post("/api/admin/password", headers=AH, json={"current": "dev-full", "role": "full", "new": "Culture-Team-2026"})
    assert r.status_code == 200 and web.get("/api/admin/me").status_code == 200   # 바꾼 사람은 로그인 유지
    fresh = TestClient(main.app, base_url="https://testserver")
    assert fresh.post("/api/admin/login", headers=AH, json={"password": "dev-full"}).status_code == 401   # 예전 암호는 안 됨
    assert fresh.post("/api/admin/login", headers=AH, json={"password": "Culture-Team-2026"}).json()["role"] == "full"
    assert reader.post("/api/admin/login", headers=AH, json={"password": "Readonly-2026!"}).json()["role"] == "readonly"
    assert reader.post("/api/admin/password", headers=AH, json={"current": "x", "role": "full", "new": "y" * 12}).status_code == 403
    assert reader.post("/api/admin/link", headers=AH, json={"action": "rotate"}).status_code == 403
    view = web.get("/api/admin/settings").json()
    assert "admin_password_hashes" not in view
    with db.get_session() as s:
        stored = json.dumps(db.get_settings(s)["admin_password_hashes"])
    assert "Culture-Team-2026" not in stored and "Readonly-2026!" not in stored and stored.count("pbkdf2_sha256$") == 2


def test_settings_open_entry(web, monkeypatch):
    """2026-11-27: 관리자 설정 '입장 키 없이 들어오기' — 켜기·끄기·비우기(환경 변수 따름). 읽기 전용은 바꿀 수 없다."""
    login(web)
    s = web.get("/api/admin/settings").json()
    assert s["open_entry"] is False and s["open_entry_source"] == "default"
    r = web.put("/api/admin/settings", headers=AH, json={"open_entry": True})
    assert r.status_code == 200 and r.json()["open_entry"] is True and r.json()["open_entry_source"] == "settings"
    assert web.get("/api/content").status_code == 200                       # 키 없이
    r = web.put("/api/admin/settings", headers=AH, json={"open_entry": False})
    assert r.json()["open_entry"] is False and web.get("/api/content").status_code == 403
    monkeypatch.setenv("OPEN_ENTRY", "1")
    r = web.put("/api/admin/settings", headers=AH, json={"clear": ["open_entry"]})
    assert r.json()["open_entry"] is True and r.json()["open_entry_source"] == "env"
    web.post("/api/admin/logout", headers=AH)
    login(web, "dev-read")
    assert web.put("/api/admin/settings", headers=AH, json={"open_entry": False}).status_code == 403


def test_delete_participant_by_name_and_serial(web, dataset):
    with db.get_session() as s:
        p = s.get(db.Participant, dataset["a"])
        name, serial = p.leader_name, p.serial
    login(web)
    assert web.post("/api/admin/participants/delete", headers=AH, json={"leader_name": name, "serial": "0000"}).status_code == 404
    assert web.post("/api/admin/participants/delete", headers=AH, json={"leader_name": name, "serial": serial}).json() == {"deleted": 1}
    with db.get_session() as s:
        assert s.get(db.Participant, dataset["a"]) is None
        assert s.query(db.Check).filter_by(participant_id=dataset["a"]).count() == 0
        assert s.query(db.Event).filter_by(participant_id=dataset["a"]).count() == 0
    assert web.get("/api/me", headers=hdr(pid=dataset["a"])).status_code == 404


def test_delete_cycle_data_needs_confirm(web, dataset):
    login(web)
    r = web.post("/api/admin/data/delete", headers=AH, json={"confirm": "wrong"})
    assert r.status_code == 422 and r.json()["error"] == "confirm_mismatch"
    assert web.post("/api/admin/data/delete", headers=AH, json={"confirm": "2026-1"}).json() == {"deleted_participants": 3}
    with db.get_session() as s:
        assert s.query(db.Participant).count() == 0 and s.query(db.Check).count() == 0


def test_cycles_listed_and_viewable(web, dataset):
    login(web)
    web.put("/api/admin/settings", headers=AH, json={"cycle_id": "2026-2"})
    me = web.get("/api/admin/me").json()
    assert me["cycle_id"] == "2026-2" and me["cycles"] == ["2026-1", "2026-2"]
    assert web.get("/api/admin/dashboard").json()["participation"]["registered"] == 0
    assert web.get("/api/admin/dashboard?cycle=2026-1").json()["participation"]["registered"] == 3
