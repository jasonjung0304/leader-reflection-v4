import pytest

import anonymize as an


@pytest.mark.parametrize("raw,expected", [
    ("묻고 듣는", "묻고 듣는 리더"),
    ("묻고 듣는 리더", "묻고 듣는 리더"),
    ("  묻고   듣는  ", "묻고 듣는 리더"),
    ("함께리더", "함께리더"),
    ("등대", "등대 리더"),
])
def test_custom_name_call_form(raw, expected):
    assert an.validate_custom_leader_name(raw) == expected


def test_same_name_key():
    assert an.name_key("묻고 듣는") == an.name_key(" 묻고  듣는 리더 ")


@pytest.mark.parametrize("raw", ["가", " 가 ", "", "가" * 16])
def test_length(raw):
    with pytest.raises(an.LeaderNameError) as e:
        an.validate_custom_leader_name(raw)
    assert e.value.code == "length"


@pytest.mark.parametrize("raw", ["a@b.com", "hong @ gc.co.kr", "010-1234-5678", "02 123 4567",
                                 "사번123456", "１２３４５６", "리더 1234567"])
def test_blocked(raw):
    with pytest.raises(an.LeaderNameError) as e:
        an.validate_custom_leader_name(raw)
    assert e.value.code == "blocked"


def test_short_numbers_allowed():
    assert an.validate_custom_leader_name("2026 첫 리더") == "2026 첫 리더"


def test_scrub_text():
    assert an.scrub_text("○○님 연락은 010-1234-5678 또는 a@b.com 으로") == "○○님 연락은 또는 으로"


# ---------------- 리더명이 사람 이름처럼 보이는지(2026-11-27, 안내·표시만) ----------------
import json as _json  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_RULE = _json.loads((_Path(__file__).resolve().parents[1] / "content" / "content.json").read_text(encoding="utf-8"))["leader_name"]["custom"]["name_like"]


@pytest.mark.parametrize("name,expected", [
    ("홍길동", True), ("김지은 리더", True), ("이서준리더", True), ("박민수", True),
    ("이끄는", False), ("정직한", False), ("차분한 리더", False), ("묻고 듣는", False), ("고마운", False),
    ("강철", False), ("남궁민수", False), ("Kim", False), ("", False),
])
def test_looks_like_person_name(name, expected):
    assert an.looks_like_person_name(name, _RULE) is expected
