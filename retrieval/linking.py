"""엔티티 링킹 — 질문 안의 이름을 그래프의 노드에 붙인다.

두 경로만 쓴다(REVISED §4 ④ 의 ER 원칙을 질의 쪽에도 그대로 적용).
  1. 사전·그래프 이름의 **정규화 후 포함 일치**. 유사도·부분 문자열 추측 병합은 하지 않는다.
  2. 그래도 못 찾으면 Evidence fulltext 로 문서를 먼저 찾고, 그 문서가 낳은 Observation 이
     가리키는 엔티티를 후보로 삼는다.

`config/aliases.yaml` 은 읽기만 한다.
"""

from __future__ import annotations

import functools
import pathlib
from dataclasses import dataclass
from typing import Any, Sequence

import yaml
from neo4j import Session

from graph.queries import search_evidence_fulltext

from .router import asks_domain_status, domain_statuses_in
from .text import content_terms, fold, normalize
from .types import EntityRef

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ALIASES_PATH = REPO_ROOT / "config" / "aliases.yaml"

#: 이보다 짧은 이름은 질문 아무 데나 걸린다("기타"·"공공"·"TM"). 링킹 대상에서 뺀다.
MIN_NAME_CHARS = 3

#: 한 질문에서 focal 로 삼는 최대 엔티티 수. 너무 많으면 subgraph 가 초점을 잃는다.
MAX_FOCAL = 4

#: 이름을 조각내는 구분자. "유통/세일즈" 처럼 두 이름이 한 칸에 든 경우가 있다.
_NAME_SPLITTERS = ("/", "·", "(", ")", ",")

LEXICON_LABELS: tuple[tuple[str, str, str | None], ...] = (
    ("Account", "canonical_name", "raw_names"),
    ("BusinessDomain", "name", "aliases"),
    ("Capability", "name", None),
    ("Need", "name", None),
    ("Feature", "name", None),
    ("Product", "name", None),
    ("Competitor", "name", None),
    ("Industry", "name", None),
)

#: focal 우선순위. 질문의 중심이 될 확률이 높은 순.
LABEL_PRIORITY = {
    "Account": 0,
    "BusinessDomain": 1,
    "Feature": 2,
    "Capability": 3,
    "Product": 4,
    "Competitor": 5,
    "Need": 6,
    "Industry": 7,
}


@dataclass(frozen=True)
class LexiconEntry:
    key: str
    label: str
    name: str
    surface: str  # 매칭에 쓴 표기

    @property
    def folded(self) -> str:
        return fold(self.surface)


@functools.lru_cache(maxsize=1)
def alias_variants() -> dict[str, str]:
    """`config/aliases.yaml` 의 표기 변형 → canonical 이름."""
    data = yaml.safe_load(ALIASES_PATH.read_text(encoding="utf-8")) or {}
    index: dict[str, str] = {}
    for entry in data.get("accounts") or []:
        canonical = entry.get("canonical")
        if not canonical:
            continue
        for variant in entry.get("variants") or []:
            index[fold(variant)] = canonical
    return index


def _surfaces(name: str, extra: Sequence[str] | None) -> list[str]:
    out: list[str] = []
    for candidate in [name, *(extra or [])]:
        if not candidate:
            continue
        text = normalize(candidate)
        out.append(text)
        for splitter in _NAME_SPLITTERS:
            if splitter in text:
                out.extend(part.strip() for part in text.split(splitter))
    return [s for s in out if len(s) >= MIN_NAME_CHARS]


def load_lexicon(session: Session) -> list[LexiconEntry]:
    """그래프에서 링킹 사전을 만든다. 노드가 정본이고 aliases.yaml 이 보강한다."""
    entries: list[LexiconEntry] = []
    seen: set[tuple[str, str]] = set()

    for label, name_prop, alias_prop in LEXICON_LABELS:
        alias_select = f"n.{alias_prop} AS aliases" if alias_prop else "null AS aliases"
        rows = session.run(
            f"MATCH (n:{label}) WHERE n.{name_prop} IS NOT NULL "
            f"RETURN n.natural_key AS key, n.{name_prop} AS name, {alias_select}"
        ).data()
        for row in rows:
            for surface in _surfaces(row["name"], row.get("aliases")):
                marker = (label, fold(surface))
                if marker in seen:
                    continue
                seen.add(marker)
                entries.append(
                    LexiconEntry(
                        key=row["key"], label=label, name=row["name"], surface=surface
                    )
                )

    variants = alias_variants()
    canonical_index = {
        fold(e.name): e for e in entries if e.label == "Account" and fold(e.name) == e.folded
    }
    for folded_variant, canonical in variants.items():
        target = canonical_index.get(fold(canonical))
        if target is None or len(folded_variant) < MIN_NAME_CHARS:
            continue
        marker = ("Account", folded_variant)
        if marker in seen:
            continue
        seen.add(marker)
        entries.append(
            LexiconEntry(
                key=target.key, label="Account", name=target.name, surface=folded_variant
            )
        )

    # 긴 이름부터 본다. "하늘IT" 가 "하늘" 보다 먼저 잡혀야 한다.
    entries.sort(key=lambda e: (-len(e.folded), e.label, e.name))
    return entries


class EntityLinker:
    def __init__(self, session_factory, max_focal: int = MAX_FOCAL):
        self._session_factory = session_factory
        self._max_focal = max_focal
        self._lexicon: list[LexiconEntry] | None = None

    def lexicon(self, session: Session) -> list[LexiconEntry]:
        if self._lexicon is None:
            self._lexicon = load_lexicon(session)
        return self._lexicon

    def link(self, session: Session, question: str) -> list[EntityRef]:
        # 이름이 아니라 **상태로** 대상을 가리키는 질문("BD 중 제외로 분류된 것은?")은
        # 이름 사전으로 잡히지 않는다. 원문 검색에 맡기면 '제외' 가 프로젝트 범위 제외로
        # 잡혀 엉뚱한 대상이 나온다. 상태는 속성이므로 속성으로 특정한다.
        by_status = self._link_domains_by_status(session, question)
        if by_status:
            return by_status[: self._max_focal]

        folded_question = fold(question)
        hits: list[EntityRef] = []
        claimed: list[str] = []

        for entry in self.lexicon(session):
            if not entry.folded or entry.folded not in folded_question:
                continue
            # 이미 잡힌 더 긴 이름에 포함되는 표기는 건너뛴다("하늘" vs "하늘IT").
            if any(entry.folded in taken for taken in claimed):
                continue
            claimed.append(entry.folded)
            hits.append(
                EntityRef(
                    key=entry.key,
                    labels=(entry.label,),
                    name=entry.name,
                    matched_text=entry.surface,
                    match_kind="graph_name",
                )
            )

        hits.sort(key=lambda e: (LABEL_PRIORITY.get(e.primary_label, 9), -len(e.matched_text)))
        if hits:
            return hits[: self._max_focal]
        return self._link_by_fulltext(session, question)

    def _link_domains_by_status(self, session: Session, question: str) -> list[EntityRef]:
        if not asks_domain_status(question):
            return []
        statuses = domain_statuses_in(question)
        if not statuses:
            return []
        rows = session.run(
            "MATCH (bd:BusinessDomain) WHERE bd.bd_status IN $statuses "
            "RETURN bd.natural_key AS key, bd.name AS name, bd.bd_status AS status "
            "ORDER BY bd.name",
            statuses=statuses,
        ).data()
        return [
            EntityRef(
                key=row["key"],
                labels=("BusinessDomain",),
                name=row["name"] or row["key"],
                matched_text=row["status"] or "",
                match_kind="graph_attribute",
            )
            for row in rows
            if row.get("key")
        ]

    def _link_by_fulltext(self, session: Session, question: str) -> list[EntityRef]:
        terms = content_terms(question)
        if not terms:
            return []
        rows = search_evidence_fulltext(session, " ".join(terms), k=8)
        evidence_ids = [r["evidence_id"] for r in rows if r.get("evidence_id")]
        if not evidence_ids:
            return []

        found = session.run(
            "MATCH (o:Observation)-[:MENTIONS]->(x) "
            "WHERE any(eid IN o.evidence_ids WHERE eid IN $ids) "
            "RETURN labels(x)[0] AS label, x.natural_key AS key, "
            "       coalesce(x.canonical_name, x.name) AS name, count(*) AS hits "
            "ORDER BY hits DESC, name LIMIT $limit",
            ids=evidence_ids,
            limit=self._max_focal,
        ).data()
        return [
            EntityRef(
                key=row["key"],
                labels=(row["label"],),
                name=row["name"] or row["key"],
                matched_text="",
                match_kind="fulltext",
            )
            for row in found
            if row.get("key")
        ]
