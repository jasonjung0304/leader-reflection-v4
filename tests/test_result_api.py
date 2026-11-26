"""결과 계산 API: 판정 기준값 설정, 결과 후 답 변경 시 재계산, 완료 시 저장, 실천 보기 검증."""
import pytest
from conftest import create, hdr
from test_rules_exhaustive import CARE_EXEC, CHAL_EXEC, PAGE_ORDER

import db
import main

REVIEWS = [r["id"] for r in main.load_content()["reviews"]]


def answers(done=(), signals=()):
    a = {i: ("done" if i in done else "not_done") for i in PAGE_ORDER}
    a.update({s: ("yes" if s in signals else "no") for s in ("s1", "s2", "s3", "s4")})
    return a


@pytest.fixture()
def run(client):
    """STEP 1 을 끝내고(키워드 2개) 상황을 골라 점검을 시작한 참여자."""
    pid = create(client).json()["participant_id"]
    client.put("/api/step1", headers=hdr(pid=pid), json={
        "reviews": {r: "remembered" for r in REVIEWS}, "keywords": ["kw_listening", "kw_aid"], "complete": True})
    client.post("/api/checks", headers=hdr(pid=pid), json={"situations": ["pushback", "low"]})
    return pid


def patch(client, pid, **body):
    return client.patch("/api/checks/1", headers=hdr(pid=pid), json=body)


def result(client, pid):
    return client.get("/api/checks/1/result", headers=hdr(pid=pid))


def test_result_needs_all_answers(client, run):
    patch(client, run, answers={"c1_1": "done"})
    r = result(client, run)
    assert r.status_code == 422 and r.json() == {"error": "check_incomplete", "message": "answers"}


def test_result_spec_example_and_saved(client, run):
    patch(client, run, answers=answers(CARE_EXEC + ["c1_1"]))
    body = result(client, run).json()
    assert body["quadrant"] == "re"
    res = body["results"]
    assert res["r1"]["keywords"] == [{"id": "kw_listening", "did": True}, {"id": "kw_aid", "did": False}]
    assert res["r2"]["items"] == ["c1_3", "c2_1", "c3_1"] and res["r3"]["item"] == "c3_2"
    assert res["r3"]["items"] == ["c3_2", "c3_3", "c3_4"]
    assert res["phrase"] == "situation:pushback"
    with db.get_session() as s:
        c = s.query(db.Check).one()
        assert c.judgment["quadrant"] == "re" and c.results == res
        assert c.judgment["axes"]["care"] == {"done": 4, "total": 4, "rate": 1.0, "signals": 0, "high": True}
    # 이어 하기에서도 같은 결과
    me = client.get("/api/me", headers=hdr(pid=run)).json()
    assert me["check_in_progress"]["quadrant"] == "re"


def test_result_recomputed_after_answer_change(client, run):
    patch(client, run, answers=answers(CARE_EXEC))
    assert result(client, run).json()["quadrant"] == "re"
    patch(client, run, answers={i: "done" for i in CHAL_EXEC})   # 결과에서 체크로 돌아가 답을 바꿈
    assert result(client, run).json()["quadrant"] == "rc"


def test_threshold_setting_used(client, run):
    patch(client, run, answers=answers(CARE_EXEC[:2] + CHAL_EXEC[:2]))
    assert result(client, run).json()["quadrant"] == "rc"
    with db.get_session() as s:
        db.save_settings(s, {"high_threshold": 0.75})
    assert result(client, run).json()["quadrant"] == "mi"


def test_completed_check_keeps_stored_result(client, run):
    patch(client, run, answers=answers(CARE_EXEC[:2] + CHAL_EXEC[:2]))
    r = patch(client, run, action_type="recommended", action_key="situation:pushback", complete=True)
    assert r.status_code == 200 and r.json()["quadrant"] == "rc"
    with db.get_session() as s:
        db.save_settings(s, {"high_threshold": 0.75})
    # 완료 뒤에 기준값을 바꿔도 그 점검의 결과는 바뀌지 않는다
    assert result(client, run).json()["quadrant"] == "rc"


def test_complete_computes_even_without_result_call(client, run):
    patch(client, run, answers=answers(CHAL_EXEC))
    r = patch(client, run, action_type="recommended", action_key="situation:pushback", complete=True)
    assert r.status_code == 200 and r.json()["quadrant"] == "oa"


@pytest.mark.parametrize("action_type,key,ok", [
    ("recommended", "situation:pushback", True),
    ("recommended", "situation:tears", False),      # 추천 문장이 아님
    ("other_preset", "situation:tears", True),      # 고르지 않은 상황 중 1순위
    ("other_preset", "situation:compare", True),    # 고르지 않은 상황 중 2순위
    ("other_preset", "situation:gap", False),       # 보기에 없는 문장
    ("other_preset", "situation:low", False),       # 고른 상황 → 보기에 없음
])
def test_action_key_must_be_one_of_the_options(client, run, action_type, key, ok):
    patch(client, run, answers=answers(CARE_EXEC))
    r = patch(client, run, action_type=action_type, action_key=key, complete=True)
    assert (r.status_code == 200) is ok, r.json()
    if not ok:
        assert r.json()["error"] == "bad_action_key"
        with db.get_session() as s:
            assert s.query(db.Check).one().status == "in_progress"


def test_result_of_other_participant_not_visible(client, run):
    patch(client, run, answers=answers())
    other = create(client).json()["participant_id"]
    assert client.get("/api/checks/1/result", headers=hdr(pid=other)).status_code == 404
    assert client.get("/api/checks/1/result", headers=hdr(pid=run, key=None)).status_code == 403
