"""3단계: STEP 1·점검·이벤트 저장 API."""
import uuid

import pytest
from conftest import create, hdr

import db
import main

REVIEWS = [r["id"] for r in main.load_content()["reviews"]]
ITEMS = main.load_content()["items"]


def all_answers(behavior="done", signal="no"):
    return {i["id"]: (behavior if i["type"] == "behavior" else signal) for i in ITEMS}


@pytest.fixture()
def pid(client):
    return create(client).json()["participant_id"]


def finish_step1(client, pid, keywords=("kw_opening",)):
    r = client.put("/api/step1", headers=hdr(pid=pid), json={
        "reviews": {r: "remembered" for r in REVIEWS}, "keywords": list(keywords),
        "last_screen": "keyword", "complete": True})
    assert r.status_code == 200, r.text
    return r.json()


def start(client, pid, situations=("pushback", "low")):
    return client.post("/api/checks", headers=hdr(pid=pid), json={"situations": list(situations)})


# ---------------- STEP 1 ----------------
def test_step1_saves_page_by_page(client, pid):
    for n, rid in enumerate(REVIEWS, 1):
        r = client.put("/api/step1", headers=hdr(pid=pid),
                       json={"reviews": {rid: "not_remembered" if n % 2 else "remembered"},
                             "last_screen": f"review_{n}"})
        assert r.status_code == 200
    body = r.json()
    assert body["done"] is False and body["last_screen"] == "review_5" and len(body["reviews"]) == 5
    me = client.get("/api/me", headers=hdr(pid=pid)).json()
    assert me["step1"]["reviews"][REVIEWS[0]] == "not_remembered"


def test_step1_complete_locks_name_and_step1(client, pid):
    body = finish_step1(client, pid, ["kw_ait", "kw_aid"])
    assert body["done"] is True and body["keywords"] == ["kw_ait", "kw_aid"]
    assert client.put("/api/step1", headers=hdr(pid=pid), json={"keywords": ["kw_ait"]}).status_code == 409
    assert client.put("/api/participants/me", headers=hdr(pid=pid), json={"leader_type": "mi"}).status_code == 409


def test_step1_complete_needs_all_reviews(client, pid):
    r = client.put("/api/step1", headers=hdr(pid=pid), json={
        "reviews": {REVIEWS[0]: "remembered"}, "keywords": ["kw_ait"], "complete": True})
    assert r.status_code == 422 and r.json()["error"] == "step1_incomplete"


@pytest.mark.parametrize("keywords", [[], ["kw_x"], ["kw_ait", "kw_ait"], ["kw_ait", "kw_aid", "kw_opening", "kw_ait"],
                                      ["kw_none", "kw_ait"], ["kw_ait"] * 4])
def test_step1_bad_keywords(client, pid, keywords):
    r = client.put("/api/step1", headers=hdr(pid=pid), json={"keywords": keywords})
    assert r.status_code == 422


def test_step1_none_keyword_alone(client, pid):
    assert finish_step1(client, pid, ["kw_none"])["keywords"] == ["kw_none"]


@pytest.mark.parametrize("body", [{"reviews": {"review_x": "remembered"}},
                                  {"reviews": {REVIEWS[0]: "maybe"}},
                                  {"last_screen": "check_1"}])
def test_step1_bad_values(client, pid, body):
    assert client.put("/api/step1", headers=hdr(pid=pid), json=body).status_code == 422


def test_step1_needs_participant(client):
    assert client.put("/api/step1", headers=hdr(), json={}).status_code == 404


# ---------------- 점검 ----------------
def test_check_requires_step1(client, pid):
    r = start(client, pid)
    assert r.status_code == 409 and r.json()["error"] == "step1_not_done"


def test_check_full_flow(client, pid):
    finish_step1(client, pid)
    r = start(client, pid)
    assert r.status_code == 201 and r.json()["seq"] == 1 and r.json()["last_screen"] == "member"
    for page in main.load_content()["checklist_pages"]:
        answers = {i: ("not_done" if i.startswith("c") else "yes") for i in page["items"]}
        r = client.patch("/api/checks/1", headers=hdr(pid=pid),
                         json={"answers": answers, "last_screen": f"check_{page['page']}"})
        assert r.status_code == 200
    assert len(r.json()["answers"]) == 17
    me = client.get("/api/me", headers=hdr(pid=pid)).json()
    assert me["check_in_progress"]["last_screen"] == "check_5"
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={
        "action_type": "recommended", "action_key": "situation:pushback", "complete": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done" and body["action_key"] == "situation:pushback"
    me = client.get("/api/me", headers=hdr(pid=pid)).json()
    assert me["checks_done"] == 1 and me["check_in_progress"] is None
    # 완료한 점검은 바꿀 수 없고, 다른 구성원 점검은 새로 시작
    assert client.patch("/api/checks/1", headers=hdr(pid=pid), json={"last_screen": "action"}).status_code == 409
    r = start(client, pid, ["none"])
    assert r.status_code == 201 and r.json()["seq"] == 2


def test_only_one_check_in_progress(client, pid):
    finish_step1(client, pid)
    start(client, pid)
    r = start(client, pid)
    assert r.status_code == 409 and r.json() == {"error": "check_in_progress", "message": "1"}


@pytest.mark.parametrize("situations", [[], ["none", "low"], ["low", "low"], ["boss"]])
def test_bad_situations(client, pid, situations):
    finish_step1(client, pid)
    assert start(client, pid, situations).status_code == 422


@pytest.mark.parametrize("answers", [{"c1_1": "yes"}, {"s1": "done"}, {"c9_9": "done"}])
def test_bad_answers(client, pid, answers):
    finish_step1(client, pid)
    start(client, pid)
    assert client.patch("/api/checks/1", headers=hdr(pid=pid), json={"answers": answers}).status_code == 422


def test_complete_needs_all_answers_and_action(client, pid):
    finish_step1(client, pid)
    start(client, pid)
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"answers": {"c1_1": "done"}, "complete": True})
    assert r.json() == {"error": "check_incomplete", "message": "answers"}
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"answers": all_answers(), "complete": True})
    assert r.json() == {"error": "check_incomplete", "message": "action"}


def test_custom_action_text_scrubbed(client, pid):
    finish_step1(client, pid)
    start(client, pid)
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={
        "answers": all_answers(), "action_type": "custom",
        "action_text": "○○님, 010-1234-5678 로 연락 줘요", "complete": True})
    assert r.status_code == 200 and r.json()["action_text"] == "○○님, 로 연락 줘요"
    assert r.json()["action_key"] is None


# 100자 제한은 화면 입력칸이 막는다. 서버는 별명 → ○○ 로 길어진 문장을 받으려고 두 배(200자)까지 받는다
@pytest.mark.parametrize("text", ["", "   ", "010-1234-5678", "가" * 201])
def test_custom_action_text_rejected(client, pid, text):
    finish_step1(client, pid)
    start(client, pid)
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"action_type": "custom", "action_text": text})
    assert r.status_code == 422


# item:s1 은 2026-11-27부터 유효(부족 신호의 추천 문장). general 은 general_phrases 의 키만, '_comment' 같은 메모 키는 안 됨
@pytest.mark.parametrize("key", ["situation:boss", "item:s9", "item:c9", "c1_1", "", "general:nope", "general:_comment", "general:"])
def test_bad_action_key(client, pid, key):
    finish_step1(client, pid)
    start(client, pid)
    r = client.patch("/api/checks/1", headers=hdr(pid=pid), json={"action_type": "other_preset", "action_key": key})
    assert r.status_code == 422


def test_restart_deletes_in_progress_only(client, pid):
    finish_step1(client, pid)
    start(client, pid)
    assert client.delete("/api/checks/1", headers=hdr(pid=pid)).json() == {"deleted": 1}
    assert client.get("/api/me", headers=hdr(pid=pid)).json()["check_in_progress"] is None
    assert start(client, pid).json()["seq"] == 1
    client.patch("/api/checks/1", headers=hdr(pid=pid), json={
        "answers": all_answers(), "action_type": "recommended", "action_key": "situation:pushback", "complete": True})
    assert client.delete("/api/checks/1", headers=hdr(pid=pid)).status_code == 409


def test_cannot_touch_other_participants_check(client, pid):
    finish_step1(client, pid)
    start(client, pid)
    other = create(client).json()["participant_id"]
    assert client.patch("/api/checks/1", headers=hdr(pid=other), json={"last_screen": "check_1"}).status_code == 404
    assert client.delete("/api/checks/1", headers=hdr(pid=other)).status_code == 404


def test_save_apis_need_entry_key(client, pid):
    finish_step1(client, pid)
    start(client, pid)
    assert client.put("/api/step1", headers=hdr(pid=pid, key=None), json={}).status_code == 403
    assert client.post("/api/checks", headers=hdr(pid=pid, key=None), json={"situations": ["none"]}).status_code == 403
    assert client.patch("/api/checks/1", headers=hdr(pid=pid, key="x"), json={}).status_code == 403
    assert client.delete("/api/checks/1", headers=hdr(pid=pid, key="x")).status_code == 403
    assert client.post("/api/events", headers=hdr(key=None), json={"events": []}).status_code == 403


# ---------------- 이벤트 ----------------
def test_events_before_leader_name_use_device_id(client):
    dev = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    r = client.post("/api/events", headers=hdr(device=dev), json={"events": [
        {"name": "screen_view", "session_id": sid,
         "props": {"screen": "notice", "visible_ms": 1200, "nickname": "몰래 별명"}},
    ]})
    assert r.json() == {"saved": 1, "dropped": 0}
    with db.get_session() as s:
        ev = s.query(db.Event).one()
        assert (ev.participant_id, ev.device_id, ev.session_id) == (None, dev, sid)
        assert ev.props == {"screen": "notice", "visible_ms": 1200}  # 정해 두지 않은 속성은 버림


def test_events_with_participant(client, pid):
    r = client.post("/api/events", headers=hdr(pid=pid, device=str(uuid.uuid4())), json={"events": [
        {"name": "answer_changed", "check_seq": 1, "props": {"item": "c1_1"}},
        {"name": "unknown_event", "props": {}},
        {"name": "blocked_next_tap", "props": {"screen": "nope"}},
        {"name": "action_custom_text", "props": {"text": "○○님 a@b.com 고마워요"}},
        {"name": "leader_switch", "props": {"leader_name": "남의 이름"}},   # 2026-11-27 공용 기기: 속성 없이 이름만 남는다
        {"name": "inapp_browser", "props": {"browser": "kakaotalk", "ua": "Mozilla/5.0 원문"}},   # 브라우저 종류만, UA 원문은 버림
    ]})
    assert r.json() == {"saved": 5, "dropped": 1}
    with db.get_session() as s:
        evs = {e.name: e for e in s.query(db.Event)}
        assert all(e.participant_id == pid and e.device_id is not None for e in evs.values())
        assert evs["answer_changed"].check_seq == 1
        assert evs["blocked_next_tap"].props == {"screen": None}
        assert evs["action_custom_text"].props == {"text": "○○님 고마워요"}
        assert evs["leader_switch"].props == {}
        assert evs["inapp_browser"].props == {"browser": "kakaotalk"}


def test_events_batch_limit(client):
    r = client.post("/api/events", headers=hdr(), json={"events": [{"name": "resume"}] * 51})
    assert r.status_code == 422


def test_screen_view_props_kept(client, pid):
    visit = str(uuid.uuid4())
    props = {"screen": "check_2", "visit": visit, "entered_at": "2026-11-26T01:00:00.000Z",
             "left_at": "2026-11-26T01:00:09.500Z", "visible_ms": 9500, "final": True, "ua": "x"}
    client.post("/api/events", headers=hdr(pid=pid), json={"events": [{"name": "screen_view", "props": props, "check_seq": 1}]})
    with db.get_session() as s:
        ev = s.query(db.Event).one()
        props.pop("ua")
        assert ev.props == props and ev.check_seq == 1


def test_content_sends_dwell_cutoff(client):
    assert client.get("/api/content", headers=hdr()).json()["settings"]["dwell_cutoff_sec"] == 180
