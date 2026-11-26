"""관리자 집계 (SPEC 8장, design/admin/*.dc.html 주석) — 순수 함수.

원칙
- 비율은 리더(참여자)별로 먼저 묶은 뒤 집계한다(여러 구성원을 점검한 리더도 1명). '점검 건 기준'인 것만 예외
- 리더명과 함께 돌려주는 개인 결과는 '점검별 4분면 경향'뿐. 체크 응답·직접 쓴 문장은 리더명과 연결하지 않는다
- 개인별 머문 시간은 어디에도 돌려주지 않는다(집계만)
- 머문 시간은 visit 별 마지막 screen_view 값 기준, 중앙값·상위 25%(평균은 쓰지 않음). 결과 정리(analyzing)는 고정 3초라 머문 시간에서만 뺀다
- 마감 시 미완료 점검: 답한 문항까지 문항별 실행률·부족 신호에 포함. 4분면·결과 지표는 완료한 점검만

입력은 db 객체가 아니라 dict 목록(load_* 에서 만든다). 그래서 테스트에서 바로 만들어 넣을 수 있다.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

import anonymize
import rules

KST = dt.timezone(dt.timedelta(hours=9))
QUADS = ("rc", "re", "oa", "mi")
TYPES = ("rc", "re", "oa", "mi", "custom")
SCREENS = ("notice", "leader_name", "intro", "review_1", "review_2", "review_3", "review_4", "review_5", "keyword",
           "member", "check_1", "check_2", "check_3", "check_4", "check_5", "analyzing", "result_1", "result_2",
           "result_3", "action", "done")
PRE_NAME = ("notice", "leader_name")                 # 리더명 전 화면 → 기기 기준
STEP1_SCREENS = ("notice", "leader_name", "intro", "review_1", "review_2", "review_3", "review_4", "review_5", "keyword")
STEP2_SCREENS = ("member", "check_1", "check_2", "check_3", "check_4", "check_5", "analyzing", "result_1",
                 "result_2", "result_3", "action")
# 완료 화면 3칸(done_button_click 의 button 값). 화면 왼쪽부터
DONE_BUTTONS = ("again", "pdf", "recommend")
BLOCKABLE = ("leader_name", "keyword", "member", "check_1", "check_2", "check_3", "check_4", "check_5", "action")
HOUR_BUCKETS = ((0, 9), (9, 12), (12, 14), (14, 18), (18, 21), (21, 24))
KEYWORD_NONE = "kw_none"


# --------------------------------------------------------------------------- #
# 작은 도우미
# --------------------------------------------------------------------------- #
def pct(n: float, d: float) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def mean(xs: Iterable[float]) -> Optional[float]:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def mean_pct(xs: Iterable[float]) -> Optional[float]:
    m = mean(xs)
    return round(100.0 * m, 1) if m is not None else None


def median(xs: Iterable[float]) -> Optional[float]:
    xs = list(xs)
    return statistics.median(xs) if xs else None


def p75(xs: Iterable[float]) -> Optional[float]:
    xs = sorted(xs)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    return statistics.quantiles(xs, n=4, method="inclusive")[2]


def secs(ms: Optional[float]) -> Optional[float]:
    return round(ms / 1000.0, 1) if ms is not None else None


def mins(ms: Optional[float]) -> Optional[float]:
    return round(ms / 60000.0, 1) if ms is not None else None


def aware(t: Any) -> Optional[dt.datetime]:
    if t is None:
        return None
    if isinstance(t, str):
        try:
            t = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def kst_date(t: Any) -> Optional[dt.date]:
    t = aware(t)
    return t.astimezone(KST).date() if t else None


# --------------------------------------------------------------------------- #
# 데이터 묶음
# --------------------------------------------------------------------------- #
class Data:
    """한 차수의 참여자·STEP 1·점검·이벤트. filters 로 리더명 유형·기간을 거른다."""

    def __init__(self, participants: list[dict], step1: list[dict], checks: list[dict], events: list[dict],
                 content: dict, settings: dict, leader_type: Optional[str] = None,
                 date_from: Optional[dt.date] = None, date_to: Optional[dt.date] = None):
        self.content = content
        self.settings = settings
        self.items = {i["id"]: i for i in content["items"]}
        self.behavior_ids = [i["id"] for i in content["items"] if i["type"] == "behavior"]
        self.signal_ids = [i["id"] for i in content["items"] if i["type"] == "signal"]

        def in_period(t: Any) -> bool:
            d = kst_date(t)
            if d is None:
                return True
            return (date_from is None or d >= date_from) and (date_to is None or d <= date_to)

        self.P = {p["id"]: p for p in participants
                  if (not leader_type or p["leader_type"] == leader_type) and in_period(p["created_at"])}
        self.step1 = {s["participant_id"]: s for s in step1 if s["participant_id"] in self.P}
        self.checks: dict[str, list[dict]] = defaultdict(list)
        for c in sorted(checks, key=lambda c: c["seq"]):
            if c["participant_id"] in self.P:
                self.checks[c["participant_id"]].append(c)
        self.done: dict[str, list[dict]] = {
            pid: sorted([c for c in cs if c["status"] == "done"], key=lambda c: aware(c["completed_at"]) or aware(c["created_at"]))
            for pid, cs in self.checks.items()}
        self.done = {pid: cs for pid, cs in self.done.items() if cs}

        # 이벤트: 선택된 참여자의 것 + (리더명 유형 필터가 없을 때) 기기만 있는 이벤트
        self.linked_devices: set[str] = set()
        for e in events:
            if e.get("participant_id") and e.get("device_id"):
                self.linked_devices.add(e["device_id"])
        sel_devices = {e["device_id"] for e in events if e.get("participant_id") in self.P and e.get("device_id")}
        self.events = []
        for e in events:
            pid = e.get("participant_id")
            if pid:
                if pid in self.P:
                    self.events.append(e)
            elif in_period(e["ts"]) and (not leader_type or e.get("device_id") in sel_devices):
                self.events.append(e)
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        for e in self.events:
            self.by_name[e["name"]].append(e)
        self.visits = self._visits()

    def _visits(self) -> list[dict]:
        """screen_view → visit 별 하나(final 우선, 그다음 나중 기록)."""
        best: dict[str, dict] = {}
        for e in self.by_name.get("screen_view", []):
            pr = e.get("props") or {}
            key = pr.get("visit") or f"ev{e['id']}"
            cur = best.get(key)
            rank = (1 if pr.get("final") else 0, e["id"])
            if cur is None or rank > cur["_rank"]:
                pid = e.get("participant_id") or (cur["pid"] if cur else None)
                best[key] = {"_rank": rank, "screen": pr.get("screen"), "pid": pid, "device": e.get("device_id"),
                             "seq": e.get("check_seq"), "visible_ms": pr.get("visible_ms") or 0,
                             "entered_at": aware(pr.get("entered_at")) or aware(e["ts"]), "session": e.get("session_id"),
                             "device_kind": e.get("device")}
            elif e.get("participant_id") and not cur["pid"]:
                cur["pid"] = e["participant_id"]
        return [v for v in best.values() if v["screen"] in SCREENS]

    # 자주 쓰는 묶음
    def leaders_with(self, name: str) -> set[str]:
        return {e["participant_id"] for e in self.by_name.get(name, []) if e.get("participant_id")}

    def visits_of(self, pid: str) -> list[dict]:
        devices = self.devices_of(pid)
        return [v for v in self.visits if v["pid"] == pid or (v["pid"] is None and v["device"] in devices and v["screen"] in PRE_NAME)]

    def devices_of(self, pid: str) -> set[str]:
        return {e["device_id"] for e in self.events if e.get("participant_id") == pid and e.get("device_id")}

    def reached(self, screen: str) -> set[str]:
        return {v["pid"] for v in self.visits if v["screen"] == screen and v["pid"]}

    def answered(self, pid: str) -> list[dict]:
        """그 리더의 모든 점검 응답(진행 중 포함)."""
        return [c.get("answers") or {} for c in self.checks.get(pid, [])]


# --------------------------------------------------------------------------- #
# 소요 시간(보이는 동안만 더한 시간)
# --------------------------------------------------------------------------- #
def active_ms(D: Data, pid: str, screens: Iterable[str], seq: Optional[int] = None, until: Any = None) -> Optional[int]:
    screens = set(screens)
    until = aware(until)
    total, found = 0, False
    for v in D.visits_of(pid):
        if v["screen"] not in screens:
            continue
        if seq is not None and v["screen"] in STEP2_SCREENS and v["seq"] != seq:
            continue
        if until and v["entered_at"] and v["entered_at"] > until:
            continue
        total += v["visible_ms"]
        found = True
    return total if found else None


def time_to_first_done(D: Data, pid: str) -> Optional[int]:
    if pid not in D.done:
        return None
    first = D.done[pid][0]
    a = active_ms(D, pid, STEP1_SCREENS, until=first["completed_at"]) or 0
    b = active_ms(D, pid, STEP2_SCREENS, seq=first["seq"]) or 0
    return a + b if (a or b) else None


# --------------------------------------------------------------------------- #
# 리더별 묶음 지표
# --------------------------------------------------------------------------- #
def item_rate(D: Data, pid: str, item: str, positive: str) -> Optional[float]:
    vals = [a[item] for a in D.answered(pid) if item in a]
    return sum(1 for v in vals if v == positive) / len(vals) if vals else None


def leader_item_rates(D: Data, items: list[str], positive: str) -> dict[str, dict]:
    out = {}
    for i in items:
        per = [r for r in (item_rate(D, pid, i, positive) for pid in D.P) if r is not None]
        out[i] = {"rate": mean_pct(per), "leaders": len(per)}
    return out


def leader_behavior_rate(D: Data, pid: str, items: Optional[list[str]] = None) -> Optional[float]:
    items = items or D.behavior_ids
    vals = [a[i] for a in D.answered(pid) for i in items if i in a]
    return sum(1 for v in vals if v == "done") / len(vals) if vals else None


def axis_high_share(D: Data, pid: str, axis: str) -> Optional[float]:
    cs = D.done.get(pid, [])
    vals = [((c.get("judgment") or {}).get("axes") or {}).get(axis, {}).get("high") for c in cs]
    vals = [v for v in vals if v is not None]
    return sum(1 for v in vals if v) / len(vals) if vals else None


def mode_quadrant(cs: list[dict]) -> Optional[str]:
    qs = [(c.get("judgment") or {}).get("quadrant") for c in cs]
    qs = [q for q in qs if q]
    if not qs:
        return None
    counts = Counter(qs)
    top = max(counts.values())
    for q in reversed(qs):          # 동점이면 가장 최근 점검의 칸
        if counts[q] == top:
            return q
    return None


def sent_result(D: Data) -> set[str]:
    """결과 PDF 를 받은 리더(2026-11-26 이전의 '나에게 결과 보내기' 공유 완료도 포함)."""
    return D.leaders_with("result_shared") | D.leaders_with("result_pdf_saved")


# --------------------------------------------------------------------------- #
# 대시보드
# --------------------------------------------------------------------------- #
def dashboard(D: Data) -> dict:
    target = int(D.settings.get("target_count") or 0)
    regs = list(D.P)
    s1_done = [pid for pid, s in D.step1.items() if s.get("completed_at")]
    done_leaders = list(D.done)

    # 추이(날짜별 누적)
    reg_dates = Counter(kst_date(D.P[p]["created_at"]) for p in regs)
    done_dates = Counter(kst_date(D.done[p][0]["completed_at"]) for p in done_leaders)
    days = sorted(d for d in set(reg_dates) | set(done_dates) if d)
    trend, a, b = [], 0, 0
    if days:
        d = days[0]
        while d <= days[-1]:
            a += reg_dates.get(d, 0)
            b += done_dates.get(d, 0)
            trend.append({"date": d.isoformat(), "registered": a, "done": b})
            d += dt.timedelta(days=1)

    devices_notice = {v["device"] for v in D.visits if v["screen"] == "notice" and v["device"]}
    devices_name = {v["device"] for v in D.visits if v["screen"] == "leader_name" and v["device"]}
    any_full = {pid for pid, cs in D.checks.items() if any(len(c.get("answers") or {}) >= len(D.items) for c in cs)}
    stages = [
        ("notice", len(devices_notice)), ("leader_name", len(devices_name)), ("intro", len(D.reached("intro"))),
        ("keyword", len(D.reached("keyword"))), ("member", len(D.reached("member"))), ("checks", len(any_full)),
        ("result", len(D.reached("result_1"))), ("action", len(D.reached("action"))), ("done", len(done_leaders)),
    ]

    started_step2 = [pid for pid, cs in D.checks.items() if cs]
    dist = Counter()
    for pid in started_step2:
        n = len(D.done.get(pid, []))
        dist["0" if n == 0 else ("4+" if n >= 4 else str(n))] += 1
    types = Counter(D.P[p]["leader_type"] for p in regs)

    participation = {
        "target": target, "registered": len(regs), "registered_pct": pct(len(regs), target),
        "step1_done": len(s1_done), "step1_done_pct": pct(len(s1_done), target),
        "check_done": len(done_leaders), "check_done_pct": pct(len(done_leaders), target),
        "members_median": median(len(D.done[p]) for p in done_leaders),
        "duration_median_min": mins(median(t for t in (time_to_first_done(D, p) for p in done_leaders) if t)),
        "trend": trend,
        "stages": [{"key": k, "count": n} for k, n in stages],
        "leader_types": [{"type": t, "count": types.get(t, 0)} for t in TYPES],
        "members_dist": [{"bucket": k, "count": dist.get(k, 0)} for k in ("1", "2", "3", "4+", "0")],
        "type_completion": [{"type": t, "pct": pct(sum(1 for p in done_leaders if D.P[p]["leader_type"] == t), types.get(t, 0))} for t in TYPES],
        "estimated": True,
        # 등록이 대상 인원을 넘음(2026-11-27): 다른 기기로 다시 시작한 중복이나 추천 링크로 대상 밖 유입 가능성
        "over_target": bool(target) and len(regs) > target,
    }
    return {"participation": participation, "recall": recall(D), "application": application(D),
            "recall_practice": recall_practice(D), "transfer": transfer(D)}


def recall(D: Data) -> dict:
    reviews = [r["id"] for r in D.content["reviews"]]
    rows = [s for s in D.step1.values() if s.get("completed_at")]
    n = len(rows)
    rem = [sum(1 for r in reviews if (s.get("reviews") or {}).get(r) == "remembered") for s in rows]
    kw_items = [k["id"] for k in D.content["keywords"]["items"]]
    some = [s for s in rows if (s.get("keywords") or []) and s["keywords"] != [KEYWORD_NONE]]

    dwell = []
    for i, r in enumerate(reviews, 1):
        groups = {"remembered": [], "not_remembered": []}
        for s in rows:
            val = (s.get("reviews") or {}).get(r)
            ms = active_ms(D, s["participant_id"], [f"review_{i}"])
            if val in groups and ms is not None:
                groups[val].append(ms)
        a, b = median(groups["remembered"]), median(groups["not_remembered"])
        dwell.append({"review": r, "remembered_s": secs(a), "not_remembered_s": secs(b),
                      "diff_s": secs(b - a) if a is not None and b is not None else None,
                      "leaders": len(groups["remembered"]) + len(groups["not_remembered"])})
    return {
        "leaders": n,
        "avg_remember_pct": mean_pct(x / len(reviews) for x in rem),
        "all_remember_pct": pct(sum(1 for x in rem if x == len(reviews)), n),
        "no_keyword_pct": pct(sum(1 for s in rows if s.get("keywords") == [KEYWORD_NONE]), n),
        "by_review": [{"review": r, "pct": pct(sum(1 for s in rows if (s.get("reviews") or {}).get(r) == "remembered"), n)} for r in reviews],
        "by_keyword": [{"keyword": k, "pct": pct(sum(1 for s in rows if k in (s.get("keywords") or [])), n)} for k in kw_items],
        "review_dwell": dwell,
        "keyword_count": [{"count": c, "pct": pct(sum(1 for s in some if len(s["keywords"]) == c), len(some))} for c in (1, 2, 3)],
        "first_keyword": [{"keyword": k, "pct": pct(sum(1 for s in some if s["keywords"][0] == k), len(some))} for k in kw_items],
    }


def application(D: Data) -> dict:
    done_leaders = list(D.done)
    rates = leader_item_rates(D, D.behavior_ids, "done")
    ranked = sorted([(i, r["rate"]) for i, r in rates.items() if r["rate"] is not None], key=lambda x: (-x[1], D.behavior_ids.index(x[0])))
    modes = Counter(m for m in (mode_quadrant(D.done[p]) for p in done_leaders) if m)
    cross = []
    for t in TYPES:
        cs = [c for p in done_leaders if D.P[p]["leader_type"] == t for c in D.done[p]]
        qs = Counter((c.get("judgment") or {}).get("quadrant") for c in cs)
        cross.append({"type": t, "checks": len(cs), "cells": {q: pct(qs.get(q, 0), len(cs)) for q in QUADS}})
    return {
        "leaders": len(done_leaders),
        "behavior_rate_pct": mean_pct(r for r in (leader_behavior_rate(D, p) for p in D.P) if r is not None),
        "care_high_pct": mean_pct(x for x in (axis_high_share(D, p, "care") for p in done_leaders) if x is not None),
        "challenge_high_pct": mean_pct(x for x in (axis_high_share(D, p, "challenge") for p in done_leaders) if x is not None),
        "sent_pct": pct(len(sent_result(D) & set(done_leaders)), len(done_leaders)),
        "top_items": [{"item": i, "pct": r} for i, r in ranked[:3]],
        "bottom_items": [{"item": i, "pct": r} for i, r in list(reversed(ranked))[:3]],
        "quadrants": [{"quadrant": q, "pct": pct(modes.get(q, 0), sum(modes.values()))} for q in QUADS],
        # 한 칸 쏠림(1.14 경고): 리더별 가장 많이 나온 칸 기준
        "quadrant_skew": quadrant_skew(modes),
        "cross": cross,
    }


PREVIEW_THRESHOLDS = (0.25, 0.5, 0.75, 1.0)
SKEW_PCT = 70.0   # 한 칸이 이 비율 이상이면 '쏠림'(1.14 경고, 설정의 기준값 미리보기)


def quadrant_skew(modes: Counter) -> Optional[dict]:
    n = sum(modes.values())
    if not n:
        return None
    top = max(QUADS, key=lambda q: modes.get(q, 0))
    share = pct(modes[top], n)
    return {"quadrant": top, "pct": share, "skewed": share >= SKEW_PCT}


def threshold_preview(D: Data) -> dict:
    """판정 기준값별 4분면 분포 미리보기(2026-11-27). 완료한 점검의 저장된 응답을 각 기준값으로 다시 판정하고,
    1.14 와 같이 리더별 가장 많이 나온 칸으로 묶는다. 저장된 결과는 바꾸지 않는다."""
    current = float(D.settings.get("high_threshold") or 0.5)
    leaders = list(D.done)
    rows = []
    for t in sorted(set(PREVIEW_THRESHOLDS) | {current}):
        modes = Counter()
        for p in leaders:
            cs = [{"judgment": {"quadrant": rules.QUADRANT[(ax["care"]["high"], ax["challenge"]["high"])]}}
                  for ax in (rules.judge_axes(c.get("answers") or {}, D.content, t) for c in D.done[p])]
            m = mode_quadrant(cs)
            if m:
                modes[m] += 1
        n = sum(modes.values())
        skew = quadrant_skew(modes)
        rows.append({"threshold": t, "current": t == current, "leaders": n,
                     "cells": {q: pct(modes.get(q, 0), n) for q in QUADS},
                     "top": skew and skew["quadrant"], "top_pct": skew and skew["pct"], "skewed": bool(skew and skew["skewed"])})
    return {"current": current, "leaders": len(leaders), "skew_pct": SKEW_PCT, "rows": rows}


MIN_GROUP = 5   # 기억과 실천: 대상이 이보다 적으면 비율을 보여 주지 않는다(표본 적음)


def practiced_in_check(c: dict, practice: dict, kw_by_id: dict) -> Optional[bool]:
    """핵심 메시지 하나를 이 점검에서 실천했는가(content.reviews[].practice).

    keywords: 그 메시지 키워드의 절반 이상이 결과 1 기준 '했어요'(rules.keyword_result, min_done 포함)
    quadrant: 점검 경향(4분면)이 그 칸
    """
    if "quadrant" in practice:
        q = (c.get("judgment") or {}).get("quadrant")
        return None if q is None else q == practice["quadrant"]
    kws = [kw_by_id[k] for k in practice.get("keywords", []) if k in kw_by_id]
    if not kws:
        return None
    answers = c.get("answers") or {}
    did = sum(1 for k in kws if rules.keyword_result(k, answers))
    return did * 2 >= len(kws)


def recall_practice(D: Data) -> list[dict]:
    """2→3 기억한 리더와 실천한 리더(2026-11-26 결정): 핵심 메시지마다 기억난 리더 비율과 실천한 리더 비율.

    리더 기준. 대상 = STEP 1 을 끝내고 점검을 1건 이상 완료한 리더.
    기억 = STEP 1 '기억나요'. 실천 = 완료한 점검의 절반 이상에서 practiced_in_check 가 참.
    함께 주는 값: 둘 다 한 리더, 기억했지만 실천하지 못한 리더(다시 알려 줄 대상), 기억난 리더 중 실천 비율.
    대상이 MIN_GROUP 명 미만이면 비율은 None(인원은 그대로).
    """
    kw_by_id = {k["id"]: k for k in D.content["keywords"]["items"]}
    rows = [s for s in D.step1.values() if s.get("completed_at") and D.done.get(s["participant_id"])]
    out = []
    for r in D.content["reviews"]:
        practice = r.get("practice") or {}
        total = remembered = practiced = both = 0
        for s in rows:
            recall_value = (s.get("reviews") or {}).get(r["id"])
            if recall_value not in ("remembered", "not_remembered"):
                continue
            vals = [v for v in (practiced_in_check(c, practice, kw_by_id) for c in D.done[s["participant_id"]]) if v is not None]
            if not vals:
                continue
            rem, did = recall_value == "remembered", sum(vals) * 2 >= len(vals)
            total += 1
            remembered += rem
            practiced += did
            both += rem and did
        ok = total >= MIN_GROUP
        out.append({"review": r["id"], "basis": "quadrant" if "quadrant" in practice else "keywords", "leaders": total,
                    "remembered": remembered, "remembered_pct": pct(remembered, total) if ok else None,
                    "practiced": practiced, "practiced_pct": pct(practiced, total) if ok else None,
                    "both": both, "remembered_not_practiced": remembered - both,
                    "practiced_of_remembered_pct": pct(both, remembered) if remembered >= MIN_GROUP else None,
                    "small": not ok})
    return out


def transfer(D: Data) -> list[dict]:
    """보조 지표: 키워드를 고른 리더 / 고르지 않은 리더의 연결 행동 실행률.
    고르지 않음에는 기억했지만 다른 키워드를 고른 리더도 들어 있다(최대 3개 선택) → 기억 비교는 recall_practice."""
    rows = [s for s in D.step1.values() if s.get("completed_at") and s.get("keywords") and s["keywords"] != [KEYWORD_NONE]]
    out = []
    for k in D.content["keywords"]["items"]:
        a, b = [], []
        for s in rows:
            r = leader_behavior_rate(D, s["participant_id"], k["linked_items"])
            if r is None:
                continue
            (a if k["id"] in s["keywords"] else b).append(r)
        pa, pb = mean_pct(a), mean_pct(b)
        out.append({"keyword": k["id"], "picked_pct": pa, "not_picked_pct": pb, "picked_n": len(a), "not_picked_n": len(b),
                    "diff_pp": round(pa - pb, 1) if pa is not None and pb is not None else None})
    return out


# --------------------------------------------------------------------------- #
# 문항 분석
# --------------------------------------------------------------------------- #
def items_tab(D: Data) -> dict:
    behavior = leader_item_rates(D, D.behavior_ids, "done")
    signals = leader_item_rates(D, D.signal_ids, "yes")
    app = application(D)

    seen_result = D.reached("result_1")
    after = D.by_name.get("answer_changed_after_result", [])
    changers = defaultdict(set)
    for e in after:
        if e.get("participant_id"):
            changers[e["participant_id"]].add((e.get("props") or {}).get("item"))
    dirs = Counter()
    for e in after:
        pr = e.get("props") or {}
        dirs[(pr.get("before"), pr.get("after"))] += 1
    total_dir = sum(dirs.values())

    changed = defaultdict(set)   # 문항 → 같은 화면에서 답을 바꾼 리더
    for e in D.by_name.get("answer_changed", []):
        if e.get("participant_id"):
            changed[(e.get("props") or {}).get("item")].add(e["participant_id"])
    answered_by = {i: {p for p in D.P if any(i in a for a in D.answered(p))} for i in D.items}
    change_rank = sorted(([i, pct(len(changed[i]), len(answered_by[i]))] for i in D.items if answered_by[i] and changed.get(i)),
                         key=lambda x: -x[1])[:5]

    done_checks = [c for cs in D.done.values() for c in cs]
    nd = len(done_checks)
    ev_by_check = defaultdict(set)
    for e in D.events:
        if e.get("participant_id") and e.get("check_seq") is not None:
            ev_by_check[(e["participant_id"], e["check_seq"])].add(e["name"])
    has = lambda c, name: name in ev_by_check.get((c["participant_id"], c["seq"]), set())  # noqa: E731
    sits = [s["id"] for s in D.content["member"]["situations"]]
    n_sits = Counter()
    for c in done_checks:
        k = len([s for s in (c.get("situations") or []) if s != "none"])
        n_sits["3+" if k >= 3 else str(k)] += 1
    texts = sorted(c["action_text"] for c in done_checks if c.get("action_type") == "custom" and c.get("action_text"))
    return {
        "behavior": [{"item": i, "axis": D.items[i].get("axis"), **behavior[i]} for i in D.behavior_ids],
        "axis_high": {"care": app["care_high_pct"], "challenge": app["challenge_high_pct"]},
        "signals": [{"item": i, **signals[i]} for i in D.signal_ids],
        "quality": {
            "changed_after_result_pct": pct(len(changers), len(seen_result)),
            "changed_items_median": median(len(v) for v in changers.values()),
            "directions": {
                "not_done_to_done": pct(dirs[("not_done", "done")], total_dir),
                "done_to_not_done": pct(dirs[("done", "not_done")], total_dir),
                "yes_to_no": pct(dirs[("yes", "no")], total_dir),
                "no_to_yes": pct(dirs[("no", "yes")], total_dir),
            },
        },
        "answer_changed_top": [{"item": i, "pct": p} for i, p in change_rank],
        "actions": {
            "checks": nd,
            "recommended": pct(sum(1 for c in done_checks if c.get("action_type") == "recommended"), nd),
            "other_preset": pct(sum(1 for c in done_checks if c.get("action_type") == "other_preset"), nd),
            "custom": pct(sum(1 for c in done_checks if c.get("action_type") == "custom"), nd),
            "custom_abandoned": pct(sum(1 for c in done_checks if has(c, "action_custom_abandoned")), nd),
        },
        "after_done": {
            "pdf_saved": pct(sum(1 for c in done_checks if has(c, "result_pdf_saved") or has(c, "result_shared")), nd),
            "another": pct(sum(1 for c in done_checks if has(c, "another_member_started")), nd),
        },
        "situations": [{"situation": s, "pct": pct(sum(1 for c in done_checks if s in (c.get("situations") or [])), nd)} for s in sits],
        "situation_count": [{"bucket": k, "pct": pct(n_sits.get(k, 0), nd)} for k in ("0", "1", "2", "3+")],
        # 직접 쓴 문장: 작성자·시각·순서 정보 없이 글자순으로만
        "custom_texts": texts,
    }


# --------------------------------------------------------------------------- #
# 참여자
# --------------------------------------------------------------------------- #
STAGE_ORDER = {"step1": 0, "checking": 1, "done": 2}


def leader_rows(D: Data) -> list[dict]:
    sent = sent_result(D)
    last_screen: dict[str, tuple] = {}
    last_ts: dict[str, dt.datetime] = {}
    for v in D.visits:
        if v["pid"] and v["entered_at"] and (v["pid"] not in last_screen or v["entered_at"] > last_screen[v["pid"]][0]):
            last_screen[v["pid"]] = (v["entered_at"], v["screen"])
    for e in D.events:
        pid, ts = e.get("participant_id"), aware(e["ts"])
        if pid and ts and (pid not in last_ts or ts > last_ts[pid]):
            last_ts[pid] = ts
    # 직접 지은 리더명이 같은 참여자 수(2026-11-27): 다른 기기로 다시 시작한 중복일 수 있다(보기 4개는 여럿이 고르므로 제외)
    same_name = Counter(p["leader_name"] for p in D.P.values() if p["leader_type"] == "custom")
    name_rule = D.content["leader_name"]["custom"].get("name_like") or {}
    rows = []
    for pid, p in D.P.items():
        s1 = D.step1.get(pid)
        cs = D.checks.get(pid, [])
        done = D.done.get(pid, [])
        if not (s1 and s1.get("completed_at")):
            stage = "step1"
        elif any(c["status"] != "done" for c in cs) or not done:
            stage = "checking"
        else:
            stage = "done"
        seen = max([t for t in (aware(p.get("last_seen_at")), last_ts.get(pid)) if t], default=None)
        rows.append({
            "leader_name": p["leader_name"], "serial": p["serial"], "leader_type": p["leader_type"],
            "started": kst_date(p["created_at"]).isoformat() if p.get("created_at") else None,
            "stage": stage,
            "last_screen": last_screen.get(pid, (None, None))[1],
            "sent": None if not done else (pid in sent),
            "members": len(done),
            # 점검별 경향: 완료 순서대로 4분면 + 완료 일시만(별명·응답 없음)
            "trends": [{"quadrant": (c.get("judgment") or {}).get("quadrant"),
                        "completed_at": aware(c["completed_at"]).astimezone(KST).strftime("%Y-%m-%d %H:%M") if c.get("completed_at") else None}
                       for c in done],
            "last_access": seen.astimezone(KST).strftime("%Y-%m-%d %H:%M") if seen else None,
            "same_name": same_name[p["leader_name"]] if p["leader_type"] == "custom" and same_name[p["leader_name"]] > 1 else None,
            # 직접 지은 리더명이 사람 이름처럼 보임(2026-11-27) — 표시만, 판단은 관리자
            "name_suspect": p["leader_type"] == "custom" and anonymize.looks_like_person_name(p["leader_name"], name_rule),
            "_sort_seen": seen.timestamp() if seen else 0,
        })
    return rows


def leaders_tab(D: Data, stage: Optional[str] = None, q: Optional[str] = None, sort: str = "last_access",
                desc: bool = True, page: int = 1, size: int = 20) -> dict:
    rows = leader_rows(D)
    counts = Counter(r["stage"] for r in rows)
    if stage in STAGE_ORDER:
        rows = [r for r in rows if r["stage"] == stage]
    if q:
        ql = " ".join(q.split()).lower().lstrip("#")
        rows = [r for r in rows if ql in r["leader_name"].lower() or ql in r["serial"] or ql in f"{r['leader_name']} #{r['serial']}".lower()]
    keys = {
        "leader_name": lambda r: (r["leader_name"], r["serial"]),
        "started": lambda r: r["started"] or "",
        "stage": lambda r: STAGE_ORDER[r["stage"]],
        "last_access": lambda r: r["_sort_seen"],
    }
    rows.sort(key=keys.get(sort, keys["last_access"]), reverse=desc)
    size = size if size in (20, 50, 100) else 20
    total = len(rows)
    pages = max(1, -(-total // size))
    page = min(max(1, page), pages)
    sl = rows[(page - 1) * size: page * size]
    for r in sl:
        r.pop("_sort_seen", None)
    return {"counts": {"all": sum(counts.values()), "step1": counts.get("step1", 0), "checking": counts.get("checking", 0),
                       "done": counts.get("done", 0)},
            "total": total, "page": page, "pages": pages, "size": size, "rows": sl}


# --------------------------------------------------------------------------- #
# 사용 흐름
# --------------------------------------------------------------------------- #
def flow_tab(D: Data) -> dict:
    regs = list(D.P)
    nreg = len(regs)
    done_leaders = set(D.done)

    # 요약
    step1_t = [t for t in (active_ms(D, p, STEP1_SCREENS) for p, s in D.step1.items() if s.get("completed_at")) if t]
    first_t = [t for t in (active_ms(D, p, STEP2_SCREENS, seq=D.done[p][0]["seq"]) for p in done_leaders) if t]
    later_t = [t for p in done_leaders for c in D.done[p][1:] for t in [active_ms(D, p, STEP2_SCREENS, seq=c["seq"])] if t]
    backs = D.by_name.get("back_nav", [])

    # 화면별 도달·멈춤
    reach = {}
    for s in SCREENS:
        if s in PRE_NAME:
            reach[s] = len({v["device"] for v in D.visits if v["screen"] == s and v["device"]})
        else:
            reach[s] = len(D.reached(s))
    stops = Counter()
    for p in regs:
        if p in done_leaders:
            continue
        vs = [v for v in D.visits_of(p) if v["entered_at"]]
        if vs:
            stops[max(vs, key=lambda v: v["entered_at"])["screen"]] += 1
    device_only = defaultdict(list)
    for v in D.visits:
        if v["pid"] is None and v["device"] and v["device"] not in D.linked_devices and v["entered_at"]:
            device_only[v["device"]].append(v)
    for vs in device_only.values():
        stops[max(vs, key=lambda v: v["entered_at"])["screen"]] += 1

    # 화면별 머문 시간: 리더(또는 기기)마다 그 화면 방문의 중앙값 → 전체 중앙값·상위 25%
    per = defaultdict(lambda: defaultdict(list))
    for v in D.visits:
        who = v["pid"] or (f"d:{v['device']}" if v["device"] else None)
        if who:
            per[v["screen"]][who].append(v["visible_ms"])
    dwell = []
    for s in SCREENS:
        if s == "analyzing":
            dwell.append({"screen": s, "excluded": True})
            continue
        vals = [median(x) for x in per[s].values()]
        dwell.append({"screen": s, "median_s": secs(median(vals)), "p75_s": secs(p75(vals)), "leaders": len(vals)})

    back_from = Counter((e.get("props") or {}).get("from") for e in backs if e.get("participant_id"))

    # 기기별
    by_dev = defaultdict(list)
    for p in regs:
        by_dev[D.P[p].get("device") or "pc"].append(p)
    devices = {d: {"share": pct(len(ps), nreg), "completion": pct(sum(1 for p in ps if p in done_leaders), len(ps)),
                   "duration_median_min": mins(median(t for t in (time_to_first_done(D, p) for p in ps) if t))}
               for d, ps in ((k, by_dev.get(k, [])) for k in ("mobile", "pc"))}
    # 앱 안 브라우저로 연 기기(2026-11-27): 이벤트를 남긴 모든 기기 중 inapp_browser 를 한 번이라도 남긴 기기
    all_devices = {e["device_id"] for e in D.events if e.get("device_id")}
    inapp_by = defaultdict(set)
    for e in D.by_name.get("inapp_browser", []):
        if e.get("device_id"):
            inapp_by[(e.get("props") or {}).get("browser") or "other"].add(e["device_id"])
    inapp_devices = set().union(*inapp_by.values()) if inapp_by else set()
    devices["inapp"] = {"devices": len(inapp_devices), "pct": pct(len(inapp_devices), len(all_devices)),
                        "by_browser": [{"browser": b, "devices": len(ds)} for b, ds in sorted(inapp_by.items(), key=lambda x: -len(x[1]))]}

    # 완료 화면에서 한 행동(완료 화면에 온 리더 대비)
    at_done = D.reached("done")
    lw = {n: D.leaders_with(n) & at_done for n in ("result_shared", "result_pdf_saved", "recommend_shared",
                                                    "recommend_copied", "recommend_opened", "another_member_started")}
    # 완료 화면 3칸 클릭: 누를 때마다 1회(같은 리더가 여러 번 누르면 모두 셈, 받기·공유 성공 여부와 별개)
    clicks, clickers = Counter(), defaultdict(set)
    for e in D.by_name.get("done_button_click", []):
        b = (e.get("props") or {}).get("button")
        if b in DONE_BUTTONS:
            clicks[b] += 1
            if e.get("participant_id"):
                clickers[b].add(e["participant_id"])
    any_action = set().union(*lw.values(), *clickers.values())
    done_actions = {
        "result_pdf_saved": pct(len(lw["result_pdf_saved"] | lw["result_shared"]), len(at_done)),
        "recommend": pct(len(lw["recommend_shared"] | lw["recommend_copied"]), len(at_done)),
        "another": pct(len(lw["another_member_started"]), len(at_done)),
        "nothing": pct(len(at_done - any_action), len(at_done)),
        "recommend_visits": len({e.get("device_id") for e in D.by_name.get("visit_from_recommend", []) if e.get("device_id")}),
        "at_done": len(at_done),
        "clicks": [{"button": b, "clicks": clicks[b], "leaders": len(clickers[b]),
                    "leaders_pct": pct(len(clickers[b] & at_done), len(at_done))} for b in DONE_BUTTONS],
    }

    # 안내 화면과 입장(기기 기준)
    notice_dev = defaultdict(set)
    for v in D.visits:
        if v["screen"] == "notice" and v["device"]:
            notice_dev[v["device"]].add(v["session"])
    name_dev = {v["device"] for v in D.visits if v["screen"] == "leader_name" and v["device"]}
    nn = len(notice_dev)
    denied = Counter((e.get("props") or {}).get("reason") for e in D.by_name.get("entry_denied", []))
    entry = {
        "notice_devices": nn,
        "to_leader_name": pct(sum(1 for d in notice_dev if d in name_dev), nn),
        "left_at_notice": pct(sum(1 for d in notice_dev if d not in name_dev), nn),
        "returned_then_proceeded": pct(sum(1 for d, ss in notice_dev.items() if d in name_dev and len(ss) >= 2), nn),
        "denied_missing": denied.get("missing", 0), "denied_wrong": denied.get("wrong", 0),
    }

    seen_result = D.reached("result_1")
    reaction = {
        "back_to_checklist": pct(len(D.leaders_with("result_back_to_checklist") & seen_result), len(seen_result)),
        "revisit_after_done": pct(len(D.leaders_with("revisit_after_done") & seen_result), len(seen_result)),
    }

    # 요일·시간대(한국 시간, 화면을 연 리더 수)
    cells = defaultdict(set)
    for v in D.visits:
        if not v["entered_at"]:
            continue
        t = v["entered_at"].astimezone(KST)
        b = next(i for i, (lo, hi) in enumerate(HOUR_BUCKETS) if lo <= t.hour < hi)
        cells[(t.weekday(), b)].add(v["pid"] or f"d:{v['device']}")
    heat = [[len(cells.get((d, b), ())) for b in range(len(HOUR_BUCKETS))] for d in range(7)]

    nick = D.by_name.get("nickname_method", [])
    methods = Counter((e.get("props") or {}).get("method") for e in nick)
    regen = [(e.get("props") or {}).get("regenerate_count") or 0 for e in nick if (e.get("props") or {}).get("method") == "regenerated"]

    blocked = Counter((e.get("props") or {}).get("screen") for e in D.by_name.get("blocked_next_tap", []))
    errs = D.by_name.get("error", [])
    err_kinds = Counter((e.get("props") or {}).get("kind") for e in errs)
    err_table = Counter(((e.get("props") or {}).get("kind"), e.get("device"), (e.get("props") or {}).get("browser")) for e in errs)

    return {
        "summary": {
            "step1_min": mins(median(step1_t)), "first_check_min": mins(median(first_t)), "later_check_min": mins(median(later_t)),
            "resume_pct": pct(len(D.leaders_with("resume")), nreg),
            "back_per_leader": round(len([e for e in backs if e.get("participant_id")]) / nreg, 1) if nreg else None,
        },
        "screens": [{"screen": s, "reached": reach[s], "stopped": stops.get(s, 0)} for s in SCREENS],
        "dwell": dwell,
        "back_top": [{"screen": s, "per_leader": round(n / nreg, 2) if nreg else None} for s, n in back_from.most_common(5) if s],
        "devices": devices,
        "done_actions": done_actions,
        "entry": entry,
        "reaction": reaction,
        "heatmap": heat,
        "nickname": {"auto": pct(methods.get("auto", 0), len(nick)), "regenerated": pct(methods.get("regenerated", 0), len(nick)),
                     "manual": pct(methods.get("manual", 0), len(nick)), "regen_median": median(regen)},
        "blocked": [{"screen": s, "per_leader": round(blocked.get(s, 0) / nreg, 2) if nreg else None} for s in BLOCKABLE],
        "errors": {"counts": {k: err_kinds.get(k, 0) for k in ("pdf", "share", "storage_unavailable", "save_retry")},
                   "table": [{"kind": k, "device": d, "browser": b, "count": n} for (k, d, b), n in err_table.most_common()]},
    }
