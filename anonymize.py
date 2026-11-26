"""직접 쓴 리더명·문장의 패턴 검사(SPEC 3.1, 5.1). AI 를 쓰지 않는다.

막는 패턴: 이메일, 전화번호, 숫자 6자리 이상(사번 등).
"""
from __future__ import annotations

import re
import unicodedata

EMAIL_RE = re.compile(r"[\w.+-]+\s*@\s*[\w-]+(?:\.[\w-]+)+", re.UNICODE)
# 010-1234-5678, 02 123 4567, 031.123.4567, +82 10 1234 5678 등
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]\d{3,4}[\s.-]\d{4}")
LONG_DIGITS_RE = re.compile(r"\d{6,}")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SPACES_RE = re.compile(r"\s+")

LEADER_SUFFIX = "리더"


def collapse_spaces(text: str) -> str:
    return SPACES_RE.sub(" ", text).strip()


def has_blocked_pattern(text: str) -> bool:
    t = unicodedata.normalize("NFKC", text)
    return bool(EMAIL_RE.search(t) or PHONE_RE.search(t) or LONG_DIGITS_RE.search(t))


class LeaderNameError(ValueError):
    """code: 'length' | 'blocked'"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def to_call_form(name: str) -> str:
    """부르는 형태 = 저장 형태 = 관리자 표시 형태. '리더'로 끝나지 않으면 ' 리더'를 붙인다.

    '…리더님'으로 적으면 '님'을 뗀다(화면이 '[리더명]님'으로 부르므로 '리더님 리더'·'리더님님'이 되지 않게).
    화면(app.js callForm)도 같은 규칙.
    """
    name = collapse_spaces(name)
    if name.endswith(LEADER_SUFFIX + "님"):
        name = name[:-1]
    return name if name.endswith(LEADER_SUFFIX) else f"{name} {LEADER_SUFFIX}"


def validate_custom_leader_name(raw: str, min_len: int = 2, max_len: int = 15) -> str:
    """직접 적은 리더명을 검사해 저장 형태로 돌려준다. 실패하면 LeaderNameError."""
    if not isinstance(raw, str):
        raise LeaderNameError("length")
    name = collapse_spaces(CONTROL_RE.sub(" ", raw))
    if not (min_len <= len(name) <= max_len):
        raise LeaderNameError("length")
    if has_blocked_pattern(name):
        raise LeaderNameError("blocked")
    return to_call_form(name)


THREE_HANGUL_RE = re.compile(r"^[가-힣]{3}$")


def looks_like_person_name(name: str, rule: dict) -> bool:
    """직접 적은 리더명이 사람 이름(성 + 이름 3글자)처럼 보이는지 — 막지 않고 안내·표시만 한다(2026-11-27).

    rule = content.leader_name.custom.name_like: surnames(흔한 성 첫 글자), exclude_endings(꾸미는 말 끝 글자 — '이끄는', '정직한' 등 제외).
    화면(app.js looksLikeName)도 같은 규칙. 오탐을 줄이려고 띄어쓰기 없는 한글 3글자만 본다.
    """
    base = collapse_spaces(name or "")
    if base.endswith(LEADER_SUFFIX):
        base = base[: -len(LEADER_SUFFIX)].strip()
    if not THREE_HANGUL_RE.match(base):
        return False
    return base[0] in rule.get("surnames", "") and base[-1] not in rule.get("exclude_endings", "")


def name_key(name: str) -> str:
    """같은 리더명 판단용 값: 앞뒤 공백 제거·연속 공백 1개 + ' 리더' 규칙 적용."""
    return to_call_form(name)


# 전각 영문·숫자·기호(！~～)와 전각 공백 → 반각. 한 글자를 한 글자로 바꾸므로 원문과 위치가 그대로 맞는다
FULLWIDTH_TO_ASCII = {**{cp: cp - 0xFEE0 for cp in range(0xFF01, 0xFF5F)}, 0x3000: 0x20}


def _strip_matches(pattern: re.Pattern, orig: str, probe: str) -> tuple[str, str]:
    """probe(검사용 사본)에서 찾은 자리를 원문과 사본 양쪽에서 지운다."""
    keep_o, keep_p, last = [], [], 0
    for m in pattern.finditer(probe):
        keep_o.append(orig[last:m.start()])
        keep_p.append(probe[last:m.start()])
        last = m.end()
    keep_o.append(orig[last:])
    keep_p.append(probe[last:])
    return "".join(keep_o), "".join(keep_p)


def scrub_text(text: str) -> str:
    """직접 쓴 문장에서 막는 패턴(이메일·전화번호·숫자 6자리 이상)을 지운다(실천 문장 저장 시 사용).

    문장 자체는 NFKC 로 바꾸지 않는다: NFKC 는 'ㅋㅋ'·'ㅠㅠ'(호환용 자모)를 조합용 자모로, '…'를 '...'로,
    '㈜'를 '(주)'로 바꿔 관리자 화면에서 글자가 깨지거나 길어진다. 전각 문자(＠, １ 등)로 피해 가는 경우만
    검사용 사본을 반각으로 바꿔 찾고, 찾은 자리를 원문에서 지운다.
    """
    orig = unicodedata.normalize("NFC", text)
    probe = orig.translate(FULLWIDTH_TO_ASCII)
    for pattern in (EMAIL_RE, PHONE_RE, LONG_DIGITS_RE):
        orig, probe = _strip_matches(pattern, orig, probe)
    return collapse_spaces(CONTROL_RE.sub(" ", orig))
