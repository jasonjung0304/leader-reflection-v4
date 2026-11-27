"""응답자 선택 시뮬레이션(2026-11-26)에서 찾은 오류 경로의 회귀 테스트."""
import pytest
from conftest import create, hdr

import admin_auth
import anonymize as an
import main

C = main.load_content()
ITEMS = C["items"]
REVIEWS = [r["id"] for r in C["reviews"]]


def answers(**override):
    a = {i["id"]: ("done" if i["type"] == "behavior" else "no") for i in ITEMS}
    a.update(override)
    return a


@pytest.fixture()
def pid(client):
    p = create(client).json()["participant_id"]
    r = client.put("/api/step1", headers=hdr(pid=p), json={
        "reviews": {r: "remembered" for r in REVIEWS}, "keywords": ["kw_opening"], "last_screen": "keyword", "complete": True})
    assert r.status_code == 200
    return p


def result_after(client, pid, situations, ans):
    assert client.post("/api/checks", headers=hdr(pid=pid), json={"situations": situations}).status_code == 201
    assert client.patch("/api/checks/1", headers=hdr(pid=pid), json={"answers": ans, "last_screen": "result_1"}).status_code == 200
    r = client.get("/api/checks/1/result", headers=hdr(pid=pid))
    assert r.status_code == 200
    return r.json()["results"]


# ---------------- 결과를 본 뒤 답 바꾸기 ----------------
def test_answer_change_after_result_clears_stale_results(client, pid):
    """'특별한 상황 없음' + 결과 3 문항이 있던 점검에서 답을 바꾸면, 이어 하기가 예전 결과를 보여 주지 않는다."""
    old = result_after(client, pid, ["none"], answers(c1_1="not_done"))
    assert old["phrase"] == "item:c1_1"
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"answers": {"c1_1": "done"}, "last_screen": "result_1"})
    assert r.status_code == 200 and r.json()["results"] == {} and r.json()["quadrant"] is None
    me = client.get("/api/me", headers=hdr(pid=pid)).json()["check_in_progress"]
    assert me["results"] == {}   # 화면은 결과가 없으면 결과 정리(analyzing)로 이어 간다
    new = client.get("/api/checks/1/result", headers=hdr(pid=pid)).json()["results"]
    # 모두 해냈어요 + 상황 없음 → 공통 문장이 추천(2026-11-27)
    assert new["phrase"] == "general:all_done"
    assert new["action_options"][0] == {"type": "recommended", "key": "general:all_done"}
    # 예전 추천으로는 완료할 수 없고(422), 새 보기(공통 문장)로는 완료된다
    bad = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"action_type": "recommended", "action_key": "item:c1_1", "complete": True})
    assert bad.status_code == 422 and bad.json()["error"] == "bad_action_key"
    o = new["action_options"][0]
    ok = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"action_type": o["type"], "action_key": o["key"], "complete": True})
    assert ok.status_code == 200 and ok.json()["status"] == "done"


def test_signal_phrase_is_recommended_and_completes(client, pid):
    """2026-11-27: 행동은 모두 했고 체크 5에서 부족 신호를 고른 경우 → 결과 3 첫 항목과 추천 문장이 그 신호에서 나온다."""
    res = result_after(client, pid, ["none"], answers(s3="yes", s4="yes"))
    assert res["r3"]["items"][:2] == ["s3", "s4"] and res["r3"]["empty_signals"] is True
    assert res["phrase"] == "item:s3"
    ok = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"action_type": "recommended", "action_key": "item:s3", "complete": True})
    assert ok.status_code == 200 and ok.json()["status"] == "done"


def test_same_answers_keep_results(client, pid):
    """페이지마다 '다음'으로 같은 답을 다시 보내는 것은 바뀐 것이 아니다."""
    a = answers(c1_1="not_done")
    result_after(client, pid, ["low"], a)
    page1 = {k: a[k] for k in ("c1_1", "c1_2", "c1_3")}
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"answers": page1, "last_screen": "check_2"})
    assert r.json()["results"]["r1"] is not None


def test_situation_change_after_result_clears_results(client, pid):
    result_after(client, pid, ["low"], answers())
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"situations": ["star"], "last_screen": "check_1"})
    assert r.json()["results"] == {}


# ---------------- 범위 밖 값 → 500 대신 4xx ----------------
@pytest.mark.parametrize("seq", ["0", "1001", "99999999999999999999"])
def test_out_of_range_seq_is_422(client, pid, seq):
    h = hdr(pid=pid)
    assert client.patch(f"/api/checks/{seq}", headers=h, json={"answers": {}}).status_code == 422
    assert client.get(f"/api/checks/{seq}/result", headers=h).status_code == 422
    assert client.delete(f"/api/checks/{seq}", headers=h).status_code == 422


def test_non_finite_event_value_dropped(client, pid):
    body = '{"events":[{"name":"screen_view","props":{"screen":"check_1","visible_ms":1e999}}]}'
    r = client.post("/api/events", headers={**hdr(pid=pid), "Content-Type": "application/json"}, content=body)
    assert r.status_code == 200 and r.json()["saved"] == 1


# ---------------- 직접 쓴 문장 ----------------
@pytest.mark.parametrize("raw,stored", [
    ("ㅋㅋ 고마워", "ㅋㅋ 고마워"),                 # 호환용 자모 그대로(NFKC 로 바꾸지 않음)
    ("다음엔 꼭ㅠㅠ", "다음엔 꼭ㅠㅠ"),
    ("기다려 줄게…", "기다려 줄게…"),
    ("㈜ 일정", "㈜ 일정"),
    ("연락 ａ＠ｂ．ｃｏ 로", "연락 로"),            # 전각으로 피해 가도 지운다
    ("사번 １２３４５６ 확인", "사번 확인"),
    ("금액 1234567원", "금액 원"),
    ("내선 02-123-4567로", "내선 로"),
])
def test_scrub_text_keeps_other_characters(raw, stored):
    assert an.scrub_text(raw) == stored


def test_custom_text_only_number_gets_specific_message(client, pid):
    assert client.post("/api/checks", headers=hdr(pid=pid), json={"situations": ["none"]}).status_code == 201
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"action_type": "custom", "action_text": "010-1234-5678"})
    assert r.status_code == 422 and r.json()["message"] == C["action"]["scrubbed_empty"]
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"action_type": "custom", "action_text": "   "})
    assert r.status_code == 422 and r.json()["message"] == C["action"]["blocked_hint"]


def test_custom_text_longer_after_masking_accepted(client, pid):
    """한 글자 별명이 ○○ 로 바뀌어 100자를 넘어도 받는다(입력칸은 100자)."""
    assert client.post("/api/checks", headers=hdr(pid=pid), json={"situations": ["none"]}).status_code == 201
    text = ("○○ 고마워 " * 20).strip()   # 119자
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"answers": answers(), "action_type": "custom", "action_text": text, "complete": True})
    assert r.status_code == 200 and r.json()["action_text"] == text


# ---------------- 리더명 ----------------
@pytest.mark.parametrize("typed,stored", [
    ("열린 리더님", "열린 리더"), ("리더님", "리더"), ("묻고 듣는", "묻고 듣는 리더"), ("햇님", "햇님 리더"), ("팀 리더", "팀 리더"),
])
def test_call_form(typed, stored):
    assert an.validate_custom_leader_name(typed) == stored


# ---------------- CSV 수식 실행 방지 ----------------
def test_csv_cells_starting_with_formula_chars_are_escaped(client):
    p = create(client, leader_type="custom", custom="=1+2").json()["participant_id"]
    client.put("/api/step1", headers=hdr(pid=p), json={
        "reviews": {r: "remembered" for r in REVIEWS}, "keywords": ["kw_none"], "last_screen": "keyword", "complete": True})
    client.post("/api/checks", headers=hdr(pid=p), json={"situations": ["none"]})
    r = client.patch("/api/checks/1", headers=hdr(pid=p), json={
        "answers": answers(), "action_type": "custom", "action_text": '=HYPERLINK("http://x.y","보기")', "complete": True})
    assert r.status_code == 200
    client.cookies.set(admin_auth.COOKIE, admin_auth.make_cookie("full", {}))
    leaders = client.get("/api/admin/export/leaders.csv").text
    texts = client.get("/api/admin/export/texts.csv").text
    assert "'=1+2 리더" in leaders
    assert "'=HYPERLINK" in texts and "\n=HYPERLINK" not in texts
    assert main.admin_api.csv_cell("-다음엔") == "'-다음엔" and main.admin_api.csv_cell(-3) == -3
