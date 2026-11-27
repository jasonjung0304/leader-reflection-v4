"""1단계 뼈대 확인: 앱 기동, /api/content, DB 테이블 생성, 정적 파일(로컬)."""
import pytest
from conftest import hdr
from sqlalchemy import inspect

import db
import main


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": "test-1", "db": "sqlite"}


def test_content_served_from_json(client):
    r = client.get("/api/content", headers=hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "test-1"
    assert body["content"] == main.load_content()
    assert "leader_name" in body["content"] and "items" in body["content"]


def test_tables_created(client):
    names = set(inspect(db.get_engine()).get_table_names())
    assert {"participant", "step1", "check_run", "event", "setting"} <= names


def test_local_static(client):
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200


@pytest.mark.parametrize("raw,expected", [
    (None, "sqlite:///"),
    ("postgres://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("postgresql://u:p@h/d?sslmode=require", "postgresql+psycopg://u:p@h/d?sslmode=require"),
])
def test_normalize_db_url(raw, expected):
    assert db.normalize_db_url(raw).startswith(expected)


def test_supabase_url_params_cleaned():
    url = "postgres://u:p@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require&supa=base-pooler.x"
    assert db.normalize_db_url(url) == (
        "postgresql+psycopg://u:p@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require")


def test_ssl_required_by_default():
    assert db.normalize_db_url("postgresql://u:p@h/d").endswith("?sslmode=require")


def test_postgres_url_fallback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@h/d")
    assert db.env_db_url() == "postgres://u:p@h/d"
    monkeypatch.setenv("DATABASE_URL", "postgres://x:y@h2/d")
    assert db.env_db_url() == "postgres://x:y@h2/d"


def test_vercel_without_db_url_refuses_sqlite(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError):
        db.normalize_db_url(None)


def test_security_headers(client):
    for path in ("/", "/api/health", "/api/content"):
        r = client.get(path)
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/content").headers["cache-control"] == "no-store"


def test_version_from_vercel_commit(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "abcdef1234567")
    assert main.app_version() == "dev-abcdef1"


def test_events_sent_without_entry_key_when_open_entry():
    """2026-11-27 회귀 방지: 키 없는 기본 주소(입장 키 없이 들어오기)에서도 화면이 이벤트를 보내야 한다.
    예전 app.js 는 입장 키가 없으면 이벤트를 아예 보내지 않아 관리자 '사용 흐름'·단계별 인원이 비었다."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "public" / "app.js").read_text(encoding="utf-8")
    flush = js[js.index("function flush("): js.index("function track(")]
    assert "if (!q.length || !S.entryKey) return;" not in flush
    assert "open_entry" in flush
    assert "'X-Entry-Key': S.entryKey }" not in flush   # 키가 없으면 헤더를 빼고 보낸다
