"""응답자 선택 무작위 시뮬레이션 (API 수준). 배포 전 회귀 확인용.

사용법(프로젝트 폴더에서): .venv\\Scripts\\python scripts\\simulate_choices.py . 400 [seed]
- '== PROBLEMS ==' 아래가 '(none)' 이면 통과. 잘못된 입력(일부러 보내는 값)도 5xx 없이 4xx 로 막히는지 함께 본다
- 임시 SQLite DB 를 쓴다(.local.db 를 건드리지 않음)
- 실제 화면이 보내는 요청 순서를 흉내 내되, 선택은 무작위(뒤로 가기, 이어 하기, 처음부터 다시, 결과 후 답 바꾸기 등)
- 확인: 5xx/예외 없음, 기대 상태 코드, 저장된 결과 == rules.evaluate, /api/me 이어 하기 상태 일치, 관리자 집계가 깨지지 않음
"""
import collections
import json
import os
import random
import sys
import tempfile
import traceback
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):   # Windows 콘솔·파일 출력에서도 한글·기호가 깨지거나 출력이 멈추지 않게
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
ROOT = Path(sys.argv[1]).resolve()
N = int(sys.argv[2]) if len(sys.argv) > 2 else 300
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 1
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["ENTRY_KEY"] = "sim-entry-key"
os.environ.pop("VERCEL", None)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)

import admin_auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
import rules  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "sim.db"
db.reset_engine(f"sqlite:///{tmp.as_posix()}")
C = TestClient(main.app, raise_server_exceptions=True)
CONTENT = main.load_content()
ITEMS = CONTENT["items"]
ITEM_IDS = [i["id"] for i in ITEMS]
PAGES = collections.OrderedDict()
for i in ITEMS:
    PAGES.setdefault(i.get("page") or i["id"][:2], []).append(i)
REVIEWS = [r["id"] for r in CONTENT["reviews"]]
KWS = [k["id"] for k in CONTENT["keywords"]["items"]]
SITS = [s["id"] for s in CONTENT["member"]["situations"] if s["id"] != "none"]
rnd = random.Random(SEED)

problems = collections.Counter()
examples = {}
stats = collections.Counter()


def problem(kind, detail):
    problems[kind] += 1
    examples.setdefault(kind, detail)


def call(method, url, pid=None, key="sim-entry-key", expect=None, headers_extra=None, **kw):
    h = {"X-Entry-Key": key} if key else {}
    if "content" in kw:
        h["Content-Type"] = "application/json"
    if pid:
        h["X-Participant-Id"] = pid
    try:
        r = C.request(method, url, headers=h, **kw)
    except Exception as e:  # 서버 예외(500)
        tb = traceback.format_exc().strip().splitlines()
        problem(f"EXCEPTION {method} {url.split('?')[0]} {type(e).__name__}",
                {"payload": kw.get("json"), "tb": tb[-6:]})
        return None
    if r.status_code >= 500:
        problem(f"5xx {method} {url}", {"status": r.status_code, "payload": kw.get("json"), "body": r.text[:300]})
    if expect is not None:
        ok = r.status_code in (expect if isinstance(expect, tuple) else (expect,))
        if not ok:
            problem(f"unexpected {r.status_code} {method} {url.split('?')[0]} (expected {expect})",
                    {"payload": kw.get("json"), "body": r.text[:300]})
    stats[f"{method} {url.split('/api/')[1].split('?')[0].split('/')[0]} {r.status_code}"] += 1
    return r


# --------------------------------------------------------------------------- #
# 리더명 입력 후보(직접 적기). 기대: 201 또는 422
# --------------------------------------------------------------------------- #
CUSTOM_NAMES = [
    "묻고 듣는", "열린 리더님", "리더", "팀 리더", "가", "가나", "가" * 15, "가" * 16, "   공백   많은   ",
    "😀😀", "😀", "<b>굵게</b>", "a&b<c>", "사번 123456", "12345 리더", "１２３４５６", "010-1234-5678",
    "a@b.co", "탭\t줄\n바꿈", "zero​width", "‮뒤집기", "é́́", "x" * 100, "x" * 101,
    "", " ", "　　", "○○", "{name}", "$&$1", "null", "undefined", "리더 리더",
]


def make_participant():
    if rnd.random() < 0.35:
        name = rnd.choice(CUSTOM_NAMES)
        order = rnd.sample(["oa", "rc", "mi", "re"], 4)
        r = call("POST", "/api/participants", json={"leader_type": "custom", "custom_name": name, "option_order": order},
                 expect=(201, 422))
        if r is None or r.status_code != 201:
            if r is not None and r.status_code == 422:
                code = (r.json() or {}).get("error") or "pydantic"
                stats[f"custom name rejected: {code}"] += 1
                if code == "pydantic":
                    stats["custom name rejected by FastAPI validation (no error code)"] += 1
            name_t = rnd.choice(["rc", "re", "oa", "mi"])
            r = call("POST", "/api/participants", json={"leader_type": name_t, "option_order": order}, expect=201)
        else:
            stored = r.json()["leader_name"]
            if stored.endswith("리더님 리더") or stored.endswith("리더님님"):
                problem("custom leader name gets awkward suffix", {"typed": name, "stored": stored})
            if "<" in stored:
                stats["leader name with HTML stored (rendered as text?)"] += 1
    else:
        r = call("POST", "/api/participants", json={"leader_type": rnd.choice(["rc", "re", "oa", "mi"]),
                                                    "option_order": rnd.sample(["oa", "rc", "mi", "re"], 4)}, expect=201)
    return r.json()["participant_id"] if r is not None and r.status_code == 201 else None


def check_me(pid, model, where):
    r = call("GET", "/api/me", pid=pid, expect=200)
    if r is None or r.status_code != 200:
        return
    me = r.json()
    s1 = me["step1"]
    if model["step1_done"] != bool(s1 and s1["done"]):
        problem("resume: step1 done mismatch", {"where": where, "me": s1})
    if s1 and model.get("step1_last") and s1["last_screen"] != model["step1_last"] and not s1["done"]:
        problem("resume: step1 last_screen mismatch", {"where": where, "me": s1["last_screen"], "model": model["step1_last"]})
    cur = me["check_in_progress"]
    if model.get("seq") is None:
        if cur is not None:
            problem("resume: phantom check in progress", {"where": where, "me": cur})
    else:
        if cur is None or cur["seq"] != model["seq"]:
            problem("resume: check in progress missing", {"where": where, "me": cur, "model_seq": model["seq"]})
        else:
            if cur["answers"] != model["answers"]:
                problem("resume: answers mismatch", {"where": where})
            if cur["situations"] != model["situations"]:
                problem("resume: situations mismatch", {"where": where, "me": cur["situations"], "model": model["situations"]})
            if model.get("last") and cur["last_screen"] != model["last"]:
                problem("resume: last_screen mismatch", {"where": where, "me": cur["last_screen"], "model": model["last"]})
    if me["checks_done"] != model["done"]:
        problem("resume: checks_done mismatch", {"where": where, "me": me["checks_done"], "model": model["done"]})


def pick_keywords():
    if rnd.random() < 0.15:
        return ["kw_none"]
    return rnd.sample(KWS, rnd.randint(1, 3))


def pick_situations():
    if rnd.random() < 0.2:
        return ["none"]
    return rnd.sample(SITS, rnd.randint(1, len(SITS) if rnd.random() < 0.1 else 3))


def answer_value(item, bias):
    if item["type"] == "behavior":
        return "done" if rnd.random() < bias else "not_done"
    return "yes" if rnd.random() < (1 - bias) * 0.6 else "no"


CUSTOM_TEXTS = [
    "다음 주에 다시 이야기해요", "○○님, 고마워요", "", "   ", "a" * 100, "가" * 100, "a" * 101,
    "010-1234-5678", "123456", "연락은 a@b.co 로", "<script>alert(1)</script>", "😀" * 50, "😀" * 60,
    "줄\n바꿈\n여러\n번", "$&$1 {name} {nickname}", "\u0000널문자", "2026년 12월 31일까지 1234567원",
]


def run_check(pid, model, keywords):
    sits = pick_situations()
    r = call("POST", "/api/checks", pid=pid, json={"situations": sits}, expect=201)
    if r is None or r.status_code != 201:
        return "fail"
    seq = r.json()["seq"]
    model.update(seq=seq, answers={}, situations=sits, last="member")
    # 구성원 화면으로 돌아가 상황을 바꾸는 경우
    if rnd.random() < 0.15:
        sits = pick_situations()
        call("PATCH", f"/api/checks/{seq}", pid=pid, json={"situations": sits, "last_screen": "member"}, expect=200)
        model["situations"] = sits
    bias = rnd.choice([0.0, 0.2, 0.5, 0.8, 1.0])
    pages = list(PAGES.values())
    p = 0
    while p < len(pages):
        page_answers = {i["id"]: answer_value(i, bias) for i in pages[p]}
        # 한 페이지 일부만 보내는 경우(화면은 다 답해야 '다음'이지만 탭마다 저장한다면)
        call("PATCH", f"/api/checks/{seq}", pid=pid, json={"answers": page_answers, "last_screen": f"check_{p + 1}"}, expect=200)
        model["answers"].update(page_answers)
        model["last"] = f"check_{p + 1}"
        roll = rnd.random()
        if roll < 0.05:
            check_me(pid, model, f"check_{p + 1}")
        if roll < 0.04:  # 처음부터 다시
            call("DELETE", f"/api/checks/{seq}", pid=pid, expect=200)
            model.update(seq=None, answers={}, situations=None, last=None)
            stats["restart"] += 1
            check_me(pid, model, "after restart")
            return "restarted"
        if roll > 0.93 and p > 0:  # 뒤로 가기
            p -= 1
            continue
        p += 1
    # 다 답하기 전에 결과를 부르면 422 이어야 함
    r = call("GET", f"/api/checks/{seq}/result", pid=pid, expect=200)
    if r is None or r.status_code != 200:
        return "fail"
    res = r.json()["results"]
    expect_out = rules.evaluate(model["answers"], model["situations"], keywords, CONTENT, 0.5)
    if res != expect_out["results"]:
        problem("result != rules.evaluate", {"api": res, "expected": expect_out["results"]})
    # 결과를 본 뒤 체크로 돌아가 답을 바꾸는 경우
    chosen = None
    if rnd.random() < 0.5:
        opts = res["action_options"]
        chosen = rnd.choice(opts) if opts else None
    if rnd.random() < 0.3:
        item = rnd.choice(ITEMS)
        newv = {"done": "not_done", "not_done": "done", "yes": "no", "no": "yes"}[model["answers"][item["id"]]]
        call("PATCH", f"/api/checks/{seq}", pid=pid, json={"answers": {item["id"]: newv}, "last_screen": "check_5"}, expect=200)
        model["answers"][item["id"]] = newv
        stats["answer changed after result"] += 1
        r = call("GET", f"/api/checks/{seq}/result", pid=pid, expect=200)
        res2 = r.json()["results"]
        if chosen and chosen not in res2["action_options"]:
            stats["earlier action choice disappears after answer change"] += 1
        res = res2
    # 실천 고르기
    opts = res["action_options"]
    if not opts:
        problem("no action options", {"situations": model["situations"], "answers": model["answers"]})
    roll = rnd.random()
    if roll < 0.25 or not opts:
        text = rnd.choice(CUSTOM_TEXTS)
        body = {"action_type": "custom", "action_text": text, "last_screen": "action", "complete": True}
        exp = (200, 422)
    else:
        o = rnd.choice(opts)
        body = {"action_type": o["type"], "action_key": o["key"], "last_screen": "action", "complete": True}
        exp = 200
    r = call("PATCH", f"/api/checks/{seq}", pid=pid, json=body, expect=exp)
    if r is not None and r.status_code == 422:
        err = r.json().get("error") or "pydantic"
        stats[f"custom text rejected: {err} ({len(body.get('action_text') or '')} chars)"] += 1
        # 화면은 다시 고르게 할 것 → 추천으로 완료
        o = opts[0]
        r = call("PATCH", f"/api/checks/{seq}", pid=pid,
                 json={"action_type": o["type"], "action_key": o["key"], "last_screen": "action", "complete": True}, expect=200)
    if r is None or r.status_code != 200:
        return "fail"
    out = r.json()
    if out["status"] != "done":
        problem("complete did not finish", out)
    if body.get("action_type") == "custom" and out.get("action_text") and len(out["action_text"]) > 200:
        problem("stored custom text > 200", {"len": len(out["action_text"])})
    # 완료 뒤 수정·삭제는 409
    call("PATCH", f"/api/checks/{seq}", pid=pid, json={"answers": {ITEM_IDS[0]: "done"}}, expect=409)
    call("DELETE", f"/api/checks/{seq}", pid=pid, expect=409)
    r = call("GET", f"/api/checks/{seq}/result", pid=pid, expect=200)
    model.update(seq=None, answers={}, situations=None, last=None)
    model["done"] += 1
    return "done"


def run_leader():
    pid = make_participant()
    if not pid:
        return
    model = {"step1_done": False, "step1_last": None, "seq": None, "done": 0}
    # 리더명 다시 고르기(STEP 1 전)
    if rnd.random() < 0.2:
        call("PUT", "/api/participants/me", pid=pid, json={"leader_type": rnd.choice(["rc", "re", "oa", "mi"]),
                                                            "option_order": []}, expect=200)
    # STEP 1: 페이지마다 저장, 가끔 뒤로 가서 바꾸기, 가끔 중도 이탈
    for n, rid in enumerate(REVIEWS, 1):
        call("PUT", "/api/step1", pid=pid, json={"reviews": {rid: rnd.choice(["remembered", "not_remembered"])},
                                                  "last_screen": f"review_{n}"}, expect=200)
        model["step1_last"] = f"review_{n}"
        if rnd.random() < 0.03:
            check_me(pid, model, f"review_{n}")
            stats["dropout during step1"] += 1
            return
    kws = pick_keywords()
    if rnd.random() < 0.1:  # 키워드 화면에서 '다음' 전 저장 없이 이탈 → 다시 와서 완료
        check_me(pid, model, "keyword")
    r = call("PUT", "/api/step1", pid=pid, json={"keywords": kws, "last_screen": "keyword", "complete": True}, expect=200)
    if r is None or r.status_code != 200:
        return
    model["step1_done"] = True
    # STEP 1 뒤 리더명 변경은 409
    call("PUT", "/api/participants/me", pid=pid, json={"leader_type": "rc", "option_order": []}, expect=409)
    call("PUT", "/api/step1", pid=pid, json={"keywords": ["kw_ait"]}, expect=409)
    members = rnd.choice([0, 1, 1, 1, 2, 3])
    guard = 0
    while model["done"] < members and guard < 8:
        guard += 1
        outcome = run_check(pid, model, kws)
        if outcome == "fail":
            return
        if rnd.random() < 0.03:
            check_me(pid, model, f"after check {outcome}")
    # 진행 중 점검이 있을 때 새 점검을 만들면 409
    if model["seq"] is None and rnd.random() < 0.2:
        r = call("POST", "/api/checks", pid=pid, json={"situations": ["none"]}, expect=201)
        if r is not None and r.status_code == 201:
            model.update(seq=r.json()["seq"], answers={}, situations=["none"], last="member")
            call("POST", "/api/checks", pid=pid, json={"situations": ["low"]}, expect=409)
            stats["left a check in progress"] += 1
    check_me(pid, model, "end")


# --------------------------------------------------------------------------- #
# 잘못된/거친 입력: 서버가 5xx 없이 4xx 로 막는지
# --------------------------------------------------------------------------- #
def hostile(pid):
    bad_bodies = [
        ("PUT", "/api/step1", {"reviews": {"r9": "remembered"}}),
        ("PUT", "/api/step1", {"reviews": {REVIEWS[0]: "maybe"}}),
        ("PUT", "/api/step1", {"keywords": ["kw_none", "kw_ait"]}),
        ("PUT", "/api/step1", {"keywords": []}),
        ("PUT", "/api/step1", {"keywords": ["kw_ait", "kw_ait"]}),
        ("PUT", "/api/step1", {"keywords": KWS[:4]}),
        ("PUT", "/api/step1", {"last_screen": "done"}),
        ("PUT", "/api/step1", {"complete": True}),
        ("PUT", "/api/step1", {"reviews": None}),
        ("PUT", "/api/step1", {"keywords": "kw_ait"}),
        ("PUT", "/api/step1", []),
        ("POST", "/api/checks", {"situations": []}),
        ("POST", "/api/checks", {"situations": ["none", "low"]}),
        ("POST", "/api/checks", {"situations": ["low", "low"]}),
        ("POST", "/api/checks", {"situations": SITS + ["x"]}),
        ("POST", "/api/checks", {"situations": None}),
        ("POST", "/api/checks", {}),
        ("PATCH", "/api/checks/1", {"answers": {"c1_1": "yes"}}),
        ("PATCH", "/api/checks/1", {"answers": {"zz": "done"}}),
        ("PATCH", "/api/checks/1", {"answers": {"c1_1": None}}),
        ("PATCH", "/api/checks/1", {"action_type": "recommended", "action_key": "situation:zz"}),
        ("PATCH", "/api/checks/1", {"action_type": "recommended", "action_key": "item:s9"}),
        ("PATCH", "/api/checks/1", {"action_type": "recommended", "action_key": "general:_comment"}),
        ("PATCH", "/api/checks/1", {"action_type": "recommended"}),
        ("PATCH", "/api/checks/1", {"action_type": "custom", "action_text": None}),
        ("PATCH", "/api/checks/1", {"complete": True}),
        ("PATCH", "/api/checks/1", {"last_screen": "keyword"}),
        ("PATCH", "/api/checks/0", {"answers": {}}),
        ("PATCH", "/api/checks/-1", {"answers": {}}),
        ("PATCH", "/api/checks/99999999999999999999", {"answers": {}}),
        ("PATCH", "/api/checks/abc", {"answers": {}}),
        ("GET", "/api/checks/1/result", None),
        ("GET", "/api/checks/99999999999999999999/result", None),
        ("DELETE", "/api/checks/99999999999999999999", None),
        ("PUT", "/api/participants/me", {"leader_type": "custom", "custom_name": None}),
        ("PUT", "/api/participants/me", {"leader_type": "rc", "option_order": ["rc", "rc", "mi", "re"]}),
        ("PUT", "/api/participants/me", {"leader_type": "rc", "option_order": ["rc"]}),
        ("POST", "/api/participants", {"leader_type": "rc", "option_order": []}),
        ("POST", "/api/participants", {"leader_type": "xx", "option_order": ["oa", "rc", "mi", "re"]}),
        ("POST", "/api/events", {"events": [{"name": "screen_view", "props": {f"k{i}": 1 for i in range(11)}}]}),
        ("POST", "/api/events", {"events": [{"name": "screen_view", "props": {"screen": {"a": 1}, "visible_ms": 1e300}}]}),
        ("POST", "/api/events", {"events": [{"name": "x" * 41}]}),
        ("POST", "/api/events", {"events": [{"name": "screen_view", "check_seq": 0}]}),
        ("POST", "/api/events", {"events": [{"name": "screen_view"}] * 51}),
        ("POST", "/api/events", {"events": [{"name": "screen_view", "props": {"screen": ["a"] * 11}}]}),
        ("POST", "/api/events", {"events": [{"name": "action_custom_text", "props": {"text": 123}}]}),
        ("POST", "/api/events", {"events": [{"name": "action_custom_text", "props": {"text": "010-1234-5678 " * 20}}]}),
        ("POST", "/api/events", {"events": [{"name": "back_nav", "props": {"from": "x" * 500, "to": None}}]}),
        ("POST", "/api/events", {"events": [{"name": "screen_view", "props": {"visible_ms": -5, "entered_at": "nope"}}]}),
        ("POST", "/api/events", {"events": [{"name": "screen_view", "session_id": "not-a-uuid", "props": {"screen": "check_1"}}]}),
    ]
    for method, url, body in bad_bodies:
        r = call(method, url, pid=pid, json=body) if body is not None else call(method, url, pid=pid)
        if r is not None and r.status_code < 400 and not url.startswith("/api/events"):
            if not (url == "/api/step1" and r.status_code == 200 and body in ({"reviews": None},)):
                stats[f"hostile accepted {r.status_code}: {method} {url} {json.dumps(body, ensure_ascii=False)[:60]}"] += 1
    # 참여자 ID 이상한 값, 키 틀림
    # Infinity 는 테스트 클라이언트가 JSON 으로 만들지 못하므로 본문을 직접 보낸다
    call("POST", "/api/events", pid=pid, expect=200, headers_extra=None,
         content='{"events":[{"name":"screen_view","props":{"screen":"check_1","visible_ms":1e999}}]}')
    for h_pid in ["not-a-uuid", "00000000-0000-0000-0000-000000000000", "' OR 1=1 --"]:
        call("GET", "/api/me", pid=h_pid, expect=404)
    call("GET", "/api/me", pid=pid, key="wrong", expect=403)
    call("GET", "/api/me", pid=pid, key=None, expect=403)
    call("GET", "/api/content", key="x" * 5000, expect=403)


def admin_checks(expected_leaders, expected_done):
    cookie = admin_auth.make_cookie("full")
    C.cookies.set(admin_auth.COOKIE, cookie)
    h = {}
    for url in ["/api/admin/dashboard", "/api/admin/items", "/api/admin/flow", "/api/admin/leaders",
                "/api/admin/leaders?stage=done", "/api/admin/leaders?stage=step1", "/api/admin/leaders?q=리더",
                "/api/admin/leaders?q=%3Cb%3E", "/api/admin/leaders?sort=name&desc=false", "/api/admin/leaders?page=999&size=5",
                "/api/admin/leaders?page=0&size=0", "/api/admin/leaders?sort=zzz", "/api/admin/leaders?stage=zzz",
                "/api/admin/dashboard?type=custom", "/api/admin/dashboard?type=rc", "/api/admin/dashboard?type=zz",
                "/api/admin/dashboard?date_from=2030-01-01", "/api/admin/dashboard?date_from=2020-01-01&date_to=2019-01-01",
                "/api/admin/dashboard?date_from=bad", "/api/admin/dashboard?cycle=nonexistent",
                "/api/admin/export/leaders.csv", "/api/admin/export/checks.csv", "/api/admin/export/texts.csv",
                "/api/admin/export/screens.csv", "/api/admin/flow?type=custom", "/api/admin/items?type=mi"]:
        try:
            r = C.get(url, headers=h)
        except Exception as e:
            tb = traceback.format_exc().strip().splitlines()
            problem(f"ADMIN EXCEPTION {url}", {"tb": tb[-6:]})
            continue
        stats[f"admin {url.split('?')[0]} {r.status_code}"] += 1
        if r.status_code >= 500:
            problem(f"ADMIN 5xx {url}", r.text[:300])
        elif r.status_code not in (200, 422):
            problem(f"ADMIN unexpected {r.status_code} {url}", r.text[:300])
    d = C.get("/api/admin/dashboard").json()
    C.cookies.clear()
    return d


for n in range(N):
    run_leader()
    if n % 25 == 0:
        pid = make_participant()
        if pid:
            hostile(pid)

with db.get_session() as s:
    from sqlalchemy import func, select
    n_part = s.scalar(select(func.count()).select_from(db.Participant))
    n_done = s.scalar(select(func.count()).select_from(db.Check).where(db.Check.status == "done"))
    n_inprog = s.scalar(select(func.count()).select_from(db.Check).where(db.Check.status == "in_progress"))
    n_ev = s.scalar(select(func.count()).select_from(db.Event))
dash = admin_checks(n_part, n_done)

print(json.dumps({"leaders_simulated": N, "seed": SEED, "db": {"participants": n_part, "checks_done": n_done,
                  "checks_in_progress": n_inprog, "events": n_ev}}, ensure_ascii=False))
print("\n== PROBLEMS ==")
if not problems:
    print("(none)")
for k, v in problems.most_common():
    print(f"[{v}] {k}")
    print("    e.g.", json.dumps(examples[k], ensure_ascii=False, default=str)[:700])
print("\n== NOTABLE STATS ==")
for k, v in sorted(stats.items()):
    if k.startswith(("custom", "hostile", "earlier", "answer", "restart", "dropout", "left", "leader name")) or " 5" in k:
        print(f"{v:6d}  {k}")
print("\n== DASHBOARD KEYS ==", list(dash.keys())[:20])
print(json.dumps(dash, ensure_ascii=False, default=str)[:1500])
