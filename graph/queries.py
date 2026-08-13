"""공용 저수준 그래프 헬퍼.

여기에는 비즈니스 규칙을 두지 않는다. 적재 판정(무엇을 Observation/Claim 으로 볼지),
근거 필수 여부, 민감도 필터 같은 것은 전부 상위 계층 몫이다.
이 모듈이 보장하는 것은 두 가지뿐이다.
  - MERGE 는 (라벨, natural_key) 로만 한다 → 같은 입력을 두 번 넣어도 노드가 하나다.
  - 라벨·관계 타입은 계약(contracts/ontology.schema.json)의 closed set 밖이면 거부한다.
    (Cypher 는 라벨을 파라미터로 못 받기 때문에 문자열을 그대로 넣으면 주입 통로가 된다.)
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Iterable, Sequence

from neo4j import Session

_ONTOLOGY_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "contracts" / "ontology.schema.json"
)
_ONTOLOGY = json.loads(_ONTOLOGY_PATH.read_text(encoding="utf-8"))

NODE_LABELS: tuple[str, ...] = tuple(
    _ONTOLOGY["x-labels"]["business"] + _ONTOLOGY["x-labels"]["knowledge"]
)
RELATIONSHIP_TYPES: tuple[str, ...] = tuple(
    _ONTOLOGY["x-relations"]["knowledge_backbone"] + _ONTOLOGY["x-relations"]["business"]
)

EVIDENCE_FULLTEXT_INDEX = "evidence_fulltext"
OBSERVATION_FULLTEXT_INDEX = "observation_fulltext"

# Lucene 질의 문법으로 해석되면 안 되는 문자들. 사용자 질문을 그대로 넣기 때문에 이스케이프한다.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def escape_lucene(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


def _label(label: str) -> str:
    if label not in NODE_LABELS:
        raise ValueError(f"온톨로지에 없는 라벨: {label!r} (허용: {', '.join(NODE_LABELS)})")
    return label


def _relationship(rel_type: str) -> str:
    if rel_type not in RELATIONSHIP_TYPES:
        raise ValueError(
            f"온톨로지에 없는 관계 타입: {rel_type!r} (허용: {', '.join(RELATIONSHIP_TYPES)})"
        )
    return rel_type


# ---------------------------------------------------------------------------
# MERGE
# ---------------------------------------------------------------------------


def merge_node(
    session: Session,
    label: str,
    natural_key: str,
    properties: dict[str, Any] | None = None,
) -> str:
    """(라벨, natural_key) 로 upsert 하고 element id 를 돌려준다."""
    if not natural_key:
        raise ValueError("natural_key 는 비어 있을 수 없다(MERGE 키다).")
    props = dict(properties or {})
    props.pop("natural_key", None)

    record = session.run(
        f"MERGE (n:{_label(label)} {{natural_key: $key}}) SET n += $props RETURN elementId(n) AS id",
        key=natural_key,
        props=props,
    ).single()
    return record["id"]


def merge_edge(
    session: Session,
    rel_type: str,
    start: tuple[str, str],
    end: tuple[str, str],
    properties: dict[str, Any] | None = None,
) -> str:
    """(시작 라벨, natural_key) → (끝 라벨, natural_key) 관계를 upsert 한다.

    양 끝 노드는 이미 있어야 한다. 없으면 조용히 넘기지 않고 LookupError 를 낸다.
    """
    start_label, start_key = start
    end_label, end_key = end
    record = session.run(
        f"MATCH (a:{_label(start_label)} {{natural_key: $start_key}}) "
        f"MATCH (b:{_label(end_label)} {{natural_key: $end_key}}) "
        f"MERGE (a)-[r:{_relationship(rel_type)}]->(b) "
        "SET r += $props RETURN elementId(r) AS id",
        start_key=start_key,
        end_key=end_key,
        props=dict(properties or {}),
    ).single()
    if record is None:
        raise LookupError(
            f"관계 {rel_type} 의 양 끝 노드를 찾지 못했다: "
            f"({start_label}, {start_key}) → ({end_label}, {end_key})"
        )
    return record["id"]


def fetch_node(session: Session, label: str, natural_key: str) -> dict[str, Any] | None:
    record = session.run(
        f"MATCH (n:{_label(label)} {{natural_key: $key}}) RETURN properties(n) AS props",
        key=natural_key,
    ).single()
    return record["props"] if record else None


# ---------------------------------------------------------------------------
# fulltext 검색
# ---------------------------------------------------------------------------


def _fulltext(
    session: Session, index_name: str, query: str, k: int, returns: Sequence[str]
) -> list[dict[str, Any]]:
    if not query or not query.strip():
        return []
    if k <= 0:
        return []
    projection = ", ".join(returns)
    rows = session.run(
        "CALL db.index.fulltext.queryNodes($index, $q, {limit: $k}) YIELD node, score "
        f"RETURN {projection}, score",
        index=index_name,
        q=escape_lucene(query),
        k=k,
    ).data()
    return rows


def search_evidence_fulltext(session: Session, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Evidence 발췌 원문 검색. 그래프에 구조화되지 않은 정보를 되찾는 경로다."""
    return _fulltext(
        session,
        EVIDENCE_FULLTEXT_INDEX,
        query,
        k,
        [
            "node.evidence_id AS evidence_id",
            "node.natural_key AS natural_key",
            "node.source_id AS source_id",
            "node.locator AS locator",
            "coalesce(node.snippet, node.excerpt) AS snippet",
        ],
    )


def search_observation_fulltext(session: Session, query: str, k: int = 10) -> list[dict[str, Any]]:
    return _fulltext(
        session,
        OBSERVATION_FULLTEXT_INDEX,
        query,
        k,
        [
            "node.observation_id AS observation_id",
            "node.natural_key AS natural_key",
            "node.statement AS statement",
            "node.evidence_ids AS evidence_ids",
        ],
    )
