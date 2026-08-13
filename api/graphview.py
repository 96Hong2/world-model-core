"""Answer 조립에 필요한 읽기 전용 그래프 조회.

A5 의 `retrieval.store` 가 "무엇을 근거로 삼을까"를 본다면, 여기는 조립에 필요한 부수 정보만
본다 — 어떤 발췌가 어떤 엔티티에 걸려 있는가(각주 ↔ 노드 연결), 자료를 화면에 뭐라고
적을까, 노드 하나를 펼치면 무엇이 나오는가.

쓰기는 하지 않는다. 세션은 호출자가 연 읽기 세션을 그대로 쓴다.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from neo4j import Session

from retrieval.store import BUSINESS_EDGE_TYPES, DISPLAY_TEXT


def _display(var: str) -> str:
    return DISPLAY_TEXT % (var, var, var, var, var)


_ENTITIES_FOR_EVIDENCE = """
MATCH (k) WHERE (k:Observation OR k:Claim)
  AND any(eid IN coalesce(k.evidence_ids, []) WHERE eid IN $ids)
MATCH (k)-[:MENTIONS|ABOUT]->(x) WHERE x.natural_key IS NOT NULL
UNWIND coalesce(k.evidence_ids, []) AS eid
WITH eid, x WHERE eid IN $ids
RETURN eid AS evidence_id, collect(DISTINCT x.natural_key)[0..12] AS keys
"""


def entities_for_evidence(
    session: Session, evidence_ids: Sequence[str]
) -> dict[str, list[str]]:
    """발췌 하나가 어떤 엔티티에 대해 말하고 있는가. 각주를 노드에 붙일 때 쓴다."""
    ids = [e for e in dict.fromkeys(evidence_ids) if e]
    if not ids:
        return {}
    return {
        row["evidence_id"]: list(row["keys"] or [])
        for row in session.run(_ENTITIES_FOR_EVIDENCE, ids=ids)
    }


_EVIDENCE_EXTRA = """
MATCH (e:Evidence) WHERE e.evidence_id IN $ids
OPTIONAL MATCH (e)-[:FROM_SOURCE]->(s:Source)
RETURN e.evidence_id AS evidence_id,
       coalesce(e.masked, false) AS masked,
       s.extractor AS extractor,
       s.canonical_location AS location
"""


def evidence_extras(session: Session, evidence_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    ids = [e for e in dict.fromkeys(evidence_ids) if e]
    if not ids:
        return {}
    return {row["evidence_id"]: dict(row) for row in session.run(_EVIDENCE_EXTRA, ids=ids)}


_SOURCES = """
MATCH (s:Source) WHERE s.source_id IN $ids
RETURN s.source_id AS source_id, s.canonical_location AS location, s.format AS format
"""


def source_labels(session: Session, source_ids: Sequence[str]) -> dict[str, str]:
    """자료를 화면에 어떻게 적을지. 경로 대신 파일 이름만 쓴다."""
    ids = [s for s in dict.fromkeys(source_ids) if s]
    if not ids:
        return {}
    out: dict[str, str] = {}
    for row in session.run(_SOURCES, ids=ids):
        location = row["location"] or ""
        out[row["source_id"]] = os.path.basename(location) or row["source_id"]
    return out


_RESTRICTED_SOURCES = """
MATCH (s:Source) WHERE toLower(coalesce(s.sensitivity, '')) = 'restricted'
RETURN s.source_id AS source_id
"""


def restricted_source_ids(session: Session) -> set[str]:
    return {row["source_id"] for row in session.run(_RESTRICTED_SOURCES) if row["source_id"]}


_NEIGHBOURS = f"""
MATCH (x) WHERE x.natural_key = $key
MATCH (x)-[r]-(y)
WHERE type(r) IN $edge_types AND y.natural_key IS NOT NULL
RETURN startNode(r).natural_key AS source_key,
       endNode(r).natural_key AS target_key,
       type(r) AS type,
       y.natural_key AS other_key,
       labels(y)[0] AS other_label,
       {_display('y')} AS other_text,
       y.status AS other_status
ORDER BY type, other_text
LIMIT $limit
"""


def one_hop(session: Session, key: str, limit: int) -> list[dict[str, Any]]:
    if not key or limit <= 0:
        return []
    return [
        dict(row)
        for row in session.run(
            _NEIGHBOURS, key=key, edge_types=list(BUSINESS_EDGE_TYPES), limit=limit
        )
    ]


_CLAIMS_FOR_EVIDENCE = """
MATCH (c:Claim) WHERE any(eid IN coalesce(c.evidence_ids, []) WHERE eid IN $ids)
RETURN c.claim_id AS claim_id,
       c.statement AS statement,
       c.status AS status,
       coalesce(c.lane, []) AS lane,
       c.claim_kind AS claim_kind,
       [eid IN coalesce(c.evidence_ids, []) WHERE eid IN $ids] AS evidence_ids
ORDER BY CASE c.status
           WHEN 'VERIFIED' THEN 0 WHEN 'CRITICAL' THEN 1
           WHEN 'CANDIDATE' THEN 2 WHEN 'UNVERIFIED' THEN 3 ELSE 4 END,
         c.claim_id
LIMIT $limit
"""


def claims_for_evidence(
    session: Session, evidence_ids: Sequence[str], limit: int = 10
) -> list[dict[str, Any]]:
    """이 발췌들 위에 세워진 주장.

    발췌만 실으면 답변이 원문 복사에 머문다. 기능맵 한 줄에서 "2.0.0 버전부터"를 뽑아낸
    것처럼, 구조화 단계에서 만들어진 사실은 Claim 에만 있고 발췌 문장에는 없다.
    상태(VERIFIED·CANDIDATE·CRITICAL)를 그대로 달아 무엇이 검증된 것인지 구분한다.
    """
    ids = [e for e in dict.fromkeys(evidence_ids) if e]
    if not ids:
        return []
    return [dict(row) for row in session.run(_CLAIMS_FOR_EVIDENCE, ids=ids, limit=limit)]
