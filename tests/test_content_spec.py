"""content.json 이 SPEC 의 표와 맞는지 (규칙이 content.json 을 읽으므로 여기서 어긋나면 결과가 틀린다)."""
from test_rules_exhaustive import (CARE_EXEC, CARE_SIG, CHAL_EXEC, CHAL_SIG, KEYWORDS, NO_AXIS, PAGE_ORDER,
                                   PRIORITY, SIGNALS)

import main

C = main.load_content()
ITEMS = {i["id"]: i for i in C["items"]}


def test_items_axis_and_type_match_spec_4_1():
    assert len(C["items"]) == 17
    for i in CARE_EXEC:
        assert (ITEMS[i]["type"], ITEMS[i]["axis"]) == ("behavior", "care")
    for i in CHAL_EXEC:
        assert (ITEMS[i]["type"], ITEMS[i]["axis"]) == ("behavior", "challenge")
    for i in NO_AXIS:
        assert (ITEMS[i]["type"], ITEMS[i]["axis"]) == ("behavior", None)
    for i in CARE_SIG:
        assert (ITEMS[i]["type"], ITEMS[i]["axis"]) == ("signal", "care")
    for i in CHAL_SIG:
        assert (ITEMS[i]["type"], ITEMS[i]["axis"]) == ("signal", "challenge")


def test_item_order_is_check_page_order():
    """같은 순위 안에서는 체크 페이지 순서(4.5) — rules.py 는 items 순서를 쓴다."""
    pages = [i for p in C["checklist_pages"] for i in p["items"]]
    assert pages == PAGE_ORDER + SIGNALS
    assert [i["id"] for i in C["items"]] == pages
    for p in C["checklist_pages"]:
        assert all(ITEMS[i]["page"] == p["page"] for i in p["items"])


def test_behavior_items_have_result_texts():
    for i in PAGE_ORDER:
        it = ITEMS[i]
        for key in ("text", "tool", "effect", "advice", "phrase", "next_label"):
            assert it.get(key), (i, key)


def test_signals_and_general_phrase_have_result_texts():
    """2026-11-27: 결과 3 은 부족 신호(체크 5)와 이어 가기로 늘 3개를 채우고, 상황·결과 3 항목이 없으면 공통 문장을 추천한다."""
    for i in ("s1", "s2", "s3", "s4"):
        for key in ("text", "advice", "phrase", "next_label"):
            assert ITEMS[i].get(key), (i, key)
        assert ITEMS[i]["phrase"].startswith("“") and ITEMS[i]["phrase"].endswith("”")
    assert C["general_phrases"]["all_done"].startswith("“")
    r3 = C["results"]["r3"]
    assert r3["signal_cap"] and r3["keep_cap"]


def test_shared_device_and_custom_note_copy():
    """2026-11-27: 이어 하기 안내는 누구 기록인지 보여 주고 '다른 리더예요'를 둔다. 직접 쓴 문장 안내는 실제 처리와 맞춘다."""
    ui = C["ui"]
    assert "[리더명]" in ui["resume_banner"] and ui["switch_leader"] and "{leader}" in ui["switch_leader_confirm"]
    note = C["action"]["custom_note"]
    assert "실명" in note and "○○" in note and "이름과 별명을 지운" not in note


def test_medium_issue_copy_2026_11_27():
    """결과 1 설명 줄, 앱 안 브라우저 안내(안내·완료 화면), 저장소 만료 대비 이어 하기 안내."""
    r1 = C["results"]["r1"]
    assert all(k in r1["signal_low"] for k in ("{axis}", "{done}", "{total}", "{signals}"))
    assert set(r1["axis_names"]) == {"care", "challenge"}
    assert C["notice"]["inapp_hint"] and C["done"]["inapp_note"]
    assert "브라우저" in C["notice"]["resume_note"] and "한 번에" in C["notice"]["resume_note"]


def test_low_issue_copy_2026_11_27():
    assert "{axis}" in C["results"]["r2"]["low_axis_cap"]
    A = C["action"]
    assert "{label}" in A["item_option_label"] and A["general_option_label"]
    rule = C["leader_name"]["custom"]["name_like"]
    assert rule["surnames"] and rule["exclude_endings"] and rule["hint"]


def test_shared_keyword_items_are_known():
    """같은 체크 문항을 두 키워드가 함께 쓰는 곳(2026-11-27 기준 2곳). 바뀌면 관리자 2→3 보조 지표 안내·SPEC 의 분리 제안을 함께 고친다."""
    by_item = {}
    for k in C["keywords"]["items"]:
        for i in k["linked_items"]:
            by_item.setdefault(i, []).append(k["id"])
    shared = {i: sorted(ks) for i, ks in by_item.items() if len(ks) > 1}
    assert shared == {"c3_3": ["kw_aid", "kw_feedforward"], "c4_3": ["kw_fairness", "kw_rating_error"]}


def test_situation_priority_matches_spec_4_7():
    sits = [s for s in C["member"]["situations"] if s["id"] != "none"]
    assert [s["id"] for s in sorted(sits, key=lambda s: s["priority"])] == PRIORITY
    none = [s for s in C["member"]["situations"] if s["id"] == "none"]
    assert len(none) == 1 and none[0].get("exclusive") is True
    assert set(C["situation_phrases"]) == set(PRIORITY)
    assert set(C["situation_labels_for_action"]) == set(PRIORITY)


def test_keyword_links_match_result1_table():
    got = {k["id"]: (k["linked_items"], k["min_done"]) for k in C["keywords"]["items"]}
    assert got == {k: (v[0], v[1]) for k, v in KEYWORDS.items()}


def test_quadrants_and_leader_name_options_match_spec_3_1():
    levels = {"rc": ("high", "high"), "re": ("high", "low"), "oa": ("low", "high"), "mi": ("low", "low")}
    for q, (care, chal) in levels.items():
        assert (C["quadrants"][q]["care"], C["quadrants"][q]["challenge"]) == (care, chal)
        assert C["quadrants"][q]["desc"]
    opts = {o["type"]: (o["care"], o["challenge"]) for o in C["leader_name"]["options"]}
    assert opts == levels


def test_result_texts_present():
    r = C["results"]
    assert set(r["r2"]["summary"]) == {"both_high", "care_only", "challenge_only", "both_low"}
    assert r["r2"]["max_items"] == 3 and r["r3"]["max_items"] == 3   # 2026-11-26 결정: 결과 2·3 최대 3개
    assert r["r2"]["empty"] and r["r3"]["empty"] and r["r3"]["empty_signals"] and r["r1"]["recall_none"]
    assert "{quadrant_label}" in r["r1"]["title"]


def test_footer_shows_team_not_version():
    """2026-11-27: 화면·PDF 하단에는 버전 대신 만든 팀을 보여 준다(버전은 /api/health 로만)."""
    assert C["ui"]["credit"] == "Culture팀"
    footer = C["done"]["pdf"]["footer"]
    assert "{credit}" in footer and "{version}" not in footer


def test_check_intro_screen_between_keyword_and_member():
    """2026-11-27: 키워드 버튼은 '퀵! 리뷰 끝내기', 다음에 점검 시작 안내(check_intro)를 하나 더 둔다.
    화면 기록(screen_view)이 받아지고 관리자 화면별 통계에도 이름이 있어야 한다."""
    import json

    import admin_stats
    assert C["keywords"]["cta"] == "퀵! 리뷰 끝내기"
    ci = C["check_intro"]
    assert ci["title"].replace("\n", " ") == "이제 본격적으로 면담 스타일을 점검해볼까요?"
    assert ci["note"] == "개인정보 수집항목이 없는 자가점검입니다."
    assert ci["step"] == C["member"]["step"] and C["ui"]["check_intro_next_aria"]
    for screens in (main.SCREEN_IDS, admin_stats.SCREENS):
        assert screens.index("check_intro") == screens.index("keyword") + 1 == screens.index("member") - 1
    labels = json.loads((main.BASE_DIR / "content" / "admin.json").read_text(encoding="utf-8"))["screens"]
    assert set(labels) == set(admin_stats.SCREENS)
