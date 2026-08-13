"""읽기 전용 그래프 접근.

질의 계층은 여기를 통해서만 Neo4j 를 본다. 이 모듈에는 판정이 없다 — 무엇을 근거로 삼을지,
무엇을 거를지는 상위(access·gaps·service)가 정한다.

DECISIONS A-1 이 요구하는 경유 경로를 여기서 구현한다.
    Event ↔ Account  : (Event)<-[:OBSERVED_IN]-(Observation)-[:MENTIONS]->(Account)
    Feature ↔ Product / Competitor ↔ Capability : (Claim)-[:ABOUT]->(Entity)
직접 엣지가 없다는 이유로 빈 결과를 돌려주지 않는다.
"""

from __future__ import annotations

from typing import Any, Sequence

from neo4j import Session

from graph.queries import escape_lucene, search_evidence_fulltext

from .text import term_overlap
from .types import CriticalItem, EvidenceRef

BUSINESS_EDGE_TYPES: tuple[str, ...] = (
    "BELONGS_TO",
    "HAS_NEED",
    "WITH_ACCOUNT",
    "FOR_PRODUCT",
    "BLOCKED_BY",
    "ADDRESSED_BY",
    "IMPLEMENTS",
    "VALIDATED_BY",
    "IN_DOMAIN",
    "TARGETS",
)

DISPLAY_TEXT = "coalesce(%s.canonical_name, %s.name, %s.title, %s.statement, %s.natural_key)"


def _display(var: str) -> str:
    return DISPLAY_TEXT % (var, var, var, var, var)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def source_label(location: str | None) -> str:
    """자료의 표기. 절대경로에서 파일 이름만 남긴다.

    폴더 경로는 버린다. 사람 이름이 든 홈 디렉토리가 답변 재료에 섞이지 않게 하려는 것이고,
    자료가 무엇인지 말해 주는 것은 마지막 조각뿐이기 때문이다. Slack 은 채널 파일 이름이
    그대로 남는다(`all-영업활동공유.jsonl`).
    """
    text = (location or "").strip()
    if not text:
        return ""
    return text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


_FETCH_EVIDENCE = """
MATCH (e:Evidence) WHERE e.evidence_id IN $ids
OPTIONAL MATCH (e)-[:FROM_SOURCE]->(s:Source)
RETURN e.evidence_id AS evidence_id,
       e.source_id AS source_id,
       e.locator AS locator,
       coalesce(e.snippet, e.excerpt, '') AS snippet,
       e.authored_at AS authored_at,
       coalesce(s.canonical_location, '') AS source_location,
       s.source_type AS source_type,
       s.sensitivity AS sensitivity,
       s.source_of_record_for AS source_of_record_for,
       s.doc_status AS doc_status,
       s.source_status AS source_status
"""


def fetch_evidence(session: Session, evidence_ids: Sequence[str]) -> dict[str, EvidenceRef]:
    ids = [e for e in dict.fromkeys(evidence_ids) if e]
    if not ids:
        return {}
    fetched: dict[str, EvidenceRef] = {}
    for row in session.run(_FETCH_EVIDENCE, ids=ids):
        fetched[row["evidence_id"]] = EvidenceRef(
            evidence_id=row["evidence_id"],
            source_id=row["source_id"] or "",
            locator=row["locator"] or "",
            snippet=row["snippet"] or "",
            source_label=source_label(row["source_location"]),
            source_type=row["source_type"] or "",
            sensitivity=row["sensitivity"] or "",
            source_of_record_for=row["source_of_record_for"] or "",
            doc_status=row["doc_status"] or "",
            source_status=row["source_status"] or "",
            authored_at=row["authored_at"],
        )
    # 요청한 순서로 돌려준다. 쿼리에 ORDER BY 가 없어 Neo4j 반환 순서는 정해져 있지 않고,
    # 그 순서가 상위 계층을 타고 답변 근거까지 흘러가므로 여기서 고정한다.
    #
    # 2026-08-13 에 한 번 고쳤다가 되돌렸다. 그때는 근거 24건 선별이 동률을 **후보가 들어온
    # 순서**로 갈랐고(`api/selection.py` `_fill` 의 seniority), 그 우연이 자료 다양성을
    # 떠받치고 있었다. 순서를 고정하자 GQ-D2 에서 한 자료가 근거 19/24 를 독점해 계약이
    # 깨졌다. 선별이 동률 안에서 곳·자료를 돌려 담게 바꾼 뒤에 다시 고정했다.
    return {eid: fetched[eid] for eid in ids if eid in fetched}


_IN_GRAPH = """
MATCH (e:Evidence) WHERE e.evidence_id IN $ids
RETURN e.evidence_id AS evidence_id,
       (EXISTS { MATCH (:Observation)-[:DERIVED_FROM]->(e) }
        OR EXISTS { MATCH (e)-[:SUPPORTS]->(:Claim) }) AS in_graph
"""


def evidence_in_graph(session: Session, evidence_ids: Sequence[str]) -> dict[str, bool]:
    """그 발췌가 이미 구조화된 지식으로 이어져 있는가."""
    ids = [e for e in dict.fromkeys(evidence_ids) if e]
    if not ids:
        return {}
    return {r["evidence_id"]: bool(r["in_graph"]) for r in session.run(_IN_GRAPH, ids=ids)}


# ---------------------------------------------------------------------------
# 1~2 hop 이웃
# ---------------------------------------------------------------------------

_OBSERVATIONS = f"""
MATCH (x) WHERE x.natural_key IN $keys
MATCH (o:Observation)-[:MENTIONS]->(x)
OPTIONAL MATCH (o)-[:OBSERVED_IN]->(ev:Event)
RETURN x.natural_key AS entity_key,
       {_display('x')} AS entity_text,
       labels(x)[0] AS entity_label,
       o.observation_id AS observation_id,
       o.statement AS statement,
       coalesce(o.evidence_ids, []) AS evidence_ids,
       ev.natural_key AS event_key,
       coalesce(ev.title, '') AS event_title,
       ev.event_type AS event_type,
       ev.observed_at AS event_at
ORDER BY coalesce(ev.observed_at, '') DESC, o.observation_id
LIMIT $limit
"""

_CLAIMS = f"""
MATCH (x) WHERE x.natural_key IN $keys
MATCH (c:Claim)-[:ABOUT]->(x)
RETURN x.natural_key AS entity_key,
       {_display('x')} AS entity_text,
       labels(x)[0] AS entity_label,
       c.claim_id AS claim_id,
       c.statement AS statement,
       c.status AS status,
       coalesce(c.lane, []) AS lane,
       c.claim_kind AS claim_kind,
       c.claim_domain AS claim_domain,
       coalesce(c.evidence_ids, []) AS evidence_ids
ORDER BY CASE c.status
           WHEN 'CRITICAL' THEN 0 WHEN 'VERIFIED' THEN 1
           WHEN 'CANDIDATE' THEN 2 ELSE 3 END,
         c.claim_id
LIMIT $limit
"""

# 이웃은 x 에서 뻗어 나가며 찾는다. `MATCH (a)-[r]->(b) WHERE a = x OR b = x` 로 쓰면
# 관계 전체를 훑는다 — 실측에서 이 한 줄이 Q-E 한 번에 46초를 썼다(관계 3.6만 개).
_NEIGHBOURS = f"""
MATCH (x) WHERE x.natural_key IN $keys
MATCH (x)-[r]-(other)
WHERE type(r) IN $edge_types AND other.natural_key IS NOT NULL
WITH DISTINCT r, startNode(r) AS a, endNode(r) AS b
RETURN a.natural_key AS source_key,
       labels(a)[0] AS source_label,
       {_display('a')} AS source_text,
       b.natural_key AS target_key,
       labels(b)[0] AS target_label,
       {_display('b')} AS target_text,
       type(r) AS type
ORDER BY type, source_key, target_key
LIMIT $limit
"""


def observations_for(
    session: Session, keys: Sequence[str], limit: int = 40
) -> list[dict[str, Any]]:
    if not keys:
        return []
    return [dict(r) for r in session.run(_OBSERVATIONS, keys=list(keys), limit=limit)]


def claims_for(session: Session, keys: Sequence[str], limit: int = 40) -> list[dict[str, Any]]:
    if not keys:
        return []
    return [dict(r) for r in session.run(_CLAIMS, keys=list(keys), limit=limit)]


def neighbours_for(
    session: Session, keys: Sequence[str], limit: int = 60
) -> list[dict[str, Any]]:
    if not keys:
        return []
    return [
        dict(r)
        for r in session.run(
            _NEIGHBOURS, keys=list(keys), edge_types=list(BUSINESS_EDGE_TYPES), limit=limit
        )
    ]


# ---------------------------------------------------------------------------
# critical 합류
# ---------------------------------------------------------------------------

_CRITICAL_CLAIMS = """
MATCH (c:Claim)-[:ABOUT]->(x)
WHERE x.natural_key IN $keys AND 'critical' IN coalesce(c.lane, [])
RETURN c.claim_id AS id,
       c.statement AS statement,
       c.status AS status,
       coalesce(c.lane, []) AS lane,
       coalesce(c.evidence_ids, []) AS evidence_ids,
       collect(DISTINCT x.natural_key) AS about
ORDER BY CASE c.status WHEN 'CRITICAL' THEN 0 WHEN 'UNVERIFIED' THEN 1 ELSE 2 END, c.claim_id
LIMIT $limit
"""

_CRITICAL_OBSERVATIONS = """
MATCH (o:Observation)-[:MENTIONS]->(x)
WHERE x.natural_key IN $keys
  AND ('critical' IN coalesce(o.lane, []) OR o.criticality IS NOT NULL)
RETURN o.observation_id AS id,
       o.statement AS statement,
       null AS status,
       coalesce(o.lane, []) AS lane,
       coalesce(o.evidence_ids, []) AS evidence_ids,
       collect(DISTINCT x.natural_key) AS about
ORDER BY o.observation_id
LIMIT $limit
"""


_BUSINESS_NEIGHBOUR_KEYS = """
MATCH (x) WHERE x.natural_key IN $keys
MATCH (x)-[r]-(n)
WHERE type(r) IN $edge_types
  AND n.natural_key IS NOT NULL
  AND NOT n.natural_key IN $keys
RETURN DISTINCT n.natural_key AS key
ORDER BY key
LIMIT $limit
"""


def business_neighbour_keys(
    session: Session, keys: Sequence[str], limit: int = 40
) -> list[str]:
    """비즈니스 엣지로 한 걸음 떨어진 엔티티의 natural_key.

    critical 합류가 focal 노드에만 붙은 항목만 찾으면, 한 칸 옆에 달린 것이 통째로 사라진다.
    실측: 가나손해보험 질문에서 CRITICAL Claim 넷이 전부 Account 가 아니라 그 Deal 에
    ABOUT 으로 붙어 있어, Account 키만 넘긴 Q-S 에서는 하나도 합류하지 못했다.
    """
    if not keys:
        return []
    return [
        r["key"]
        for r in session.run(
            _BUSINESS_NEIGHBOUR_KEYS,
            keys=list(keys),
            edge_types=list(BUSINESS_EDGE_TYPES),
            limit=limit,
        )
    ]


def critical_items(session: Session, keys: Sequence[str], limit: int = 20) -> list[CriticalItem]:
    """lane=critical 항목을 별도 경로로 끌어온다.

    이 경로가 없으면 미검증 CRITICAL 이 상위 K 밖으로 밀려 검색 결과에서 사라진다
    (REVISED §0 25행: "검색 후보에서 사라지지 않는 것"이 목적).
    """
    if not keys:
        return []
    out: list[CriticalItem] = []
    for kind, cypher in (("Claim", _CRITICAL_CLAIMS), ("Observation", _CRITICAL_OBSERVATIONS)):
        for row in session.run(cypher, keys=list(keys), limit=limit):
            out.append(
                CriticalItem(
                    kind=kind,
                    id=row["id"],
                    statement=row["statement"] or "",
                    status=row["status"],
                    lane=tuple(row["lane"] or ()),
                    evidence_ids=tuple(row["evidence_ids"] or ()),
                    about=tuple(row["about"] or ()),
                )
            )
    return out


# ---------------------------------------------------------------------------
# 원문 검색 (추가 원문 근거)
# ---------------------------------------------------------------------------


def fulltext_evidence(session: Session, query: str, k: int) -> list[dict[str, Any]]:
    return search_evidence_fulltext(session, query, k=k)


#: 전역 top-K 는 흔한 낱말이 많은 문서에 밀린다. 질문의 중심 엔티티를 다루는 자료 안에서
#: 한 번 더 찾아야 그 자료에 딱 한 번 적힌 사실이 살아남는다(Gate 1A 항목 4).
_FULLTEXT_IN_SOURCES = """
CALL db.index.fulltext.queryNodes('evidence_fulltext', $q, {limit: $scan}) YIELD node, score
WITH node, score WHERE node.source_id IN $source_ids
RETURN node.evidence_id AS evidence_id,
       node.source_id AS source_id,
       node.locator AS locator,
       coalesce(node.snippet, node.excerpt, '') AS snippet,
       score AS score
ORDER BY score DESC
LIMIT $k
"""

FULLTEXT_SCAN_DEPTH = 1200

_EVIDENCE_COUNT_BY_SOURCE = """
MATCH (e:Evidence) WHERE e.source_id IN $source_ids
RETURN e.source_id AS source_id, count(*) AS total
"""


def evidence_count_by_source(session: Session, source_ids: Sequence[str]) -> dict[str, int]:
    if not source_ids:
        return {}
    return {
        r["source_id"]: r["total"]
        for r in session.run(_EVIDENCE_COUNT_BY_SOURCE, source_ids=list(source_ids))
    }


def locator_section(locator: str) -> str:
    """발췌가 자료 안에서 속한 묶음. xlsx 는 시트 이름, 구분이 없는 자료는 빈 문자열."""
    text = locator or ""
    return text.split("!", 1)[0].strip() if "!" in text else ""


_EVIDENCE_COUNT_BY_SECTION = """
MATCH (e:Evidence)
WHERE e.source_id IN $source_ids AND e.locator CONTAINS '!'
WITH e.source_id AS source_id, split(e.locator, '!')[0] AS section, count(*) AS total
RETURN source_id, section, total
"""


def rare_sections(
    session: Session, source_ids: Sequence[str], ceiling: int
) -> set[tuple[str, str]]:
    """자료 **안에서** 발췌가 `ceiling` 이하뿐인 묶음(xlsx 시트).

    자료 단위 rarity 로는 발췌 79건짜리 워크북 안의 두 줄짜리 시트를 못 잡는다. 그 시트에
    적힌 사실은 코퍼스 전체에서 그 두 줄이 전부라, 큰 자료와 같은 잣대로 자르면 매번 밀린다
    (PRD 1A AC-6). 실측: `BD Overview!Guest통제` 2행 · `영업활동일지!6월15일(월)` 1행.
    """
    ids = [s for s in dict.fromkeys(source_ids) if s]
    if not ids:
        return set()
    return {
        (row["source_id"], row["section"])
        for row in session.run(_EVIDENCE_COUNT_BY_SECTION, source_ids=ids)
        if 0 < row["total"] <= ceiling
    }


def rare_sources(session: Session, source_ids: Sequence[str], ceiling: int) -> set[str]:
    """발췌가 `ceiling` 이하뿐인 자료들.

    이런 자료에 적힌 사실은 코퍼스 전체에서 그 몇 줄이 전부다(REVISED §5 의 "1회 등장 정보").
    상위 계층이 이 표시를 보고 자르지 않는다.
    """
    totals = evidence_count_by_source(session, [s for s in dict.fromkeys(source_ids) if s])
    return {source_id for source_id, total in totals.items() if 0 < total <= ceiling}

_SOURCES_FOR_ENTITIES = """
MATCH (x) WHERE x.natural_key IN $keys
MATCH (k)-[:MENTIONS|ABOUT]->(x)
WHERE k:Observation OR k:Claim
UNWIND coalesce(k.evidence_ids, []) AS eid
MATCH (e:Evidence {evidence_id: eid})
RETURN e.source_id AS source_id, count(*) AS hits
ORDER BY hits DESC, source_id
LIMIT $limit
"""


def sources_for_entities(
    session: Session, keys: Sequence[str], limit: int = 12
) -> list[str]:
    """질문의 중심 엔티티를 실제로 다루는 자료들.

    근거 풀은 상한에 잘리지만 '어느 자료가 이 엔티티를 다루는가'는 잘리면 안 된다.
    잘린 풀만 보고 원문 재검색을 하면, 자료 하나에 딱 한 번 적힌 사실을 영영 못 찾는다.
    """
    if not keys:
        return []
    return [
        r["source_id"]
        for r in session.run(_SOURCES_FOR_ENTITIES, keys=list(keys), limit=limit)
        if r["source_id"]
    ]


def fulltext_evidence_in_sources(
    session: Session, query: str, source_ids: Sequence[str], k: int
) -> list[dict[str, Any]]:
    if not query.strip() or not source_ids or k <= 0:
        return []
    return [
        dict(r)
        for r in session.run(
            _FULLTEXT_IN_SOURCES,
            q=escape_lucene(query),
            source_ids=list(dict.fromkeys(source_ids)),
            scan=FULLTEXT_SCAN_DEPTH,
            k=k,
        )
    ]


#: 이름으로 자료를 찾을 때 훑는 깊이. 자료 목록만 뽑으므로 발췌 순위와 달리 넉넉해도 싸다.
SOURCE_DISCOVERY_SCAN = 300

_SOURCES_MENTIONING = """
CALL db.index.fulltext.queryNodes('evidence_fulltext', $q, {limit: $scan}) YIELD node, score
RETURN node.source_id AS source_id, max(score) AS best
ORDER BY best DESC
LIMIT $limit
"""


def sources_mentioning(
    session: Session, names: Sequence[str], limit: int = 12
) -> list[str]:
    """이름이 원문에 나오는 자료들.

    `sources_for_entities` 는 Observation·Claim 을 통해서만 자료를 찾는다. 추출이 아무것도
    만들지 못한 문서는 그 경로에 아예 안 걸린다. 실측: 하늘IT 검토 메모 7건은 Observation 이
    하나도 없어서 후보 자료 목록에서 빠졌고, 그 안에만 있는 멀티테넌트 전제가 사라졌다.
    """
    query = " ".join(n for n in names if n).strip()
    if not query:
        return []
    return [
        r["source_id"]
        for r in session.run(
            _SOURCES_MENTIONING,
            q=escape_lucene(query),
            scan=SOURCE_DISCOVERY_SCAN,
            limit=limit,
        )
        if r["source_id"]
    ]


_SOURCE_LOCATIONS = """
MATCH (s:Source)
WHERE $source_ids IS NULL OR s.source_id IN $source_ids
RETURN s.source_id AS source_id, coalesce(s.canonical_location, '') AS location
"""


def source_labels(session: Session, source_ids: Sequence[str] | None = None) -> dict[str, str]:
    """자료 id → 그 자료의 표기(파일 이름). `source_ids` 가 없으면 전부.

    자료는 279개뿐이라 전수를 읽어도 싸다. 이름 대조는 파이썬에서 한다 — Cypher `CONTAINS`
    로 하면 영문 낱말 경계를 못 봐서 `AG` 가 다른 낱말 속 글자열에 걸린다.
    """
    ids = list(dict.fromkeys(s for s in (source_ids or []) if s)) or None
    return {
        r["source_id"]: source_label(r["location"])
        for r in session.run(_SOURCE_LOCATIONS, source_ids=ids)
        if r["source_id"]
    }


def sources_named(
    session: Session, names: Sequence[str], source_ids: Sequence[str] | None = None
) -> list[str]:
    """이름이 **자료의 표기**에 있는 자료들.

    `sources_mentioning` 은 본문을 뒤진다. 그런데 누구에게 낸 제안인지가 파일 이름에만 적힌
    자료가 있다. 실측(2026-08-13): 소미생명 제안서는 발췌 32건 어디에도 회사 이름이
    없어서, 그 회사를 물으면 후보 풀에 한 건도 들어오지 못했다(풀 21건 중 0건).

    겹침 판정은 `term_overlap` 을 그대로 쓴다. 파일 이름은 낱말 사이 구분자가 적어서 영문
    짧은 이름이 우연히 박히기 쉬운데, 그 함수가 영문에만 낱말 경계를 요구한다.
    """
    wanted = [n for n in names if n and n.strip()]
    if not wanted:
        return []
    labels = source_labels(session, source_ids)
    return [sid for sid, label in labels.items() if label and term_overlap(wanted, label)]


_EVIDENCE_OF_SOURCES = """
MATCH (e:Evidence) WHERE e.source_id IN $source_ids
RETURN e.evidence_id AS evidence_id,
       e.source_id AS source_id,
       e.locator AS locator,
       coalesce(e.snippet, e.excerpt, '') AS snippet
ORDER BY e.source_id, e.locator, e.evidence_id
LIMIT $limit
"""


def evidence_of_sources(
    session: Session, source_ids: Sequence[str], limit: int = 400
) -> list[dict[str, Any]]:
    """작은 자료들을 통째로 읽는다. 여러 자료를 한 번에 묻는다(스캔을 한 번으로 줄인다).

    fulltext 는 전역 상위 N 을 먼저 자르고 그다음에 자료로 거른다. 발췌가 7천 건인 Slack 이
    그 N 을 다 채우면 발췌 7건짜리 메모는 순위표에 오르지도 못한다. 작은 자료는 통째로 읽어
    상위 계층이 직접 고르게 한다.
    """
    ids = [s for s in dict.fromkeys(source_ids) if s]
    if not ids:
        return []
    return [dict(r) for r in session.run(_EVIDENCE_OF_SOURCES, source_ids=ids, limit=limit)]


# ---------------------------------------------------------------------------
# 집계 재료
# ---------------------------------------------------------------------------

_SOURCES = """
MATCH (s:Source)
RETURN s.source_id AS source_id, s.sensitivity AS sensitivity,
       s.doc_status AS doc_status, s.source_status AS source_status,
       s.source_type AS source_type
"""


def source_metadata(session: Session) -> list[dict[str, Any]]:
    return [dict(r) for r in session.run(_SOURCES)]


_BD_OVERVIEW = """
MATCH (bd:BusinessDomain)
WHERE size($terms) = 0 OR any(t IN $terms WHERE toLower(bd.name) CONTAINS t)
OPTIONAL MATCH (bd)-[:TARGETS]->(ind:Industry)
WITH bd, [x IN collect(DISTINCT ind.name) WHERE x IS NOT NULL] AS industries
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(bd)
WITH bd, industries, collect(cl.evidence_ids) AS nested
RETURN bd.bd_id AS bd_id, bd.name AS name, bd.bd_status AS bd_status,
       bd.industry_scope AS industry_scope, bd.channel_type AS channel_type,
       bd.guest_control AS guest_control, bd.market_size_note AS market_size_note,
       industries AS industries,
       reduce(acc = [], l IN nested | acc + coalesce(l, []))[0..6] AS evidence_ids
ORDER BY name
LIMIT $limit
"""

# 범위는 두 갈래로 잡는다. 질문의 낱말과 겹치는 Need, 그리고 질문의 중심 엔티티에서
# 실제로 도달하는 Need(고객사 직접 · 딜을 통한 BusinessDomain 경유).
_NEED_SCOPE = """
MATCH (a:Account)-[:HAS_NEED]->(n:Need)
WHERE n.name IS NOT NULL
OPTIONAL MATCH (a)<-[:WITH_ACCOUNT]-(d:Deal)-[:IN_DOMAIN]->(bd:BusinessDomain)
WITH n, a, collect(DISTINCT bd.natural_key) AS scope_keys
WHERE size($terms) = 0 AND size($keys) = 0
   OR any(t IN $terms WHERE toLower(n.name) CONTAINS t)
   OR any(t IN $terms WHERE toLower(coalesce(n.definition, '')) CONTAINS t)
   OR a.natural_key IN $keys
   OR n.natural_key IN $keys
   OR any(k IN scope_keys WHERE k IN $keys)
OPTIONAL MATCH (n)-[:ADDRESSED_BY]->(c:Capability)
WITH n, collect(DISTINCT a.canonical_name) AS accounts,
     [x IN collect(DISTINCT c.name) WHERE x IS NOT NULL] AS capabilities
OPTIONAL MATCH (o:Observation)-[:MENTIONS]->(n)
WITH n, accounts, capabilities, collect(o.evidence_ids) AS nested
RETURN n.need_id AS need_id, n.name AS need, accounts AS accounts,
       size(accounts) AS account_count, capabilities AS capabilities,
       size(capabilities) AS capability_count,
       reduce(acc = [], l IN nested | acc + coalesce(l, [])) AS evidence_ids
ORDER BY account_count DESC, need
LIMIT $limit
"""

_ACCOUNT_DEAL_SCOPE = """
MATCH (d:Deal)-[:WITH_ACCOUNT]->(a:Account)
OPTIONAL MATCH (d)-[:IN_DOMAIN]->(scope:BusinessDomain)
WITH d, a, collect(scope.natural_key) AS scope_keys
WHERE size($keys) = 0
   OR a.natural_key IN $keys
   OR d.natural_key IN $keys
   OR any(k IN scope_keys WHERE k IN $keys)
OPTIONAL MATCH (d)-[:IN_DOMAIN]->(bd:BusinessDomain)
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(d)
WITH a, d, collect(DISTINCT bd.name) AS domains, collect(cl.evidence_ids) AS nested
RETURN a.canonical_name AS account, d.natural_key AS deal, d.stage_value AS stage,
       d.outcome AS outcome, d.observed_at AS observed_at,
       [x IN domains WHERE x IS NOT NULL] AS domains,
       reduce(acc = [], l IN nested | acc + coalesce(l, [])) AS evidence_ids
ORDER BY account, deal
LIMIT $limit
"""

# Capability 의 근거를 Feature 쪽에서만 모으면 수요 쪽 원문이 통째로 빠진다.
# 실측: '기업 공식 카카오 채널화' 는 Feature 가 0개라 근거 0건이었는데, 그 Capability 가
# 대응하는 Need 의 관측에는 Pain 레지스트리 원문이 그대로 붙어 있었다.
# 그래서 "이 Capability 가 대응하는 Need 를 적어 둔 발췌"도 같은 행의 근거로 싣는다.
_CAPABILITY_SCOPE = """
MATCH (c:Capability)
WHERE size($terms) = 0 OR any(t IN $terms WHERE toLower(c.name) CONTAINS t)
OPTIONAL MATCH (n:Need)-[:ADDRESSED_BY]->(c)
OPTIONAL MATCH (o:Observation)-[:MENTIONS]->(n)
WITH c, [x IN collect(DISTINCT n.name) WHERE x IS NOT NULL] AS needs,
     collect(o.evidence_ids) AS need_nested
OPTIONAL MATCH (f:Feature)-[:IMPLEMENTS]->(c)
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(f)
WITH c, needs, need_nested, count(DISTINCT f) AS feature_count,
     collect(cl.evidence_ids) AS nested
RETURN c.capability_id AS capability_id, c.name AS name, c.addon AS addon,
       feature_count AS feature_count, needs AS needs,
       reduce(acc = [], l IN nested | acc + coalesce(l, []))[0..6]
       + reduce(acc = [], l IN need_nested | acc + coalesce(l, []))[0..6] AS evidence_ids
ORDER BY feature_count DESC, name
LIMIT $limit
"""

_COMPETITOR_SCOPE = """
MATCH (c:Competitor)
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(c)
WITH c, collect(cl.evidence_ids) AS nested
RETURN c.name AS name, c.competitor_kind AS kind,
       reduce(acc = [], l IN nested | acc + coalesce(l, [])) AS evidence_ids
ORDER BY name
LIMIT $limit
"""

_INDUSTRY_SCOPE = """
MATCH (ind:Industry)
WHERE size($terms) = 0 OR any(t IN $terms WHERE toLower(ind.name) CONTAINS t)
OPTIONAL MATCH (a:Account)-[:BELONGS_TO]->(ind)
OPTIONAL MATCH (bd:BusinessDomain)-[:TARGETS]->(ind)
RETURN ind.name AS name, count(DISTINCT a) AS account_count,
       [x IN collect(DISTINCT bd.name) WHERE x IS NOT NULL] AS domains
ORDER BY account_count DESC, name
LIMIT $limit
"""


def business_domains(
    session: Session, terms: Sequence[str], limit: int = 25
) -> list[dict[str, Any]]:
    return [dict(r) for r in session.run(_BD_OVERVIEW, terms=list(terms), limit=limit)]


def industries(session: Session, terms: Sequence[str], limit: int = 15) -> list[dict[str, Any]]:
    return [dict(r) for r in session.run(_INDUSTRY_SCOPE, terms=list(terms), limit=limit)]


def needs_in_scope(
    session: Session,
    terms: Sequence[str],
    keys: Sequence[str] = (),
    limit: int = 20,
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in session.run(_NEED_SCOPE, terms=list(terms), keys=list(keys), limit=limit)
    ]


def accounts_and_deals(
    session: Session, keys: Sequence[str], limit: int = 25
) -> list[dict[str, Any]]:
    return [dict(r) for r in session.run(_ACCOUNT_DEAL_SCOPE, keys=list(keys), limit=limit)]


def capabilities_in_scope(
    session: Session, terms: Sequence[str], limit: int = 20
) -> list[dict[str, Any]]:
    return [dict(r) for r in session.run(_CAPABILITY_SCOPE, terms=list(terms), limit=limit)]


def competitors(session: Session, limit: int = 10) -> list[dict[str, Any]]:
    return [dict(r) for r in session.run(_COMPETITOR_SCOPE, limit=limit)]


# ---------------------------------------------------------------------------
# 이름 → 노드 키
# ---------------------------------------------------------------------------

#: 집계 행은 서로를 **이름 문자열**로 가리킨다(need 행의 `capabilities`, capability 행의 `needs`).
#: 그 이름이 좁혀진 상대 집계에 없으면 노드 키를 알 수 없어 관계를 걸 수 없다. 같은 이름이
#: 여러 노드에 있을 수 있으므로 canonical(id 가 붙은 것)을 먼저 세운다.
_NAME_TO_KEY = """
MATCH (n:%(label)s) WHERE n.name IS NOT NULL AND n.natural_key IS NOT NULL
RETURN n.name AS name, n.natural_key AS key, n.%(id_field)s IS NOT NULL AS canonical
ORDER BY canonical DESC, key
"""

_NAME_TO_KEY_LABELS: dict[str, str] = {"Need": "need_id", "Capability": "capability_id"}


def keys_by_name(session: Session, label: str) -> dict[str, str]:
    """`label` 노드의 이름 → natural_key. 라벨은 화이트리스트로 제한한다."""
    id_field = _NAME_TO_KEY_LABELS.get(label)
    if not id_field:
        raise ValueError(f"이름으로 키를 찾을 수 있는 라벨이 아니다: {label}")
    out: dict[str, str] = {}
    for row in session.run(_NAME_TO_KEY % {"label": label, "id_field": id_field}):
        out.setdefault(row["name"], row["key"])
    return out
