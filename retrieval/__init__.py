"""A5 — Retrieval 3종 + Router.

읽기 전용이다. 이 패키지 어디에도 쓰기 세션을 여는 코드가 없다.
Answer JSON 조립은 A6 몫이고, 여기서는 그 재료(`RetrievalResult`)까지만 만든다.
"""

from .access import DEMO_POLICY, AccessPolicy, apply_hard_filters
from .citations import strip_uncited_sentences, verify_citations
from .gaps import GapEngine
from .linking import EntityLinker
from .pool import CandidatePool
from .router import Router, llm_route_classifier
from .service import RetrievalService
from .subgraph import SubgraphBuilder
from .templates import CYPHER_TEMPLATES, UnknownTemplateError, run_template
from .text import content_terms, derive_gap_subject
from .types import (
    CriticalItem,
    EntityRef,
    EvidenceRef,
    GapFinding,
    GapVerdict,
    RawSignal,
    RetrievalResult,
    RouteDecision,
    Subgraph,
    SubgraphEdge,
    SubgraphNode,
)

__all__ = [
    "AccessPolicy",
    "CYPHER_TEMPLATES",
    "CandidatePool",
    "CriticalItem",
    "DEMO_POLICY",
    "EntityLinker",
    "EntityRef",
    "EvidenceRef",
    "GapEngine",
    "GapFinding",
    "GapVerdict",
    "RawSignal",
    "RetrievalResult",
    "RetrievalService",
    "RouteDecision",
    "Router",
    "Subgraph",
    "SubgraphBuilder",
    "SubgraphEdge",
    "SubgraphNode",
    "UnknownTemplateError",
    "apply_hard_filters",
    "content_terms",
    "derive_gap_subject",
    "llm_route_classifier",
    "run_template",
    "strip_uncited_sentences",
    "verify_citations",
]
