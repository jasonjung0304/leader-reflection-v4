"""결과 규칙(SPEC 4장) — 순수 함수. AI 를 쓰지 않는다.

입력: 17문항 응답, 고른 상황, STEP 1 키워드, content.json, 판정 기준값
출력: 축 판정·4분면, 결과 1(기억한 것과 실제로 한 것), 결과 2(잘한 점), 결과 3(다음에 해 볼 것),
      꼭 해야 하는 말(추천 문장), 실천 화면 보기 3개

반환값은 문구가 아니라 식별자(문항 ID, 상황 ID, 요약 키)다. 화면은 content.json 에서 문구를 찾는다.
"""
from __future__ import annotations

from typing import Any, Optional

DONE, NOT_DONE = "done", "not_done"
YES = "yes"
AXES = ("care", "challenge")
QUADRANT = {(True, True): "rc", (True, False): "re", (False, True): "oa", (False, False): "mi"}
SUMMARY_KEY = {(True, True): "both_high", (True, False): "care_only",
               (False, True): "challenge_only", (False, False): "both_low"}
KEYWORD_NONE = "kw_none"
SITUATION_NONE = "none"
GENERAL_ALL_DONE = "all_done"


def _behavior_items(content: dict) -> list[dict]:
    """체크 페이지 순서(= content.items 순서)의 행동 문항."""
    return [i for i in content["items"] if i["type"] == "behavior"]


def judge_axes(answers: dict[str, str], content: dict, threshold: float = 0.5) -> dict[str, dict]:
    """축별 실행률·부족 신호 수·높음 여부 (SPEC 4.3). 분모는 그 축의 실행 문항 수(4)."""
    out = {}
    for axis in AXES:
        exec_items = [i["id"] for i in _behavior_items(content) if i.get("axis") == axis]
        signal_items = [i["id"] for i in content["items"] if i["type"] == "signal" and i.get("axis") == axis]
        done = sum(1 for i in exec_items if answers.get(i) == DONE)
        signals = sum(1 for i in signal_items if answers.get(i) == YES)
        rate = done / len(exec_items) if exec_items else 0.0
        out[axis] = {"done": done, "total": len(exec_items), "rate": rate, "signals": signals,
                     "high": rate >= threshold and signals <= 1}
    return out


def keyword_result(keyword: dict, answers: dict[str, str]) -> bool:
    """키워드 하나가 이번 면담에서 '했어요'인지 (SPEC 4.4).

    연결 문항 중 '했어요'가 min_done 개 이상이면 '했어요'. min_done 이 없으면 연결 문항 모두.
    (content.keywords.items[].min_done — 면담 오프닝·공정성 3요소는 3개 중 2개, 발전적 피드백 AID 는 2개 모두)
    """
    linked = keyword["linked_items"]
    done = sum(1 for i in linked if answers.get(i) == DONE)
    return done >= keyword.get("min_done", len(linked))


def recall(keywords: list[str], answers: dict[str, str], content: dict) -> dict:
    if not keywords or keywords == [KEYWORD_NONE]:
        return {"none": True, "keywords": []}
    by_id = {k["id"]: k for k in content["keywords"]["items"]}
    return {"none": False,
            "keywords": [{"id": k, "did": keyword_result(by_id[k], answers)} for k in keywords if k in by_id]}


def signal_low(axes: dict[str, dict], threshold: float) -> list[dict]:
    """실행률은 기준값 이상인데 부족 신호가 2개 이상이라 '낮음'이 된 축(2026-11-27 결정).
    결과 1 이 '행동은 했는데 왜 낮음인지' 한 줄로 설명할 수 있게 축별 했어요 수·신호 수를 돌려준다."""
    return [{"axis": a, "done": axes[a]["done"], "total": axes[a]["total"], "signals": axes[a]["signals"]}
            for a in AXES if not axes[a]["high"] and axes[a]["rate"] >= threshold]


def _tiers(answers: dict[str, str], axes: dict[str, dict], content: dict, value: str, high_first: bool) -> list[list[str]]:
    """value('했어요'/'못 했어요')인 행동 문항을 [앞 순위 축, 축 없음, 뒤 순위 축] 세 묶음으로. 묶음 안은 체크 페이지 순서."""
    items = [i for i in _behavior_items(content) if answers.get(i["id"]) == value]
    axis_items = lambda high: [i["id"] for i in items if i.get("axis") in AXES and axes[i["axis"]]["high"] is high]
    return [axis_items(high_first), [i["id"] for i in items if i.get("axis") is None], axis_items(not high_first)]


def strengths(answers: dict[str, str], axes: dict[str, dict], content: dict) -> dict:
    """결과 2: 높음 축 실행 문항 중 '했어요' → 축 없음 문항 중 '했어요' → 낮음 축 실행 문항 중 '했어요', 최대 max_items 개.

    (2026-11-26 결정) 최대 2개 → 3개. 3개가 차도록 낮음 축의 '했어요'를 세 번째 순위로 보탠다.
    '했어요'가 3개보다 적으면 있는 만큼만 보여 준다.
    """
    first, second, third = _tiers(answers, axes, content, DONE, high_first=True)
    max_items = content["results"]["r2"].get("max_items", 3)
    any_done = any(answers.get(i["id"]) == DONE for i in _behavior_items(content))
    items = (first + second + third)[:max_items]
    # (2026-11-27) 세 번째 순위(낮음 축의 '했어요')는 화면이 '축은 낮음이지만 해낸 행동'으로 표시해 결과 1 과 어긋나 보이지 않게
    return {"summary": SUMMARY_KEY[(axes["care"]["high"], axes["challenge"]["high"])],
            "items": items, "low_axis": [i for i in items if i in set(third)],
            "empty": not any_done}


def _signals_yes(answers: dict[str, str], axes: dict[str, dict], content: dict) -> list[str]:
    """체크 5에서 '그랬어요'를 고른 부족 신호. 낮음 축의 신호를 먼저, 묶음 안은 체크 페이지 순서."""
    sig = [i for i in content["items"] if i["type"] == "signal" and answers.get(i["id"]) == YES]
    low = [i["id"] for i in sig if i.get("axis") in AXES and not axes[i["axis"]]["high"]]
    return low + [i["id"] for i in sig if i["id"] not in low]


def next_try(answers: dict[str, str], axes: dict[str, dict], content: dict, shown_strengths=()) -> dict:
    """결과 3 '다음에 해 볼 것' — 늘 max_items(3)개를 채운다 (2026-11-27 결정, 이전: 있는 만큼).

    순위: ① 낮음 축 '못 했어요' ② 축 없음 '못 했어요' ③ 높음 축 '못 했어요'
          ④ 체크 5에서 '그랬어요'를 고른 부족 신호(낮음 축 먼저)
          ⑤ 이어 가기: '했어요' 행동 중 결과 2에 나오지 않은 것(체크 페이지 순서)
    item(꼭 해야 하는 말의 기준, 4.7) = ①~④ 중 첫 항목. 없으면 None(→ 공통 문장).
    empty = ①~④가 하나도 없음(모두 해냈어요 → 이어 가기만), empty_signals = ①~③은 없고 ④가 있음.
    keep = 목록 중 ⑤에서 온 항목(화면이 '이어 가기'로 다르게 보여 준다).
    """
    first, second, third = _tiers(answers, axes, content, NOT_DONE, high_first=False)
    tries = first + second + third
    signals = _signals_yes(answers, axes, content)
    shown = set(shown_strengths)
    keep = [i["id"] for i in _behavior_items(content) if answers.get(i["id"]) == DONE and i["id"] not in shown]
    max_items = content["results"]["r3"].get("max_items", 3)
    picks = (tries + signals + keep)[:max_items]
    lead = (tries + signals)[0] if tries + signals else None
    return {"item": lead, "items": picks, "keep": [i for i in picks if i in set(keep)],
            "empty": not (tries or signals), "empty_signals": not tries and bool(signals)}


def situations_by_priority(content: dict) -> list[str]:
    sits = [s for s in content["member"]["situations"] if s["id"] != SITUATION_NONE]
    return [s["id"] for s in sorted(sits, key=lambda s: s["priority"])]


def phrase(situations: list[str], r3_item: Optional[str], content: dict) -> Optional[str]:
    """꼭 해야 하는 말(SPEC 4.7): 고른 상황 중 우선순위 1위 문장 → 결과 3 첫 항목(행동·부족 신호)의 phrase
    → 둘 다 없으면(상황 없음 + 모두 해냈어요) 공통 문장 general_phrases.all_done (2026-11-27 결정)."""
    chosen = [s for s in situations_by_priority(content) if s in situations]
    if chosen:
        return f"situation:{chosen[0]}"
    if r3_item:
        return f"item:{r3_item}"
    if GENERAL_ALL_DONE in (content.get("general_phrases") or {}):
        return f"general:{GENERAL_ALL_DONE}"
    return None


def action_options(situations: list[str], recommended: Optional[str], content: dict, r3: Optional[dict] = None) -> list[dict]:
    """실천 화면 보기: 추천 문장 1개 + 고르지 않은 상황 중 우선순위 높은 문장 2개.

    (2026-11-27 결정) 상황을 6~7개 골라 남은 상황 문장이 모자라면 결과 3 항목(못 한 행동·부족 신호, 이어 가기 제외)의 말
    → 공통 문장 순으로 채워 3개를 맞춘다.
    """
    opts = []
    if recommended:
        opts.append({"type": "recommended", "key": recommended})
    used = {recommended}
    extra = []
    if r3 is not None:
        keep = set(r3.get("keep") or [])
        extra = [f"item:{i}" for i in r3.get("items") or [] if i not in keep]
        if GENERAL_ALL_DONE in (content.get("general_phrases") or {}):
            extra.append(f"general:{GENERAL_ALL_DONE}")
    candidates = [f"situation:{s}" for s in situations_by_priority(content) if s not in situations] + extra
    for key in candidates:
        if len(opts) >= 3:
            break
        if key in used:
            continue
        opts.append({"type": "other_preset", "key": key})
        used.add(key)
    return opts


def evaluate(answers: dict[str, str], situations: list[str], keywords: list[str], content: dict,
             threshold: float = 0.5) -> dict[str, Any]:
    axes = judge_axes(answers, content, threshold)
    quadrant = QUADRANT[(axes["care"]["high"], axes["challenge"]["high"])]
    r2 = strengths(answers, axes, content)
    r3 = next_try(answers, axes, content, r2["items"])
    recommended = phrase(situations, r3["item"], content)
    return {
        "judgment": {"quadrant": quadrant, "threshold": threshold, "axes": axes},
        "results": {
            "r1": {**recall(keywords, answers, content), "signal_low": signal_low(axes, threshold)},
            "r2": r2,
            "r3": r3,
            "phrase": recommended,
            "action_options": action_options(situations, recommended, content, r3),
        },
    }
