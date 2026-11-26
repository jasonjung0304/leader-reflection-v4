import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

KEY = "test-entry-key"
ORDER = ["oa", "rc", "mi", "re"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "test-1")
    monkeypatch.setenv("ENTRY_KEY", KEY)
    monkeypatch.delenv("VERCEL", raising=False)
    db.reset_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    return TestClient(main.app)


def hdr(pid=None, key=KEY, device=None):
    h = {}
    if key is not None:
        h["X-Entry-Key"] = key
    if pid:
        h["X-Participant-Id"] = pid
    if device:
        h["X-Device-Id"] = device
    return h


def create(client, leader_type="rc", custom=None, order=ORDER, **kw):
    return client.post("/api/participants", headers=hdr(**kw),
                       json={"leader_type": leader_type, "custom_name": custom, "option_order": order})
