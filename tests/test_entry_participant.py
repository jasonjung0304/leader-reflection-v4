"""2단계: 입장 키 확인, 참여자 생성(리더명), 리더명 변경, 이어 하기, 권한."""
import datetime as dt
import uuid

import pytest
from conftest import ORDER, create, hdr

import db
import main


def now():
    return dt.datetime.now(dt.timezone.utc)


# ---------------- 입장 키 ----------------
def test_content_without_key_denied_and_logged(client):
    dev = str(uuid.uuid4())
    r = client.get("/api/content", headers={"X-Device-Id": dev})
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "entry_denied"
    assert body["message"] == main.load_content()["notice"]["entry_denied"]
    assert "content" not in body
    with db.get_session() as s:
        ev = s.query(db.Event).one()
        assert (ev.name, ev.device_id, ev.props) == ("entry_denied", dev, {"reason": "missing"})


def test_wrong_key_denied_and_not_logged_raw(client):
    r = client.get("/api/content", headers=hdr(key="secret-guess"))
    assert r.status_code == 403
    with db.get_session() as s:
        ev = s.query(db.Event).one()
        assert ev.props == {"reason": "wrong"}
        assert "secret-guess" not in repr({c.name: getattr(ev, c.name) for c in ev.__table__.columns})


def test_admin_setting_overrides_env_key(client):
    with db.get_session() as s:
        db.save_settings(s, {"entry_key": "new-key"})
    assert client.get("/api/content", headers=hdr()).status_code == 403
    assert client.get("/api/content", headers=hdr(key="new-key")).status_code == 200


def test_vercel_without_key_denies_all(client, monkeypatch):
    monkeypatch.delenv("ENTRY_KEY")
    monkeypatch.setattr(main, "ON_VERCEL", True)
    assert client.get("/api/content", headers=hdr(key="dev")).status_code == 403


def test_local_without_key_uses_dev(client, monkeypatch):
    monkeypatch.delenv("ENTRY_KEY")
    assert client.get("/api/content", headers=hdr(key="dev")).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("post", "/api/participants"), ("put", "/api/participants/me"), ("get", "/api/me"),
])
def test_user_apis_need_key(client, method, path):
    pid = create(client).json()["participant_id"]
    kwargs = {} if method == "get" else {"json": {"leader_type": "rc", "option_order": ORDER}}
    r = getattr(client, method)(path, headers=hdr(pid=pid, key=None), **kwargs)
    assert r.status_code == 403 and r.json()["error"] == "entry_denied"


# ---------------- 마감 ----------------
def test_closed(client):
    pid = create(client).json()["participant_id"]
    with db.get_session() as s:
        db.save_settings(s, {"deadline": "2000-01-01"})
    r = client.get("/api/content", headers=hdr())
    assert r.status_code == 200 and r.json() == {
        "closed": True, "message": main.load_content()["notice"]["closed"], "version": "test-1"}
    assert create(client).status_code == 403
    assert client.get("/api/me", headers=hdr(pid=pid)).json()["error"] == "closed"


def test_not_closed_on_deadline_day():
    kst = dt.timezone(dt.timedelta(hours=9))
    today = dt.datetime.now(kst).date().isoformat()
    assert main.is_closed({"deadline": today}) is False


# ---------------- 참여자 생성 ----------------
def test_create_preset(client):
    r = create(client, "re")
    assert r.status_code == 201
    body = r.json()
    uuid.UUID(body["participant_id"], version=4)
    assert body["leader_name"] == "다정하고 배려 깊은 리더"
    assert body["leader_type"] == "re" and body["option_order"] == ORDER
    assert "serial" not in body
    with db.get_session() as s:
        p = s.get(db.Participant, body["participant_id"])
        assert p.serial.isdigit() and len(p.serial) == 4
        assert p.device == "pc" and p.cycle_id == "2026-1"


def test_device_from_user_agent_not_stored_raw(client):
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148"
    r = client.post("/api/participants", headers={**hdr(), "User-Agent": ua},
                    json={"leader_type": "rc", "option_order": ORDER})
    with db.get_session() as s:
        p = s.get(db.Participant, r.json()["participant_id"])
        assert p.device == "mobile"
        assert "iPhone" not in repr({c.name: getattr(p, c.name) for c in p.__table__.columns})


def test_create_custom_appends_leader(client):
    r = create(client, "custom", "  묻고   듣는 ")
    assert r.status_code == 201 and r.json()["leader_name"] == "묻고 듣는 리더"


@pytest.mark.parametrize("custom,code", [
    ("가", "leader_name_length"), ("a@b.com", "leader_name_blocked"),
    ("010-1234-5678", "leader_name_blocked"), ("사번 123456", "leader_name_blocked"),
    (None, "leader_name_length"),
])
def test_create_custom_rejected(client, custom, code):
    r = create(client, "custom", custom)
    assert r.status_code == 422 and r.json()["error"] == code
    with db.get_session() as s:
        assert s.query(db.Participant).count() == 0


def test_blocked_message_from_content(client):
    r = create(client, "custom", "a@b.com")
    assert r.json()["message"] == main.load_content()["leader_name"]["custom"]["blocked_message"]


@pytest.mark.parametrize("order", [[], ["rc", "re", "oa"], ["rc", "rc", "oa", "mi"], ["rc", "re", "oa", "xx"]])
def test_bad_option_order(client, order):
    assert create(client, order=order).status_code == 422


def test_bad_leader_type(client):
    r = client.post("/api/participants", headers=hdr(), json={"leader_type": "boss", "option_order": ORDER})
    assert r.status_code == 422


def test_serials_unique_within_same_name(client):
    for _ in range(30):
        assert create(client, "custom", "묻고 듣는").status_code == 201
    create(client, "custom", "묻고 듣는 리더")
    with db.get_session() as s:
        rows = s.query(db.Participant).all()
        assert len({r.leader_name_norm for r in rows}) == 1
        assert len({r.serial for r in rows}) == 31


# ---------------- 리더명 변경 ----------------
def test_rename_before_step1(client):
    pid = create(client, "rc").json()["participant_id"]
    r = client.put("/api/participants/me", headers=hdr(pid=pid),
                   json={"leader_type": "custom", "custom_name": "등대"})
    assert r.status_code == 200 and r.json()["leader_name"] == "등대 리더"
    assert r.json()["option_order"] == ORDER  # 순서는 그대로
    with db.get_session() as s:
        p = s.get(db.Participant, pid)
        assert (p.name_changes, p.leader_type, p.leader_name_norm) == (1, "custom", "등대 리더")
        assert s.query(db.Participant).count() == 1


def test_rename_locked_after_step1(client):
    pid = create(client).json()["participant_id"]
    with db.get_session() as s:
        s.add(db.Step1(participant_id=pid, completed_at=now()))
        s.commit()
    r = client.put("/api/participants/me", headers=hdr(pid=pid), json={"leader_type": "mi"})
    assert r.status_code == 409 and r.json()["error"] == "leader_name_locked"


# ---------------- 이어 하기 ----------------
def test_resume_new_participant(client):
    pid = create(client, "oa").json()["participant_id"]
    body = client.get("/api/me", headers=hdr(pid=pid)).json()
    assert body["participant_id"] == pid and body["leader_name"] == "단호하고 명확한 리더"
    assert body["step1"] is None and body["check_in_progress"] is None and body["checks_done"] == 0
    assert body["leader_name_locked"] is False


def test_resume_with_progress(client):
    pid = create(client).json()["participant_id"]
    with db.get_session() as s:
        s.add(db.Step1(participant_id=pid, reviews={"review_1": "remembered"}, keywords=["k1"],
                       last_screen="keyword", completed_at=now()))
        s.add(db.Check(id=str(uuid.uuid4()), participant_id=pid, seq=1, status="done"))
        s.add(db.Check(id=str(uuid.uuid4()), participant_id=pid, seq=2, status="in_progress",
                       last_screen="check_3", answers={"c1_1": "done"}))
        s.commit()
    body = client.get("/api/me", headers=hdr(pid=pid)).json()
    assert body["leader_name_locked"] is True
    assert body["step1"]["done"] is True and body["checks_done"] == 1
    assert body["check_in_progress"]["seq"] == 2 and body["check_in_progress"]["last_screen"] == "check_3"


@pytest.mark.parametrize("pid", [None, "not-a-uuid", str(uuid.uuid4())])
def test_unknown_participant(client, pid):
    create(client)
    r = client.get("/api/me", headers=hdr(pid=pid))
    assert r.status_code == 404 and r.json()["error"] == "participant_not_found"


def test_participant_only_sees_own_data(client):
    a = create(client, "rc").json()["participant_id"]
    b = create(client, "mi").json()["participant_id"]
    assert client.get("/api/me", headers=hdr(pid=a)).json()["participant_id"] == a
    assert client.get("/api/me", headers=hdr(pid=b)).json()["leader_type"] == "mi"
    client.put("/api/participants/me", headers=hdr(pid=b), json={"leader_type": "oa"})
    assert client.get("/api/me", headers=hdr(pid=a)).json()["leader_type"] == "rc"


def test_other_cycle_participant_not_resumed(client):
    pid = create(client).json()["participant_id"]
    with db.get_session() as s:
        db.save_settings(s, {"cycle_id": "2026-2"})
    assert client.get("/api/me", headers=hdr(pid=pid)).status_code == 404
