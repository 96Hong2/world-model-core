"""답변 문장의 표기를 **발췌의 표기로 되돌린다.**

프롬프트는 이미 "발췌에 있는 값은 원문 표기 그대로 옮겨라. 띄어쓰기까지 그대로" 라고 지시한다.
그런데 실측에서 모델이 계속 미세하게 고쳐 썼다.

    발췌 `한서버`        → 답변 `한 서버`        (띄어쓰기를 넣었다 · GQ-D6)
    발췌 `2026-06-01`  → 답변 `2026년 6월 1일`  (날짜 형식을 바꿨다 · GQ-A1)

두 답변 다 뜻은 맞다. 문제는 **그 값을 원문에서 찾을 수 없게 된다**는 것이다. 사용자가 근거를
되짚을 때도, 검사가 대조할 때도 같은 문자열이어야 한다.

부탁으로 안 되는 일은 결정적으로 처리한다. 여기서 하는 것은 **표기 되돌리기뿐**이고,
없던 값을 만들지 않는다. 발췌에 실제로 있는 표기로만 바꾸므로 근거 없는 값이 새로 생길 수 없다.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

#: `2026년 6월 1일` · `2026년 6월` 처럼 우리말로 쓴 날짜.
_KO_DATE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")

#: 되돌릴 표기의 최소 길이. 한두 글자를 건드리면 엉뚱한 자리를 바꾼다.
_MIN_LEN = 3

#: 한 문장에서 되돌릴 표기 수 상한. 폭주를 막는 안전장치다.
_MAX_FIXES = 40


def _iso_dates(text: str) -> set[str]:
    return set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text))


def _spaced_variants(term: str) -> list[str]:
    """`한서버` → [`한 서버`]. 사이마다 공백 하나를 넣은 표기를 만든다.

    발췌 쪽 표기에 공백이 없을 때, 모델이 넣었을 만한 자리를 찾는다.
    """
    if len(term) < _MIN_LEN or " " in term:
        return []
    return [term[:i] + " " + term[i:] for i in range(1, len(term))]


def _source_terms(snippets: Iterable[str]) -> list[str]:
    """발췌에서 **공백 없는 한글 낱말**만 모은다. 되돌리기 후보다."""
    seen: set[str] = set()
    for snippet in snippets:
        for token in re.findall(r"[가-힣]{3,12}", snippet or ""):
            seen.add(token)
    return sorted(seen, key=len, reverse=True)


def restore_notation(text: str, snippets: Sequence[str]) -> str:
    """`text` 안의 표기를 발췌의 표기로 되돌린다. 발췌에 없는 표기는 건드리지 않는다."""
    if not text or not snippets:
        return text

    joined = "\n".join(s or "" for s in snippets)
    fixes = 0

    # 1) 날짜 — 우리말 표기를 발췌에 실제로 있는 ISO 표기로 되돌린다.
    iso = _iso_dates(joined)
    if iso:

        def to_iso(match: re.Match[str]) -> str:
            nonlocal fixes
            if fixes >= _MAX_FIXES:
                return match.group(0)
            year, month, day = match.group(1), match.group(2), match.group(3)
            if day is None:
                return match.group(0)
            candidate = f"{year}-{int(month):02d}-{int(day):02d}"
            if candidate in iso:
                fixes += 1
                return candidate
            return match.group(0)

        text = _KO_DATE.sub(to_iso, text)

    # 2) 띄어쓰기 — 발췌가 붙여 쓴 낱말을 모델이 띄어 썼으면 되돌린다.
    for term in _source_terms(snippets):
        if fixes >= _MAX_FIXES:
            break
        if term in text:
            continue  # 이미 원문 표기대로다
        for variant in _spaced_variants(term):
            if variant in text and variant not in joined:
                # 발췌가 그 띄어쓴 표기를 쓰지 않을 때만 되돌린다(둘 다 쓰면 손대지 않는다).
                text = text.replace(variant, term)
                fixes += 1
                break

    return text


__all__ = ["restore_notation"]
