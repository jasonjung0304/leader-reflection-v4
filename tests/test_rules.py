"""rules.py 기본 테스트. 17문항 응답 조합 전체 테스트는 5단계에서 보강한다."""
import pytest

import main
import rules

C = main.load_content()
CARE = ["c1_3", "c2_1", "c3_1", "c4_2"]
CHAL = ["c3_2", "c3_3", "c3_4", "c4_1"]
NOAXIS = ["c1_1", "c1_2", "c2_2", "c2_3", "c4_3"]


def answers(done=(), signals=()):
    a = {i["id"]: ("done" if i["id"] in done else "not_done") for i in C["items"] if i["type"] == "behavior"}
    a.update({s: ("yes" if s in signals else "no") for s in ("s1", "s2", "s3", "s4")})
    return a


@pytest.mark.parametrize("done,signals,quadrant", [
    (CARE + CHAL, (), "rc"),
    (CARE, (), "re"),
    (CHAL, (), "oa"),
    ((), (), "mi"),
    (CARE[:2] + CHAL[:2], (), "rc"),           # 0.5 는 높음
    (CARE[:1] + CHAL, (), "oa"),               # 0.25 는 낮음
    (CARE + CHAL, ("s1", "s2"), "re"),         # 쓴소리 부족 신호 2개 → 쓴소리 낮음
    (CARE + CHAL, ("s1", "s3"), "rc"),         # 신호 1개씩은 칸이 바뀌지 않음
    (CARE + CHAL, ("s3", "s4"), "oa"),
])
def test_quadrant(done, signals, quadrant):
    out = rules.evaluate(answers(done, signals), ["none"], ["kw_none"], C)
    assert out["judgment"]["quadrant"] == quadrant


def test_threshold_setting():
    a = answers(CARE[:2] + CHAL[:2])
    assert rules.evaluate(a, ["none"], [], C, threshold=0.75)["judgment"]["quadrant"] == "mi"


def test_spec_example_pushback_re():
    """디자인 예시: 결과에 반발했어요 + 말하지 않는 리더."""
    out = rules.evaluate(answers(CARE + ["c1_1"]), ["pushback", "low"], ["kw_listening", "kw_aid"], C)
    r = out["results"]
    assert out["judgment"]["quadrant"] == "re"
    assert r["r2"] == {"summary": "care_only", "items": ["c1_3", "c2_1", "c3_1"], "low_axis": [], "empty": False}
    assert r["r3"] == {"item": "c3_2", "items": ["c3_2", "c3_3", "c3_4"], "keep": [], "empty": False, "empty_signals": False}
    assert r["phrase"] == "situation:pushback"
    assert r["action_options"] == [
        {"type": "recommended", "key": "situation:pushback"},
        {"type": "other_preset", "key": "situation:tears"},
        {"type": "other_preset", "key": "situation:compare"},
    ]
    assert r["r1"] == {"none": False, "keywords": [{"id": "kw_listening", "did": True}, {"id": "kw_aid", "did": False}],
                       "signal_low": []}


def test_r2_falls_back_to_no_axis_items():
    out = rules.evaluate(answers(["c1_1", "c2_2", "c4_3"]), ["none"], [], C)
    assert out["results"]["r2"]["items"] == ["c1_1", "c2_2", "c4_3"]


def test_r2_empty_when_nothing_done():
    out = rules.evaluate(answers(), ["none"], [], C)
    assert out["results"]["r2"]["empty"] is True and out["results"]["r2"]["items"] == []


def test_r3_all_done_keeps_and_phrase_from_r3_item():
    all_done = rules.evaluate(answers(CARE + CHAL + NOAXIS), ["none"], [], C)["results"]
    # 모두 해냈어요 → 결과 2에 없는 '했어요' 3개로 이어 가기, 추천은 공통 문장(2026-11-27)
    assert all_done["r3"] == {"item": None, "items": ["c1_1", "c1_2", "c2_2"], "keep": ["c1_1", "c1_2", "c2_2"],
                              "empty": True, "empty_signals": False}
    assert all_done["phrase"] == "general:all_done"
    out = rules.evaluate(answers(CARE + CHAL + NOAXIS[1:]), ["none"], [], C)["results"]
    assert out["r3"]["item"] == "c1_1" and out["phrase"] == "item:c1_1"
    assert out["action_options"][0] == {"type": "recommended", "key": "item:c1_1"}
    assert [o["key"] for o in out["action_options"][1:]] == ["situation:tears", "situation:pushback"]


@pytest.mark.parametrize("kw,done,did", [
    ("kw_opening", ["c1_1", "c1_2"], True),
    ("kw_opening", ["c1_1"], False),
    ("kw_aid", ["c3_2"], False),
    ("kw_aid", ["c3_2", "c3_3"], True),
    ("kw_fairness", ["c4_1", "c4_3"], True),
    ("kw_feedforward", ["c3_3"], True),
])
def test_keyword_rules(kw, done, did):
    out = rules.evaluate(answers(done), ["none"], [kw], C)
    assert out["results"]["r1"]["keywords"] == [{"id": kw, "did": did}]


def test_keyword_none():
    assert rules.evaluate(answers(), ["none"], ["kw_none"], C)["results"]["r1"] == {"none": True, "keywords": [], "signal_low": []}


def test_signal_low_only_when_behaviors_meet_threshold():
    """2026-11-27: 축이 낮음인데 실행률은 기준 이상 = 부족 신호 때문. 실행률이 모자라 낮음이면 설명하지 않는다."""
    by_signals = rules.evaluate(answers(CARE + CHAL[:2], ["s1", "s2"]), ["none"], [], C)["results"]["r1"]
    assert by_signals["signal_low"] == [{"axis": "challenge", "done": 2, "total": 4, "signals": 2}]
    by_behavior = rules.evaluate(answers(CARE + CHAL[:1], ["s1", "s2"]), ["none"], [], C)["results"]["r1"]
    assert by_behavior["signal_low"] == []
    strict = rules.evaluate(answers(CARE + CHAL[:2], ["s1", "s2"]), ["none"], [], C, threshold=0.75)["results"]["r1"]
    assert strict["signal_low"] == []      # 기준 0.75 에서는 2/4 가 이미 모자람
