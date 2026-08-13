"""Product Gap 3값 판정 (REVISED §5 · §8 · A5).

    CONFIRMED  명시적 기능 부재 확인, 또는 Lost Deal / Objection 등 직접 Evidence 존재
    POSSIBLE   강한 Need Evidence 는 있으나 대응 Capability Evidence 미발견
    UNKNOWN    판단할 자료 자체가 부족

가장 중요한 규칙은 금지 규칙이다. **Need→Capability 연결이 없다는 것만으로 CONFIRMED 를
내지 않는다.** 그래프에 선이 없다는 것은 "제품에 없다"가 아니라 "우리가 아직 못 봤다"이다
(§8 상시 불변 원칙: absence of evidence 를 evidence of absence 로 취급하지 않는다).

CONFIRMED 가 성립하려면 세 가지가 동시에 있어야 한다.
    (1) 부재를 말하는 문장이 실제 발췌에 있고
    (2) 그 발췌가 **부재를 말할 자격이 있는 자료**에서 왔고
        (그 자료가 무엇의 정본인지 선언에 '기능 부재'·'제약'이 있거나, 제품 스펙 자료다)
    (3) 그 발췌가 지금 묻는 주제를 실제로 다루고 있다.
셋 중 하나라도 없으면 최대 POSSIBLE 이다.

"기술적으로는 가능하지만 핵심 목적이 아니다"는 기능 부재가 아니라 의도적 비범위다.
이건 CONFIRMED 를 막고 `out_of_scope_by_design` 으로 따로 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .text import content_terms, fold, term_overlap
from .types import EvidenceRef, GapFinding, GapVerdict

#: 제품·대응 쪽의 부재를 말하는 표현. 고객이 처한 상황("통제 못 함")은 여기 넣지 않는다.
#: 그 둘을 섞으면 Pain 진술이 곧 기능 부재로 둔갑한다.
ABSENCE_PHRASES: tuple[str, ...] = (
    "미지원",
    "미제공",
    "지원하지 않",
    "제공하지 않",
    "제공되지 않",
    "지원 불가",
    "제공 불가",
    "대응 불가",
    "기능이 없",
    "기능 부재",
    "구현되지 않",
    "개발 검토 중",
    "개발 예정",
    "불가 의견",
    "정책상 제한",
    "수정 불가",
    "삭제 불가",
    "표시 불가",
    "다운로드 불가",
    "지원 예정 없",
)

#: Lost Deal / Objection 등 '직접 Evidence' 쪽 표현.
DIRECT_LOSS_PHRASES: tuple[str, ...] = (
    "수주 실패",
    "탈락",
    "실주",
    "경쟁사로 결정",
    "도입 보류 사유",
)

#: 의도적 비범위. CONFIRMED 를 막는다.
OUT_OF_SCOPE_PHRASES: tuple[str, ...] = (
    "기술적으로는 가능",
    "핵심 목적이 아니",
    "핵심 목적은 아니",
    "시스템이 아닙니다",
    "플랫폼이 아닙니다",
    "영역이 아닙니다",
    "지향하지 않",
    "목적이 아닙니다",
    "범위가 아니",
)

#: 필요·불편을 말하는 표현. Need 노드로 정규화되지 않은 원문 근거도 "강한 Need Evidence" 로 본다.
#: 그래프에 Need 노드가 만들어졌는지를 조건으로 걸면, 추출이 놓친 순간 UNKNOWN 으로 떨어진다.
NEED_PHRASES: tuple[str, ...] = (
    "불편",
    "어려움",
    "어렵",
    "필요하",
    "요구",
    "요청",
    "못 함",
    "못함",
    "못하",
    "안 됨",
    "안됨",
    "산재",
    "누락",
    "부담",
    "리스크",
    "위험",
    "불가함",
    "pain",
    "이슈",
)

#: 무엇의 정본인지 선언(Source.source_of_record_for)에 이 말이 있으면 부재를 말할 자격이 있다.
ABSENCE_AUTHORISING_ROLES: tuple[str, ...] = ("기능 부재", "제약", "미지원", "한계", "대응 불가")

#: 제품 스펙 계열 자료는 선언 문구와 무관하게 부재를 말할 자격이 있다.
ABSENCE_AUTHORISING_SOURCE_TYPES: frozenset[str] = frozenset(
    {"release_spec", "product_doc", "product_brochure"}
)

#: 주제가 같다고 보려면 내용어가 최소 이만큼 겹쳐야 한다.
MIN_SUBJECT_OVERLAP = 2


@dataclass(frozen=True)
class GapEngine:
    min_subject_overlap: int = MIN_SUBJECT_OVERLAP

    # -- 판정 ------------------------------------------------------------
    def assess(
        self,
        subject: str,
        need_evidence: Sequence[EvidenceRef],
        capability_evidence: Sequence[EvidenceRef],
        scan_evidence: Sequence[EvidenceRef],
    ) -> GapFinding:
        terms = content_terms(subject)

        out_of_scope = self._out_of_scope_hits(scan_evidence, terms)
        absence = self._absence_hits(scan_evidence, terms)

        need_ids = [e.evidence_id for e in need_evidence]
        cap_ids = [e.evidence_id for e in capability_evidence]
        need_from_text = False
        if not need_ids:
            need_ids = self._need_hits(scan_evidence, terms)
            need_from_text = bool(need_ids)

        if out_of_scope:
            verdict = GapVerdict.POSSIBLE if need_ids else GapVerdict.UNKNOWN
            reason = (
                "자료가 '기술적으로는 가능하지만 핵심 목적이 아니다'라고 적었다. "
                "기능 부재가 아니라 의도적인 비범위로 본다."
            )
            return GapFinding(
                subject=subject,
                verdict=verdict,
                reason=reason,
                need_evidence_ids=need_ids,
                capability_evidence_ids=cap_ids,
                explicit_absence_evidence_ids=[],
                out_of_scope_by_design=True,
            )

        if absence:
            return GapFinding(
                subject=subject,
                verdict=GapVerdict.CONFIRMED,
                reason=(
                    "이 주제를 정본으로 다루는 자료가 부재를 직접 적었다. "
                    "연결이 없어서가 아니라 원문에 적혀 있어서 확정으로 본다."
                ),
                need_evidence_ids=need_ids,
                capability_evidence_ids=cap_ids,
                explicit_absence_evidence_ids=absence,
            )

        if need_ids:
            source_note = (
                "구조화된 Need 로는 잡히지 않았고 원문 발췌에서만 확인됐다. "
                if need_from_text
                else ""
            )
            return GapFinding(
                subject=subject,
                verdict=GapVerdict.POSSIBLE,
                reason=(
                    "필요를 말하는 근거는 있으나 대응하는 Capability 근거를 찾지 못했다. "
                    f"{source_note}부재를 적은 자료도 없으므로 확정하지 않는다."
                ),
                need_evidence_ids=need_ids,
                capability_evidence_ids=cap_ids,
            )

        return GapFinding(
            subject=subject,
            verdict=GapVerdict.UNKNOWN,
            reason="판단할 자료를 찾지 못했다. 있다/없다 어느 쪽으로도 답하지 않는다.",
            capability_evidence_ids=cap_ids,
        )

    # -- 내부 ------------------------------------------------------------
    def _on_subject(self, evidence: EvidenceRef, terms: Sequence[str]) -> bool:
        if not terms:
            return False
        hits = term_overlap(list(terms), evidence.snippet)
        return len(hits) >= min(self.min_subject_overlap, len(terms))

    def _absence_hits(
        self, evidence: Sequence[EvidenceRef], terms: Sequence[str]
    ) -> list[str]:
        out: list[str] = []
        for item in evidence:
            if not self._authorised_to_state_absence(item):
                continue
            if not _contains_any(item.snippet, ABSENCE_PHRASES + DIRECT_LOSS_PHRASES):
                continue
            if not self._on_subject(item, terms):
                continue
            out.append(item.evidence_id)
        return out

    def _need_hits(self, evidence: Sequence[EvidenceRef], terms: Sequence[str]) -> list[str]:
        return [
            item.evidence_id
            for item in evidence
            if _contains_any(item.snippet, NEED_PHRASES) and self._on_subject(item, terms)
        ]

    def _out_of_scope_hits(
        self, evidence: Sequence[EvidenceRef], terms: Sequence[str]
    ) -> list[str]:
        return [
            item.evidence_id
            for item in evidence
            if _contains_any(item.snippet, OUT_OF_SCOPE_PHRASES) and self._on_subject(item, terms)
        ]

    @staticmethod
    def _authorised_to_state_absence(evidence: EvidenceRef) -> bool:
        role = evidence.source_of_record_for or ""
        if any(marker in role for marker in ABSENCE_AUTHORISING_ROLES):
            return True
        return evidence.source_type in ABSENCE_AUTHORISING_SOURCE_TYPES


def _contains_any(text: str, phrases: Sequence[str]) -> bool:
    folded = fold(text)
    return any(fold(p) in folded for p in phrases)
