"""SPEC 4장 결과 규칙 — 17문항 응답 조합 전체(2^17 = 131,072가지) 검사.

rules.py 와 따로, SPEC 문장을 그대로 옮긴 '기준 구현'(아래 ref_*)을 만들고 두 결과가 모든 조합에서 같은지 본다.
기준 구현은 content.json 을 읽지 않고 SPEC 4.1 표·4.7 우선순위·결과 1 키워드 연결표(Result3 디자인 주석)를
직접 적어 둔다. 그래서 content.json 의 축·순서·연결이 SPEC 과 어긋나도 이 테스트가 잡는다.
"""
import itertools

import pytest

import main
import rules

C = main.load_content()

# --- SPEC 4.1 표 -------------------------------------------------------------
CARE_EXEC = ["c1_3", "c2_1", "c3_1", "c4_2"]
CHAL_EXEC = ["c3_2", "c3_3", "c3_4", "c4_1"]
NO_AXIS = ["c1_1", "c1_2", "c2_2", "c2_3", "c4_3"]
CARE_SIG = ["s3", "s4"]          # 불쾌한 공격성 쪽
CHAL_SIG = ["s1", "s2"]          # 파괴적 공감 쪽
# 체크 페이지 순서(체크 1~5)
PAGE_ORDER = ["c1_1", "c1_2", "c1_3", "c2_1", "c2_2", "c2_3", "c3_1", "c3_2", "c3_3", "c3_4", "c4_1", "c4_2", "c4_3"]
SIGNALS = ["s1", "s2", "s3", "s4"]
ALL_IDS = PAGE_ORDER + SIGNALS
# --- SPEC 4.7 우선순위 --------------------------------------------------------
PRIORITY = ["tears", "pushback", "low", "compare", "gap", "senior", "star"]
# --- 결과 1 키워드 연결(디자인 Result3 주석): (연결 문항, '했어요'로 보는 최소 개수) ---
KEYWORDS = {
    "kw_opening": (["c1_1", "c1_2", "c1_3"], 2),
    "kw_listening": (["c2_1"], 1),
    "kw_expand_q": (["c2_2"], 1),
    "kw_future_q": (["c2_3"], 1),
    "kw_ait": (["c3_1"], 1),
    "kw_aid": (["c3_2", "c3_3"], 2),
    "kw_feedforward": (["c3_3"], 1),
    "kw_fairness": (["c4_1", "c4_2", "c4_3"], 2),
    "kw_rating_error": (["c4_3"], 1),
}
QUAD = {(True, True): "rc", (True, False): "re", (False, True): "oa", (False, False): "mi"}
SUMMARY = {(True, True): "both_high", (True, False): "care_only", (False, True): "challenge_only", (False, False): "both_low"}


def ref_axis(ans, exec_items, sig_items, th):
    done = sum(ans[i] == "done" for i in exec_items)
    yes = sum(ans[i] == "yes" for i in sig_items)
    return (done / 4) >= th and yes <= 1          # 4.2: 분모는 항상 4


def ref(ans, situations, keywords, th=0.5):
    care = ref_axis(ans, CARE_EXEC, CARE_SIG, th)
    chal = ref_axis(ans, CHAL_EXEC, CHAL_SIG, th)
    high = {i: care for i in CARE_EXEC} | {i: chal for i in CHAL_EXEC}
    # 4.5 결과 2 (최대 3개: 높음 축 '했어요' → 축 없음 '했어요' → 낮음 축 '했어요')
    first = [i for i in PAGE_ORDER if i in high and high[i] and ans[i] == "done"]
    second = [i for i in PAGE_ORDER if i in NO_AXIS and ans[i] == "done"]
    third = [i for i in PAGE_ORDER if i in high and not high[i] and ans[i] == "done"]
    r2_items = (first + second + third)[:3]
    r2 = {"summary": SUMMARY[(care, chal)], "items": r2_items,
          "low_axis": [i for i in r2_items if i in third],          # 2026-11-27: 낮음 축에서 온 항목 표시
          "empty": not any(ans[i] == "done" for i in PAGE_ORDER)}
    # 4.6 결과 3 (2026-11-27: 늘 3개) 낮음 축 '못 했어요' → 축 없음 → 높음 축 → 체크 5 '그랬어요'(낮음 축 신호 먼저)
    #     → 이어 가기('했어요' 중 결과 2에 없는 것, 페이지 순서). 기준 항목(item)은 이어 가기 앞의 첫 항목
    first = [i for i in PAGE_ORDER if i in high and not high[i] and ans[i] == "not_done"]
    second = [i for i in PAGE_ORDER if i in NO_AXIS and ans[i] == "not_done"]
    third = [i for i in PAGE_ORDER if i in high and high[i] and ans[i] == "not_done"]
    sig_high = {"s1": chal, "s2": chal, "s3": care, "s4": care}
    sigs = ([i for i in SIGNALS if ans[i] == "yes" and not sig_high[i]]
            + [i for i in SIGNALS if ans[i] == "yes" and sig_high[i]])
    keep = [i for i in PAGE_ORDER if ans[i] == "done" and i not in r2["items"]]
    tries = first + second + third
    r3_item = (tries + sigs)[0] if tries + sigs else None
    r3_items = (tries + sigs + keep)[:3]
    # 4.7 꼭 해야 하는 말 (2026-11-27: 상황도 결과 3 항목도 없으면 공통 문장)
    chosen = [s for s in PRIORITY if s in situations]
    phrase = f"situation:{chosen[0]}" if chosen else (f"item:{r3_item}" if r3_item else "general:all_done")
    options = ([{"type": "recommended", "key": phrase}] if phrase else [])
    # 남은 상황 문장 → (2026-11-27) 모자라면 결과 3 항목(이어 가기 제외)의 말 → 공통 문장
    candidates = [f"situation:{s}" for s in PRIORITY if s not in situations]
    candidates += [f"item:{i}" for i in r3_items if i not in keep] + ["general:all_done"]
    for key in candidates:
        if len(options) >= 3:
            break
        if all(o["key"] != key for o in options):
            options.append({"type": "other_preset", "key": key})
    # 4.4 결과 1
    if keywords == ["kw_none"]:
        r1 = {"none": True, "keywords": []}
    else:
        r1 = {"none": False, "keywords": [
            {"id": k, "did": sum(ans[i] == "done" for i in KEYWORDS[k][0]) >= KEYWORDS[k][1]} for k in keywords]}
    # 4.3 보충(2026-11-27): 실행률은 기준 이상인데 부족 신호 2개 이상이라 낮음이 된 축 → 결과 1 설명 한 줄
    r1["signal_low"] = []
    for axis, ex, sg, high_ in (("care", CARE_EXEC, CARE_SIG, care), ("challenge", CHAL_EXEC, CHAL_SIG, chal)):
        done = sum(ans[i] == "done" for i in ex)
        yes = sum(ans[i] == "yes" for i in sg)
        if not high_ and done / 4 >= th:
            r1["signal_low"].append({"axis": axis, "done": done, "total": 4, "signals": yes})
    return {"quadrant": QUAD[(care, chal)],
            "results": {"r1": r1, "r2": r2, "r3": {"item": r3_item, "items": r3_items,
                                                   "keep": [i for i in r3_items if i in keep],
                                                   "empty": not (tries or sigs), "empty_signals": not tries and bool(sigs)},
                        "phrase": phrase, "action_options": options}}


def answers_from_bits(bits):
    return {i: (("done" if b else "not_done") if i.startswith("c") else ("yes" if b else "no"))
            for i, b in zip(ALL_IDS, bits)}


SITUATION_SETS = [
    ["none"],
    ["pushback", "low"],
    ["star"],
    ["tears", "pushback", "low", "compare", "gap", "senior", "star"],
    ["gap", "senior"],
    ["compare", "tears"],
    ["low"],
]
KEYWORD_SETS = [
    ["kw_none"],
    ["kw_opening", "kw_aid", "kw_fairness"],
    ["kw_listening", "kw_ait", "kw_feedforward"],
    ["kw_expand_q", "kw_future_q", "kw_rating_error"],
    ["kw_aid"],
]


def test_all_answer_combinations_match_spec():
    """17문항 모든 조합. 상황·키워드 묶음은 조합마다 돌려 가며 붙인다(7×5 = 35가지 짝이 고르게 섞임)."""
    mismatches = []
    counts = {"rc": 0, "re": 0, "oa": 0, "mi": 0}
    for n, bits in enumerate(itertools.product((0, 1), repeat=len(ALL_IDS))):
        ans = answers_from_bits(bits)
        sits = SITUATION_SETS[n % len(SITUATION_SETS)]
        kws = KEYWORD_SETS[n % len(KEYWORD_SETS)]
        got = rules.evaluate(ans, sits, kws, C)
        want = ref(ans, sits, kws)
        counts[got["judgment"]["quadrant"]] += 1
        if got["judgment"]["quadrant"] != want["quadrant"] or got["results"] != want["results"]:
            mismatches.append((ans, sits, kws, got, want))
            if len(mismatches) >= 5:
                break
    assert not mismatches, mismatches[0]
    assert sum(counts.values()) == 2 ** 17
    assert all(counts.values())  # 네 칸 모두 나올 수 있다


@pytest.mark.parametrize("th", [0.25, 0.5, 0.75, 1.0])
def test_threshold_setting_all_axis_combinations(th):
    """판정 기준값(설정값)을 바꿔도 SPEC 4.3 식대로. 축 문항 8개 + 부족 신호 4개의 모든 조합(4,096가지)."""
    axis_ids = CARE_EXEC + CHAL_EXEC + SIGNALS
    for bits in itertools.product((0, 1), repeat=len(axis_ids)):
        ans = answers_from_bits([0] * len(ALL_IDS))
        for i, b in zip(axis_ids, bits):
            ans[i] = ("done" if b else "not_done") if i.startswith("c") else ("yes" if b else "no")
        got = rules.evaluate(ans, ["none"], ["kw_none"], C, threshold=th)
        assert got["judgment"]["quadrant"] == ref(ans, ["none"], ["kw_none"], th)["quadrant"]
        assert got["judgment"]["threshold"] == th


@pytest.mark.parametrize("kw", sorted(KEYWORDS))
def test_each_keyword_over_its_linked_items(kw):
    """키워드마다 연결 문항의 모든 조합에서 결과 1 표시가 맞는지."""
    linked, need = KEYWORDS[kw]
    for bits in itertools.product((0, 1), repeat=len(linked)):
        ans = answers_from_bits([0] * len(ALL_IDS))
        for i, b in zip(linked, bits):
            ans[i] = "done" if b else "not_done"
        r1 = rules.evaluate(ans, ["none"], [kw], C)["results"]["r1"]
        assert r1 == {"none": False, "keywords": [{"id": kw, "did": sum(bits) >= need}], "signal_low": []}


def test_keyword_order_is_kept():
    ans = answers_from_bits([1] * len(ALL_IDS))
    order = ["kw_fairness", "kw_listening", "kw_opening"]
    assert [k["id"] for k in rules.evaluate(ans, ["none"], order, C)["results"]["r1"]["keywords"]] == order


# --- SPEC 에 적힌 경계·예외 ---------------------------------------------------
def test_exact_half_is_high_and_one_signal_does_not_flip():
    ans = answers_from_bits([0] * len(ALL_IDS))
    for i in CARE_EXEC[:2] + CHAL_EXEC[:2]:
        ans[i] = "done"
    ans["s1"] = ans["s3"] = "yes"
    assert rules.evaluate(ans, ["none"], [], C)["judgment"]["quadrant"] == "rc"


def test_two_signals_make_axis_low_even_when_all_done():
    ans = answers_from_bits([1] * len(PAGE_ORDER) + [0, 0, 0, 0])
    ans["s3"] = ans["s4"] = "yes"
    out = rules.evaluate(ans, ["none"], [], C)
    assert out["judgment"]["quadrant"] == "oa"
    # 결과 1 이 '행동은 4/4 했지만 체크 5 순간 2개라 낮음'을 설명할 수 있게(2026-11-27)
    assert out["results"]["r1"]["signal_low"] == [{"axis": "care", "done": 4, "total": 4, "signals": 2}]
    # 케어 축이 낮음이지만 '못 했어요'가 없다 → 결과 3 은 고른 부족 신호 2개 + 이어 가기 1개, 안내는 empty_signals
    assert out["results"]["r3"] == {"item": "s3", "items": ["s3", "s4", "c1_1"], "keep": ["c1_1"],
                                    "empty": False, "empty_signals": True}
    assert out["results"]["phrase"] == "item:s3"


def test_both_high_axis_not_done_is_phrase_source():
    """4.6/4.7 (2026-11-27): 두 축 모두 높음이어도 축 문항의 '못 했어요'는 결과 3 첫 항목이자 추천 문장의 기준이 된다.
    (이전: 앞의 두 순위에서만 골라 추천 문장이 없었음) 나머지 두 칸은 결과 2에 없는 '했어요'로 이어 가기."""
    ans = answers_from_bits([1] * len(PAGE_ORDER) + [0, 0, 0, 0])
    ans["c2_1"] = "not_done"      # 케어 3/4 → 여전히 높음
    out = rules.evaluate(ans, ["none"], [], C)
    assert out["judgment"]["quadrant"] == "rc"
    assert out["results"]["r2"]["items"] == ["c1_3", "c3_1", "c3_2"]
    assert out["results"]["r3"] == {"item": "c2_1", "items": ["c2_1", "c1_1", "c1_2"], "keep": ["c1_1", "c1_2"],
                                    "empty": False, "empty_signals": False}
    assert out["results"]["phrase"] == "item:c2_1"


def test_all_done_with_all_signals_is_low_but_says_signals():
    """2026-11-26: 행동 13개 모두 '했어요' + 부족 신호 4개 모두 '있었어요' → 두 축 낮음(방임형).
    결과 3 은 '모두 해냈어요' 대신 empty_signals 문구(체크 5 에서 고른 순간을 살펴보기)를 쓴다."""
    ans = answers_from_bits([1] * len(ALL_IDS))
    out = rules.evaluate(ans, ["none"], [], C)
    assert out["judgment"]["quadrant"] == "mi"
    r3 = out["results"]["r3"]
    assert r3["items"] == ["s1", "s2", "s3"] and r3["keep"] == [] and r3["empty_signals"] is True
    all_no = rules.evaluate(answers_from_bits([1] * len(PAGE_ORDER) + [0, 0, 0, 0]), ["none"], [], C)
    assert all_no["results"]["r3"]["empty_signals"] is False and all_no["results"]["r3"]["empty"] is True


def test_r2_empty_only_when_no_behavior_done():
    ans = answers_from_bits([0] * len(PAGE_ORDER) + [1, 1, 1, 1])
    r2 = rules.evaluate(ans, ["none"], [], C)["results"]["r2"]
    assert r2 == {"summary": "both_low", "items": [], "low_axis": [], "empty": True}


def test_r2_low_axis_done_is_third_tier():
    """낮음 축 문항만 '했어요'여도 세 번째 순위로 보여 준다(이전에는 empty 가 아닌데 행동이 0개였던 빈틈)."""
    ans = answers_from_bits([0] * len(ALL_IDS))
    ans["c1_3"] = "done"          # 케어 1/4 → 낮음
    r2 = rules.evaluate(ans, ["none"], [], C)["results"]["r2"]
    assert r2 == {"summary": "both_low", "items": ["c1_3"], "low_axis": ["c1_3"], "empty": False}


def test_r2_order_high_axis_before_no_axis_then_page_order():
    ans = answers_from_bits([1] * len(PAGE_ORDER) + [0, 0, 0, 0])
    # 두 축 높음 → 축 문항 중 페이지 순서 앞 3개(c1_3, c2_1, c3_1). 축 없음 c1_1 은 체크 1에 있어도 뒤로 밀린다
    assert rules.evaluate(ans, ["none"], [], C)["results"]["r2"]["items"] == ["c1_3", "c2_1", "c3_1"]


def test_three_items_whenever_three_eligible():
    """결과 2 는 '했어요'가 3개 이상이면 3개(적으면 있는 만큼, 2026-11-26).
    결과 3 은 늘 3개(2026-11-27), 결과 2 와 겹치지 않고, 이어 가기는 '못 했어요'·신호가 모자랄 때만 뒤쪽에."""
    for bits in itertools.product((0, 1), repeat=len(ALL_IDS)):
        ans = answers_from_bits(bits)
        res = rules.evaluate(ans, ["none"], [], C)["results"]
        done = sum(bits[:len(PAGE_ORDER)])
        r2, r3, keep = res["r2"]["items"], res["r3"]["items"], res["r3"]["keep"]
        assert len(r2) == min(3, done) and len(set(r2)) == len(r2)
        assert len(r3) == 3 and len(set(r3)) == 3 and not set(r2) & set(r3)
        assert r3[len(r3) - len(keep):] == keep and all(ans[i] == "done" for i in keep)
        if res["r3"]["item"]:
            assert r3[0] == res["r3"]["item"] and res["r3"]["item"] not in keep
        else:
            assert keep == r3 and res["phrase"] == "general:all_done"


def test_all_situations_selected_still_three_options():
    """2026-11-27: 상황 7개를 모두 고르면 남은 상황 문장이 없으므로 결과 3 의 말(못 한 행동·신호) → 공통 문장으로 채워 3개.
    (이전: 추천 1개뿐 + 직접 쓰기)"""
    ans = answers_from_bits([0] * len(ALL_IDS))
    opts = rules.evaluate(ans, PRIORITY, [], C)["results"]["action_options"]
    assert opts == [{"type": "recommended", "key": "situation:tears"},
                    {"type": "other_preset", "key": "item:c1_3"}, {"type": "other_preset", "key": "item:c2_1"}]
    all_done = answers_from_bits([1] * len(PAGE_ORDER) + [0, 0, 0, 0])      # 결과 3 이 이어 가기뿐 → 공통 문장 1개
    opts = rules.evaluate(all_done, PRIORITY, [], C)["results"]["action_options"]
    assert opts == [{"type": "recommended", "key": "situation:tears"}, {"type": "other_preset", "key": "general:all_done"}]
    six = rules.evaluate(ans, PRIORITY[:6], [], C)["results"]["action_options"]   # 고성과자만 남음
    assert [o["key"] for o in six] == ["situation:tears", "situation:star", "item:c1_3"]


def test_action_options_three_unless_all_done_and_all_situations():
    """모든 응답 조합 × 상황 7개 전부: 결과 3 에 못 한 행동이나 신호가 2개 이상이면 언제나 보기 3개."""
    for bits in itertools.product((0, 1), repeat=len(ALL_IDS)):
        res = rules.evaluate(answers_from_bits(bits), PRIORITY, [], C)["results"]
        tries = [i for i in res["r3"]["items"] if i not in res["r3"]["keep"]]
        assert len(res["action_options"]) == min(3, 1 + len(tries) + 1)
        assert len({o["key"] for o in res["action_options"]}) == len(res["action_options"])


def test_general_phrase_when_none_and_everything_done():
    """2026-11-27: '특별한 상황 없음' + 모두 해냈어요 + 부족 신호 없음 → 공통 문장이 추천, 나머지 2개는 우선순위 상황 문장.
    (이전: 추천 문장 없이 상황 문장 3개만)"""
    ans = answers_from_bits([1] * len(PAGE_ORDER) + [0, 0, 0, 0])
    res = rules.evaluate(ans, ["none"], [], C)["results"]
    assert res["phrase"] == "general:all_done"
    assert res["action_options"] == [{"type": "recommended", "key": "general:all_done"},
                                     {"type": "other_preset", "key": "situation:tears"},
                                     {"type": "other_preset", "key": "situation:pushback"}]
    assert res["r3"]["empty"] is True and res["r3"]["keep"] == res["r3"]["items"] == ["c1_1", "c1_2", "c2_2"]


def test_every_phrase_key_resolves_to_text():
    """추천 문장 키는 언제나 content.json 의 문장으로 바뀔 수 있어야 하고, 상황 '없음'이어도 추천이 하나는 있다."""
    items = {i["id"]: i for i in C["items"]}
    texts = {"situation": C["situation_phrases"], "general": C["general_phrases"]}
    for n, bits in enumerate(itertools.product((0, 1), repeat=len(ALL_IDS))):
        ans = answers_from_bits(bits)
        res = rules.evaluate(ans, SITUATION_SETS[n % len(SITUATION_SETS)], [], C)["results"]
        assert res["phrase"] and res["action_options"][0]["type"] == "recommended"
        for key in [res["phrase"]] + [o["key"] for o in res["action_options"]]:
            kind, ref_id = key.split(":")
            text = items[ref_id]["phrase"] if kind == "item" else texts[kind][ref_id]
            assert text.startswith("“")
