"""공통 후처리 4단 — citation 검증 (REVISED §4 ⑦ · Answer 계약 citations).

계약이 요구하는 것은 하나다. "evidence_id 가 evidence[] 에 실존하지 않으면 그 문장을 제거한다
(traceability 100%)". 판정은 전부 문자열 비교다. LLM 이 자기 인용을 스스로 검사하게 두지 않는다.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def verify_citations(
    citations: Sequence[dict[str, Any]], evidence_ids: Iterable[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(살아남은 각주, 제거된 각주).

    제거된 것을 조용히 버리지 않고 함께 돌려준다. 무엇이 왜 빠졌는지 A6 가 기록해야 한다.
    """
    pool = {eid for eid in evidence_ids if eid}
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for citation in citations:
        if citation.get("evidence_id") in pool:
            kept.append(citation)
        else:
            dropped.append(citation)
    return kept, dropped


def strip_uncited_sentences(text: str, dropped_markers: Iterable[int]) -> str:
    """제거된 각주가 붙어 있던 문장을 본문에서 걷어낸다.

    문장 경계는 마침표·줄바꿈으로만 본다. 문장 안에 살아 있는 각주가 하나라도 있으면 남긴다.
    """
    markers = {f"[{m}]" for m in dropped_markers}
    if not markers or not text:
        return text

    out: list[str] = []
    for line in text.splitlines():
        pieces = [p for p in _split_sentences(line) if not _only_dropped(p, markers)]
        out.append(" ".join(pieces).strip())
    return "\n".join(line for line in out if line).strip()


def _split_sentences(line: str) -> list[str]:
    """마침표·느낌표는 **뒤가 공백이거나 줄 끝일 때만** 문장 경계다.

    그러지 않으면 '2.85억' 이 '3.' 에서 쪼개져, 각주 문장을 지울 때
    '격차는 3.' 같은 훼손 조각이 남는다(api/guards.split_sentences 와 같은 규칙.
    retrieval 은 api 를 import 하지 않으므로 규칙을 복제한다).
    """
    parts: list[str] = []
    buffer = ""
    for index, ch in enumerate(line):
        buffer += ch
        if ch not in ".。!?":
            continue
        nxt = line[index + 1] if index + 1 < len(line) else ""
        if ch in ".!" and nxt and not nxt.isspace():
            continue
        parts.append(buffer)
        buffer = ""
    if buffer.strip():
        parts.append(buffer)
    return parts


def _only_dropped(sentence: str, dropped: set[str]) -> bool:
    hit = [m for m in dropped if m in sentence]
    if not hit:
        return False
    remaining = sentence
    for marker in hit:
        remaining = remaining.replace(marker, "")
    return "[" not in remaining
