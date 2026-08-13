"""각주 되붙이기 — 모델이 번호만 적고 본문에 안 달았을 때.

합성 모델은 `citations` 에 근거 번호를 적으면서 본문 문장 끝에 `[n]` 을 빼먹는 일이 있다.
그러면 조립 단계의 "본문에서 사라진 각주는 버린다" 규칙에 걸려 근거가 전부 사라진다.
실측(직전 라운드 저장본 `data/cache/gate_1a_answers.json`, 2026-08-08 07:57):

    GQ-P2  본문 3문장 · 인용 0건 · guard 로그 전부 0
           캐시된 모델 출력에는 citations=[{marker:5},{marker:7}] 이 있었다
    GQ-A1  본문 2문장 · 인용 0건 · 모델 출력 citations=[{marker:2}]

게이트가 지운 것도 아니고 인용 검증이 떨어뜨린 것도 아니다. 번호가 본문에 없어서 조용히
버려졌고, 로그에도 아무 흔적이 없었다. 그래서 데모에서 그 질문이 나오면 3클릭이 끊긴다.

여기서 하는 일은 **문장을 만들지 않는다.** 이미 게이트를 통과해 남아 있는 문장 중, 그
발췌를 실제로 옮겨 적은 문장을 찾아 번호만 붙인다. 찾지 못하면 붙이지 않고 그 사실을
남긴다(`GuardReport.unanchored_citations`).

**게이트가 지운 문장의 각주는 되살리지 않는다.** 되붙이기 대상은 모델이 애초에 본문에
번호를 달지 않은 각주뿐이다(`already` 로 걸러 낸다). 그러지 않으면 무근거 문장이 지워질
때마다 각주가 되살아나 게이트가 무력해진다.

두 번째 구멍은 **모델이 citations 에도 안 적은 각주**다. 그러면 위의 되붙이기가 붙일 것이
없어 문장이 각주 없이 남는다. 숫자·회사명이 든 문장이 그렇게 남으면 무근거는 아니지만
(게이트가 이미 근거 안에 있는 값만 남겼다) 사용자가 그 값의 원본으로 갈 수 없다 — 1A AC-4
가 그 문장에서 막힌다. Lead 실측(2026-08-08 14:00 GQ-D1):

    "가격 논리에는 … 가나손해보험 기여가치 5.5억원을 근거로 함께 제시할 수 있습니다."
    → 12문장 중 이 1문장만 마커가 없었다. 6.66 은 발췌에 실재한다.

그래서 `anchor_orphan_tokens` 가 마지막으로 한 번 더 훑는다. **문장을 지우지 않는다** —
지우는 쪽으로 먼저 가면 답변이 통째로 사라지는 사고가 난다(GQ-P2 전례). 그 값을 가진
발췌를 찾아 번호만 붙이고, 못 찾으면 그대로 둔다.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

from retrieval.text import content_terms, term_overlap

from .guards import _ASCII_PROPER, _KO_ORG, _NUMBER, normalize, split_sentences, surviving_markers

#: 문장과 발췌가 같은 것을 말한다고 보려면 겹쳐야 하는 내용어 수. 하나만 겹치는 것은
#: 흔한 낱말 하나로 아무 문장에나 각주를 붙이는 것과 같아서 인정하지 않는다.
MIN_OVERLAP = 2

_MARKER = re.compile(r"\[\d{1,3}\]")


def anchor_citations(
    text: str,
    citations: Sequence[Mapping[str, Any]],
    snippets: Mapping[str, str],
    *,
    already: set[int] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """(번호를 붙인 본문, 붙인 각주, 못 붙인 각주).

    `already` 는 모델이 원래 본문에 달았던 번호다. 그 번호는 손대지 않는다 — 본문에서
    사라졌다면 게이트가 그 문장을 지웠다는 뜻이고, 그 판단을 뒤집지 않는다.
    """
    already = already or set()
    placed = surviving_markers(text)
    pending = [
        dict(c)
        for c in citations
        if isinstance(c.get("marker"), int)
        and c["marker"] not in placed
        and c["marker"] not in already
    ]
    if not text.strip() or not pending:
        return text, [], []

    lines = text.splitlines()
    # (줄 번호, 문장 번호) → 문장. 문장을 통째로 바꿔치기하려고 자리를 기억한다.
    grid = [split_sentences(line) for line in lines]
    terms_at = {
        (i, j): content_terms(sentence)
        for i, sentences in enumerate(grid)
        for j, sentence in enumerate(sentences)
        if sentence.strip()
    }

    anchored: list[dict[str, Any]] = []
    unanchored: list[dict[str, Any]] = []
    for citation in sorted(pending, key=lambda c: c["marker"]):
        snippet = snippets.get(str(citation.get("evidence_id") or "")) or ""
        spot, score = _best_spot(terms_at, snippet)
        if spot is None or score < MIN_OVERLAP:
            unanchored.append(
                {"marker": citation["marker"], "evidence_id": citation.get("evidence_id", "")}
            )
            continue
        i, j = spot
        grid[i][j] = _append_marker(grid[i][j], citation["marker"])
        anchored.append(dict(citation))

    rebuilt = "\n".join("".join(sentences) for sentences in grid)
    return rebuilt, anchored, unanchored


def _best_spot(
    terms_at: Mapping[tuple[int, int], list[str]], snippet: str
) -> tuple[tuple[int, int] | None, int]:
    """발췌를 가장 많이 옮겨 적은 문장. 같으면 앞 문장을 고른다."""
    best: tuple[int, int] | None = None
    best_score = 0
    for spot in sorted(terms_at):
        score = len(term_overlap(terms_at[spot], snippet))
        if score > best_score:
            best, best_score = spot, score
    return best, best_score


def _append_marker(sentence: str, marker: int) -> str:
    """문장 끝(뒤쪽 공백 앞)에 번호를 붙인다."""
    stripped = sentence.rstrip()
    tail = sentence[len(stripped) :]
    return f"{stripped}[{marker}]{tail}"


# ---------------------------------------------------------------------------
# 모델이 citations 에도 적지 않은 각주 — 값을 가진 발췌를 찾아 붙인다
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    body = _MARKER.sub(" ", text or "")
    return set(_NUMBER.findall(body)) | set(_ASCII_PROPER.findall(body)) | set(_KO_ORG.findall(body))


def _contains(token: str, haystack: str) -> bool:
    """쉼표는 표기 차이라 무시한다(2,564 ↔ 2564)."""
    return token.replace(",", "") in haystack.replace(",", "")


def anchor_orphan_tokens(
    text: str,
    refs: Sequence[Any],
    question: str,
    *,
    marker_for: Callable[[int], int | None],
) -> str:
    """마커 없는 문장에 그 값을 가진 발췌의 번호를 붙인다.

    고르는 문장은 **질문에 없던 숫자·고유명사**를 담은 것뿐이다. 질문의 낱말을 되쓴 문장은
    새 사실이 아니라 추적할 것이 없다. 그 토큰을 가장 많이 담은 발췌를 고르고, 어느 발췌도
    그 토큰을 갖고 있지 않으면 **문장을 그대로 둔다**(지우지 않는다).

    번호를 정하는 일은 부르는 쪽이 한다(`marker_for(ref_index)`). 발췌를 근거 목록에
    승격시킬지, 이미 있는 번호를 다시 쓸지는 조립 단계의 판단이라서다. `None` 을 돌려주면
    그 발췌는 쓰지 않는다.
    """
    if not text.strip() or not refs:
        return text

    question_tokens = _tokens(question)
    snippets = [normalize(getattr(ref, "snippet", "")) for ref in refs]
    grid = [split_sentences(line) for line in text.splitlines()]

    for i, sentences in enumerate(grid):
        for j, sentence in enumerate(sentences):
            if not sentence.strip() or surviving_markers(sentence):
                continue
            fresh = {t for t in _tokens(sentence) if t not in question_tokens}
            if not fresh:
                continue

            terms = content_terms(sentence)
            best: int | None = None
            best_key = (0, 0)
            for index, snippet in enumerate(snippets):
                hits = sum(1 for token in fresh if _contains(token, snippet))
                if not hits:
                    continue
                key = (hits, len(term_overlap(terms, snippet)))
                if key > best_key:
                    best, best_key = index, key
            if best is None:
                continue
            marker = marker_for(best)
            if marker is None:
                continue
            grid[i][j] = _append_marker(sentence, marker)

    return "\n".join("".join(sentences) for sentences in grid)


def orphan_token_sentences(text: str, question: str) -> list[str]:
    """마커가 없는데 질문에 없던 숫자·고유명사를 담은 문장. 추적이 끊긴 자리다."""
    question_tokens = _tokens(question)
    found: list[str] = []
    for line in (text or "").splitlines():
        for sentence in split_sentences(line):
            if not sentence.strip() or surviving_markers(sentence):
                continue
            if {t for t in _tokens(sentence) if t not in question_tokens}:
                found.append(sentence.strip())
    return found


class MarkerAllocator:
    """발췌 → 각주 번호. 이미 번호가 있으면 그것을 다시 쓰고, 없으면 새로 뗀다.

    번호와 발췌 목록 순서를 맞춰 둔다(`n` 번 각주 = evidence[n-1]). 모델이 본 프롬프트의
    번호 규칙이 그것이고, 승격된 발췌도 같은 규칙 안에 들어와야 화면이 헷갈리지 않는다.
    """

    def __init__(self, evidence: list[Any], citations: Sequence[Mapping[str, Any]]):
        self.evidence = evidence
        self.by_evidence: dict[str, int] = {
            str(c.get("evidence_id") or ""): c["marker"]
            for c in citations
            if isinstance(c.get("marker"), int)
        }
        self.added: list[dict[str, Any]] = []
        self.promoted: list[Any] = []

    def _register(self, evidence_id: str, marker: int) -> int:
        self.by_evidence[evidence_id] = marker
        self.added.append({"marker": marker, "evidence_id": evidence_id})
        return marker

    def existing(self, index: int) -> int | None:
        """이미 근거 목록에 있는 발췌의 번호."""
        ref = self.evidence[index]
        evidence_id = getattr(ref, "evidence_id", "") or ""
        if not evidence_id:
            return None
        marker = self.by_evidence.get(evidence_id)
        if marker is not None:
            return marker
        return self._register(evidence_id, index + 1)

    def promote(self, ref: Any) -> int | None:
        """원문 검색 발췌를 근거 목록 끝에 올리고 번호를 준다."""
        evidence_id = getattr(ref, "evidence_id", "") or ""
        if not evidence_id:
            return None
        marker = self.by_evidence.get(evidence_id)
        if marker is not None:
            return marker
        self.evidence.append(ref)
        self.promoted.append(ref)
        return self._register(evidence_id, len(self.evidence))


__all__ = [
    "anchor_citations",
    "anchor_orphan_tokens",
    "orphan_token_sentences",
    "MarkerAllocator",
    "MIN_OVERLAP",
]
