"""로컬 확인용 가짜 데이터 만들기 — 관리자 화면을 미리 볼 때만 쓴다(운영 DB 에 쓰지 말 것).

    .venv\\Scripts\\python scripts\\seed_demo.py [기본 주소 http://127.0.0.1:8000] [인원 30]

사용자 API 를 실제로 호출해 리더명 → STEP 1 → 점검 → 이벤트까지 무작위로 만든다.
"""
import random
import sys
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 30
KEY = "dev"
if "127.0.0.1" not in BASE and "localhost" not in BASE:
    sys.exit("로컬 주소에서만 실행하세요")

c = httpx.Client(base_url=BASE, timeout=30)
content = c.get("/api/content", headers={"X-Entry-Key": KEY}).json()["content"]
REVIEWS = [r["id"] for r in content["reviews"]]
KWS = [k["id"] for k in content["keywords"]["items"]]
SITS = [s["id"] for s in content["member"]["situations"] if s["id"] != "none"]
PAGES = content["checklist_pages"]
TYPES = ["rc", "re", "oa", "mi"]
UA = {"mobile": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148", "pc": "Mozilla/5.0 (Windows NT 10.0) Chrome/140"}
rnd = random.Random(7)


def ev(h, events):
    c.post("/api/events", headers=h, json={"events": events})


def sv(screen, seq=None, ms=None):
    e = {"name": "screen_view", "props": {"screen": screen, "visit": str(uuid.uuid4()), "final": True,
                                          "visible_ms": ms or rnd.randint(3000, 40000)}}
    if seq:
        e["check_seq"] = seq
    return e


for i in range(N):
    device = rnd.choice(["mobile", "mobile", "pc"])
    dev = str(uuid.uuid4())
    base = {"X-Entry-Key": KEY, "X-Device-Id": dev, "User-Agent": UA[device]}
    ev(base, [sv("notice")])
    if rnd.random() < 0.12:
        continue                                            # 안내만 보고 나감
    order = rnd.sample(TYPES, 4)
    lt = rnd.choice(TYPES + ["custom"])
    body = {"leader_type": lt, "option_order": order, "custom_name": rnd.choice(["묻고 듣는", "함께 크는", "등대"]) if lt == "custom" else None}
    pid = c.post("/api/participants", headers=base, json=body).json()["participant_id"]
    h = dict(base, **{"X-Participant-Id": pid})
    ev(h, [sv("leader_name"), {"name": "leader_name_pick", "props": {"leader_type": lt, "option_order": order, "changes": rnd.randint(0, 2)}}, sv("intro")])
    stop = rnd.random()
    reviews = {}
    for n, r in enumerate(REVIEWS, 1):
        val = "remembered" if rnd.random() < 0.7 else "not_remembered"
        reviews[r] = val
        c.put("/api/step1", headers=h, json={"reviews": {r: val}, "last_screen": f"review_{n + 1}" if n < 5 else "keyword"})
        ev(h, [sv(f"review_{n}", ms=rnd.randint(8000, 60000) if val == "not_remembered" else rnd.randint(4000, 25000)),
               {"name": "review_recall", "props": {"review": r, "value": val}}])
        if stop < 0.08 and n == 3:
            break
    if stop < 0.08:
        continue                                            # STEP 1 도중 이탈
    kws = ["kw_none"] if rnd.random() < 0.1 else rnd.sample(KWS, rnd.randint(1, 3))
    c.put("/api/step1", headers=h, json={"keywords": kws, "last_screen": "keyword", "complete": True})
    ev(h, [sv("keyword")] + [{"name": "keyword_pick", "props": {"keyword": k, "order": j + 1}} for j, k in enumerate(kws)])
    members = rnd.choice([0, 1, 1, 1, 2, 2, 3])
    care_bias, chal_bias = rnd.random(), rnd.random()
    for m in range(members + (1 if stop < 0.2 else 0)):
        sits = ["none"] if rnd.random() < 0.3 else rnd.sample(SITS, rnd.randint(1, 3))
        seq = c.post("/api/checks", headers=h, json={"situations": sits}).json()["seq"]
        ev(h, [sv("member", seq=seq), {"name": "nickname_method", "props": rnd.choice([{"method": "auto", "regenerate_count": 0},
                                                                                         {"method": "regenerated", "regenerate_count": rnd.randint(1, 4)},
                                                                                         {"method": "manual", "regenerate_count": 0}]), "check_seq": seq}])
        if m == members:                                    # 진행 중에 멈춘 점검
            c.patch(f"/api/checks/{seq}", headers=h, json={"answers": {"c1_1": "done"}, "last_screen": "check_2"})
            ev(h, [sv("check_1", seq=seq)])
            break
        answers = {}
        for p in PAGES:
            for it in p["items"]:
                item = next(x for x in content["items"] if x["id"] == it)
                if item["type"] == "signal":
                    answers[it] = "yes" if rnd.random() < 0.25 else "no"
                else:
                    bias = care_bias if item["axis"] == "care" else chal_bias if item["axis"] == "challenge" else 0.6
                    answers[it] = "done" if rnd.random() < 0.3 + 0.6 * bias else "not_done"
            ev(h, [sv(f"check_{p['page']}", seq=seq)])
        c.patch(f"/api/checks/{seq}", headers=h, json={"answers": answers, "last_screen": "result_1"})
        res = c.get(f"/api/checks/{seq}/result", headers=h).json()["results"]
        ev(h, [sv("analyzing", seq=seq, ms=3000), sv("result_1", seq=seq), sv("result_2", seq=seq), sv("result_3", seq=seq), sv("action", seq=seq)])
        if rnd.random() < 0.15:
            ev(h, [{"name": "back_nav", "props": {"from": "result_1", "to": "check_5"}, "check_seq": seq},
                   {"name": "result_back_to_checklist", "check_seq": seq},
                   {"name": "answer_changed_after_result", "props": {"item": "c3_2", "before": "not_done", "after": "done"}, "check_seq": seq}])
        if rnd.random() < 0.2:
            body = {"action_type": "custom", "action_text": rnd.choice(["○○님, 지난번엔 제가 말을 많이 끊었어요. 이번엔 끝까지 들을게요.",
                                                                         "○○님이 먼저 이야기해 주면 좋겠어요.", "연락은 010-1234-5678 로 주세요"]), "complete": True}
        else:
            opt = rnd.choice(res["action_options"])
            body = {"action_type": opt["type"], "action_key": opt["key"], "complete": True}
        c.patch(f"/api/checks/{seq}", headers=h, json=body)
        done_evs = [sv("done", seq=seq)]
        click = lambda b: {"name": "done_button_click", "props": {"button": b}, "check_seq": seq}  # noqa: E731
        for _ in range(rnd.choice([0, 1, 1, 1, 2])):          # PDF 다운받기(여러 번 누르는 리더도 있음)
            done_evs += [click("pdf"), {"name": "result_pdf_saved", "check_seq": seq}]
        if rnd.random() < 0.25:
            done_evs += [click("recommend"), {"name": "recommend_copied", "check_seq": seq}]
        if m < members - 1:
            done_evs += [click("again"), {"name": "another_member_started", "check_seq": seq}]
        ev(h, done_evs)
    if rnd.random() < 0.1:
        ev(h, [{"name": "error", "props": {"kind": rnd.choice(["pdf", "share", "save_retry"]), "browser": rnd.choice(["safari", "kakaotalk", "chrome"])}}])

c.get("/api/content")                                       # 입장 키 없이 접속 1건
c.get("/api/content", headers={"X-Entry-Key": "wrong"})     # 틀린 키 1건
print(f"{N}명 분량을 만들었어요: {BASE}/admin (로컬 암호 dev-full / dev-read)")
