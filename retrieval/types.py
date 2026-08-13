"""A5 가 A6 에 넘기는 재료의 자료형.

Answer JSON 조립은 A6 몫이다. 여기서는 "무엇을 근거로 무엇을 찾았는가"만 담는다.
필드 이름은 `contracts/answer.schema.json` 과 맞춰 두어 A6 가 재가공 없이 옮길 수 있게 했다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GapVerdict(str, Enum):
    """REVISED §5 · A5 의 Product Gap 3값."""

    CONFIRMED = "CONFIRMED"
    POSSIBLE = "POSSIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EntityRef:
    """질문에 링크된 엔티티."""

    key: str
    labels: tuple[str, ...]
    name: str
    matched_text: str
    match_kind: str  # alias | graph_name | fulltext

    @property
    def primary_label(self) -> str:
        return self.labels[0] if self.labels else ""


@dataclass(frozen=True)
class EvidenceRef:
    """근거 한 조각. authority 는 라벨링 재료로만 들고 간다(1A 는 rerank 하지 않는다)."""

    evidence_id: str
    source_id: str
    locator: str
    snippet: str
    #: 자료 자체의 표기(파일 이름·채널 이름). 폴더 경로는 담지 않는다.
    #: 누구에게 낸 제안인지가 **파일 이름에만** 적힌 자료가 있어서, 본문만 보면 그 고객사
    #: 질문에서 한 건도 걸리지 않는다(실측: 소미생명 제안서 발췌 32건 중 '소미' 0건).
    source_label: str = ""
    source_type: str = ""
    sensitivity: str = ""
    source_of_record_for: str = ""
    doc_status: str = ""
    source_status: str = ""
    observed_at: str | None = None
    authored_at: str | None = None
    via: str = ""  # observation | claim | critical | template
    claim_ids: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = ()

    def to_contract(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "locator": self.locator,
            "snippet": self.snippet,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class RawSignal:
    """'추가 원문 근거'. 합성 인용과 섞지 않는다(REVISED §4 ⑦)."""

    evidence_id: str
    source_id: str
    locator: str
    snippet: str
    match_terms: tuple[str, ...]
    in_graph: bool
    score: float = 0.0

    def to_contract(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "locator": self.locator,
            "snippet": self.snippet,
            "match_terms": list(self.match_terms),
            "in_graph": self.in_graph,
        }


@dataclass(frozen=True)
class CriticalItem:
    """검색 후보에서 사라지면 안 되는 항목. 별도 경로로 합류시킨 것들이다."""

    kind: str  # Claim | Observation
    id: str
    statement: str
    status: str | None
    lane: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    about: tuple[str, ...] = ()


@dataclass(frozen=True)
class GapFinding:
    subject: str
    verdict: GapVerdict
    reason: str
    need_evidence_ids: list[str] = field(default_factory=list)
    capability_evidence_ids: list[str] = field(default_factory=list)
    explicit_absence_evidence_ids: list[str] = field(default_factory=list)
    out_of_scope_by_design: bool = False

    def to_contract(self) -> dict[str, Any]:
        basis: dict[str, Any] = {"reason": self.reason}
        if self.need_evidence_ids:
            basis["need_evidence_ids"] = list(self.need_evidence_ids)
        if self.capability_evidence_ids:
            basis["capability_evidence_ids"] = list(self.capability_evidence_ids)
        if self.explicit_absence_evidence_ids:
            basis["explicit_absence_evidence_ids"] = list(self.explicit_absence_evidence_ids)
        if self.out_of_scope_by_design:
            basis["out_of_scope_by_design"] = True
        return {"subject": self.subject, "verdict": self.verdict.value, "basis": basis}


@dataclass(frozen=True)
class SubgraphNode:
    id: str
    labels: tuple[str, ...]
    label_text: str
    rank: str  # focal | cited | supporting
    status: str | None = None

    def to_contract(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "labels": list(self.labels),
            "label_text": self.label_text,
            "rank": self.rank,
        }
        if self.status is not None:
            payload["status"] = self.status
        return payload


@dataclass(frozen=True)
class SubgraphEdge:
    source: str
    target: str
    type: str
    claim_ids: tuple[str, ...] = ()

    def to_contract(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "from": self.source,
            "to": self.target,
            "type": self.type,
        }
        if self.claim_ids:
            payload["claim_ids"] = list(self.claim_ids)
        return payload


@dataclass
class Subgraph:
    nodes: list[SubgraphNode] = field(default_factory=list)
    edges: list[SubgraphEdge] = field(default_factory=list)
    truncated: bool = False

    def to_contract(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_contract() for n in self.nodes],
            "edges": [e.to_contract() for e in self.edges],
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class RouteDecision:
    retriever: str  # Q-E | Q-M | Q-S
    stage: str  # template | signal | llm_fallback | default
    matched_rule: str
    template: str | None = None
    llm_classifier_used: bool = False
    signals: tuple[str, ...] = ()

    def to_contract(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "retriever": self.retriever,
            "matched_rule": self.matched_rule,
            "llm_classifier_used": self.llm_classifier_used,
        }
        return payload


@dataclass
class RetrievalResult:
    """A6 가 Answer 를 조립할 재료 한 벌."""

    question: str
    route: RouteDecision
    focal_entities: list[EntityRef] = field(default_factory=list)
    graph_evidence: list[EvidenceRef] = field(default_factory=list)
    raw_signals: list[RawSignal] = field(default_factory=list)
    #: 근거 id → 그 후보를 낸 곳(집계 행·critical 항목·원문 검색 자료).
    #: A6 가 마지막 24건을 고를 때 한 곳이 자리를 독식하지 않게 하는 재료다.
    evidence_groups: dict[str, str] = field(default_factory=dict)
    aggregates: dict[str, Any] = field(default_factory=dict)
    gaps: list[GapFinding] = field(default_factory=list)
    critical_items: list[CriticalItem] = field(default_factory=list)
    subgraph: Subgraph = field(default_factory=Subgraph)
    synthesis_input: dict[str, Any] = field(default_factory=dict)
    notices: dict[str, Any] = field(default_factory=dict)
