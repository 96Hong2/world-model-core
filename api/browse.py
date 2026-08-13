"""질문하지 않고 회사 지식을 둘러보는 조회 라우트.

    GET /browse/overview        둘러보기 첫 화면
    GET /browse/entities        종류별 Entity 목록
    GET /browse/entity/{key}    Entity 상세 + 주변 관계도 + 근거
    GET /browse/sources         서재 목록
    GET /browse/source/{sid}    자료 상세 + 여기서 나온 지식 + 원문 발췌
    GET /browse/runs            수집 실행 목록
    GET /browse/run/{run_id}    실행 상세 (자료별 타임라인 + 새로 생긴 관계)
    GET /browse/element         노드·관계 하나의 상세
    GET /browse/health          데이터 상태
    POST /browse/open-source    자료 원본 파일을 이 컴퓨터의 기본 앱으로 연다

**답변 경로와 완전히 분리된다.** `api/service.py` 의 Retrieval·Answer 로직을 건드리지 않고,
읽기 전용 세션으로 그래프를 직접 조회한다. 계약은 `web/src/api/browse.ts` 가 정본이다.

민감도 필터는 답변 경로와 같은 기준을 쓴다 — `viewer` 계정은 restricted 자료를 못 본다.
필터를 여기서 다시 만들지 않고 `AccessPolicy.allowed_sensitivity` 를 그대로 본다.

관계도는 Answer 계약의 subgraph 모양(`nodes`/`edges`, edge 는 `from`/`to`)으로 낸다.
그래야 화면이 graph/adapter.ts 와 GraphCanvas 를 그대로 쓴다.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Sequence

from fastapi import APIRouter, Body, Header, HTTPException, Query
from neo4j import Session

from graph.connection import ReadOnlyGraph
from retrieval.store import BUSINESS_EDGE_TYPES, DISPLAY_TEXT

# --------------------------------------------------------------------------- 표기

#: 화면에 적을 한국어 이름. 온톨로지 라벨은 식별자라 그대로 두고 옆에 붙인다.
LABEL_KO: dict[str, str] = {
    "BusinessDomain": "사업영역",
    "Account": "고객사",
    "Need": "요구",
    "Capability": "역량",
    "Feature": "기능",
    "Industry": "산업",
    "Product": "제품",
    "Claim": "주장",
    "Source": "자료",
    "Observation": "관찰",
    "Evidence": "근거",
    "Event": "이벤트",
    "Competitor": "경쟁사",
    "Persona": "페르소나",
    "Deal": "거래",
    "Test": "테스트",
}

EDGE_KO: dict[str, str] = {
    "BELONGS_TO": "소속",
    "HAS_NEED": "요구를 가짐",
    "WITH_ACCOUNT": "고객사",
    "FOR_PRODUCT": "대상 제품",
    "ADDRESSED_BY": "대응 역량",
    "IMPLEMENTS": "구현",
    "IN_DOMAIN": "사업영역",
    "TARGETS": "대상 산업",
    "MENTIONS": "언급",
    "ABOUT": "대상",
    "SUPPORTS": "뒷받침",
    "DERIVED_FROM": "출처 발췌",
    "FROM_SOURCE": "원본 자료",
    "OBSERVED_IN": "관측된 자리",
}

#: 자료를 서재에서 묶는 갈래. source_type 하나하나를 필터로 늘어놓으면 22개가 되어 못 쓴다.
SOURCE_GROUPS: dict[str, str] = {
    "release_spec": "제품",
    "product_doc": "제품",
    "product_brochure": "제품",
    "user_manual": "제품",
    "architecture_spec": "엔지니어링",
    "repo_doc": "엔지니어링",
    "code": "엔지니어링",
    "test": "엔지니어링",
    "glossary": "엔지니어링",
    "sales_activity_log": "영업·BD",
    "sales_weekly_plan": "영업·BD",
    "bd_registry": "영업·BD",
    "bd_registry_aux": "영업·BD",
    "bd_openbook": "영업·BD",
    "proposal": "제안",
    "internal_analysis": "시장·전략",
    "internal_memo": "시장·전략",
    "internal_deck": "시장·전략",
    "pain_registry": "고객",
    "customer_internal_report": "고객",
    "ai_eval_dataset": "고객",
    "compliance_checklist": "고객",
    "slack_thread": "슬랙",
}

SOURCE_TYPE_KO: dict[str, str] = {
    "release_spec": "기능맵(제품 기능 정본)",
    "sales_activity_log": "영업 활동일지",
    "sales_weekly_plan": "주간 영업 계획",
    "bd_registry": "BD 레지스트리(사업영역 정본)",
    "bd_registry_aux": "BD 분류 보조 문서",
    "pain_registry": "고객사 Pain Point 관리대장",
    "bd_openbook": "BD 오픈북",
    "proposal": "제안서",
    "product_brochure": "제품 소개 자료",
    "user_manual": "사용자 매뉴얼",
    "internal_memo": "내부 검토 메모",
    "internal_analysis": "내부 분석 자료",
    "internal_deck": "내부 발표 자료",
    "customer_internal_report": "고객사 내부 문서",
    "slack_thread": "슬랙 쓰레드",
    "repo_doc": "저장소 문서",
    "product_doc": "구축·설정 가이드",
    "glossary": "용어집",
    "code": "소스 코드",
    "test": "테스트 코드",
    "compliance_checklist": "법규 점검표",
    "ai_eval_dataset": "AI 품질 평가 기록",
    "architecture_spec": "아키텍처 명세",
}

PROP_KO: dict[str, str] = {
    "name": "이름",
    "canonical_name": "대표 이름",
    "aliases": "다른 표기",
    "raw_names": "자료에 적힌 표기",
    "account_kind": "고객 구분",
    "industry_scope": "산업 구분",
    "target_company": "타겟 기업",
    "target_company_detail": "타겟 기업 상세",
    "br_role": "BR 역할",
    "guest_role": "Guest 역할",
    "guest_control": "Guest 통제",
    "channel_type": "채널 유형",
    "work_desc": "업무 내용",
    "expected_effects": "기대 효과",
    "market_size_note": "시장 규모 메모",
    "partners": "파트너",
    "maturity": "성숙도",
    "bd_status": "사업 단계",
    "status": "상태",
    "statement": "내용",
    "claim_kind": "주장 종류",
    "claim_domain": "주장 유형",
    "lane": "처리 갈래",
    "stage": "단계",
    "amount": "금액",
    "created_at": "처음 들어온 시각",
    "updated_at": "마지막으로 손댄 시각",
    "pipeline_run_id": "수집 실행",
    "source_of_record_for": "이 자료가 정본인 것",
    "canonical_location": "원본 위치",
    "source_type": "자료 종류",
    "sensitivity": "민감도",
    "visibility": "공개 범위",
    "doc_status": "문서 상태",
    "content_hash": "내용 지문",
    "modified_at": "원본 수정 시각",
    "origin": "출처 구분",
    "needs_human_confirm": "사람 확인 필요",
}

#: 화면에 그대로 내보내지 않는 내부 값. 상세 속성표에서 뺀다.
HIDDEN_PROPS = {"natural_key", "evidence_ids", "source_ids", "claim_ids", "excerpt_hash"}

#: 둘러보기에서 훑을 수 있는 종류. Evidence·Observation 은 목록으로 훑을 것이 아니라
#: 근거로 딸려 나오는 것이라 넣지 않는다.
EXPLORE_TYPES: dict[str, str] = {
    "domain": "BusinessDomain",
    "account": "Account",
    "need": "Need",
    "capability": "Capability",
    "feature": "Feature",
    "product": "Product",
    "industry": "Industry",
    "deal": "Deal",
    "competitor": "Competitor",
}

GATE_WHY: dict[str, str] = {
    "domain": "회사가 어떤 사업 영역을 개척하고 있고 각각 어디까지 왔는지 봅니다.",
    "account": "고객사·파트너마다 어떤 요구와 거래가 기록돼 있는지 봅니다.",
    "need": "여러 고객사에서 반복되는 요구를 찾습니다. 제품 우선순위의 근거입니다.",
    "capability": "요구에 대응하는 우리 역량이 무엇인지, 어떤 기능이 그것을 구현하는지 봅니다.",
    "feature": "제품 기능이 어떤 역량·요구와 이어져 있는지 봅니다.",
    "product": "제품 단위로 무엇이 묶여 있는지 봅니다.",
    "industry": "산업별로 어떤 사업영역이 겨냥하고 있는지 봅니다.",
    "deal": "실제 거래가 어느 단계에 있고 무엇이 걸려 있는지 봅니다.",
    "competitor": "자료에 등장한 경쟁 상대를 봅니다.",
}

_DISPLAY = DISPLAY_TEXT
_EDGE_LIST = list(BUSINESS_EDGE_TYPES)

#: 목록 한 번에 내보내는 최대 건수. 화면이 더 달라고 하면 offset 으로 이어 받는다.
MAX_LIMIT = 200

#: 자료 상세·관계도가 훑을 엔티티 라벨. 라벨 없이 `MATCH (n) WHERE $sid IN n.source_ids` 로
#: 쓰면 노드 3만 개를 통째로 훑어 자료 한 건 여는 데 11초가 걸린다(실측).
_ENTITY_LABEL_FILTER = " OR ".join(f"n:{label}" for label in EXPLORE_TYPES.values()) + " OR n:Persona"

_SAFE_KEY = re.compile(r"^[\w\-.:/#가-힣 ()\[\]&+,·']{1,300}$")


def _display(var: str) -> str:
    return _DISPLAY % (var, var, var, var, var)


def _first_label(labels: Iterable[str]) -> str:
    for label in labels:
        if label in LABEL_KO:
            return label
    return next(iter(labels), "Unknown")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " · ".join(str(v) for v in value if v not in (None, ""))
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)


def _props(raw: dict[str, Any]) -> list[dict[str, str]]:
    """상세 화면 속성표. 내부 값은 빼고, 이름을 아는 것부터 위로 올린다."""
    out: list[dict[str, str]] = []
    known = [k for k in PROP_KO if k in raw]
    rest = sorted(k for k in raw if k not in PROP_KO and k not in HIDDEN_PROPS)
    for key in known + rest:
        if key in HIDDEN_PROPS:
            continue
        value = _text(raw.get(key))
        if not value:
            continue
        out.append({"key": key, "key_ko": PROP_KO.get(key, key), "value": value})
    return out


def _counts(pairs: Iterable[tuple[str, int]]) -> list[dict[str, Any]]:
    return [
        {"label": label, "label_ko": LABEL_KO.get(label, label), "count": count}
        for label, count in pairs
        if count
    ]


def _open_target(location: str | None, where: str = "") -> dict[str, Any]:
    """원본으로 가는 길. 브라우저가 열 수 없는 경로에 링크를 만들지 않는다."""
    path = location or ""
    return {
        "kind": "viewer" if path else "path",
        "path": path,
        "where": where,
        "reason": (
            "브라우저가 로컬 파일을 직접 열 수 없어 월드모델 안에서 그 위치를 펼칩니다."
            if path
            else "원본 위치가 기록돼 있지 않습니다."
        ),
    }


def _subgraph(
    nodes: Sequence[dict[str, Any]], edges: Sequence[dict[str, Any]], truncated: bool = False
) -> dict[str, Any]:
    return {"nodes": list(nodes), "edges": list(edges), "truncated": truncated}


def _node(
    key: str, labels: Sequence[str], text: str, rank: str, status: str | None = None
) -> dict[str, Any]:
    return {
        "id": key,
        "labels": list(labels) or ["Unknown"],
        "label_text": text or key,
        "rank": rank,
        "status": status,
    }


# --------------------------------------------------------------------------- 조회


_OVERVIEW_DOMAINS = f"""
MATCH (bd:BusinessDomain)
OPTIONAL MATCH (bd)<-[:IN_DOMAIN]-(d:Deal)
OPTIONAL MATCH (d)-[:WITH_ACCOUNT]->(a:Account)
OPTIONAL MATCH (a)-[:HAS_NEED]->(n:Need)
WITH bd,
     count(DISTINCT d) AS deals,
     count(DISTINCT a) AS accounts,
     count(DISTINCT n) AS needs
RETURN bd.natural_key AS id,
       {_display('bd')} AS name,
       bd.bd_status AS status,
       bd.maturity AS maturity,
       bd.industry_scope AS industry_scope,
       accounts AS account_count,
       needs AS need_count,
       deals AS deal_count
ORDER BY deals DESC, accounts DESC, name
"""

_OVERVIEW_COUNTS = """
MATCH (n) WHERE n.natural_key IS NOT NULL OR n:Evidence
WITH labels(n)[0] AS label, count(*) AS c
RETURN label, c ORDER BY c DESC
"""

_OVERVIEW_TOTALS = """
MATCH (n) WITH count(n) AS nodes
MATCH ()-[r]->() WITH nodes, count(r) AS edges
OPTIONAL MATCH (s:Source) WHERE s.created_at IS NOT NULL
RETURN nodes, edges, max(s.created_at) AS last_ingest_at
"""


#: 사업영역별 근거 수. apoc 없이 순수 Cypher 로 센다(컨테이너에 플러그인을 깔지 않는다).
_DOMAIN_EVIDENCE_PLAIN = """
MATCH (bd:BusinessDomain)
OPTIONAL MATCH (k)-[:MENTIONS|ABOUT]->(bd)
WHERE k:Observation OR k:Claim
WITH bd, collect(k.evidence_ids) AS nested
WITH bd, reduce(acc = [], l IN nested | acc + coalesce(l, [])) AS ids
UNWIND (CASE WHEN size(ids) = 0 THEN [null] ELSE ids END) AS eid
RETURN bd.natural_key AS id, count(DISTINCT eid) AS c
"""


#: 실행 목록·실행 상세 응답. `pipeline_run_id` 에 인덱스가 없어 매번 노드 3만·관계 3.7만을
#: 훑는다(실측 12초대). 스냅샷 한 벌이라 응답 자체를 담아 둔다.
_OVERVIEW_CACHE: dict[str, Any] | None = None
_RUNS_CACHE: dict[str, Any] | None = None
_RUN_CACHE: dict[str, dict[str, Any]] = {}


def warm(graph: ReadOnlyGraph) -> None:
    """무거운 집계표를 미리 만든다.

    자료별 지식 수와 자료별 증가량은 노드 3만 개를 한 번 훑어야 나온다(첫 호출 실측 40초대).
    데모에서 서재·변경 화면을 처음 누른 사람이 그 시간을 기다리게 두지 않는다.
    스냅샷 한 벌을 읽는 구조라 한 번 만들면 그대로 쓴다.

    실패해도 서버는 뜬다 — 그때는 화면이 처음 한 번 느리게 열릴 뿐이다.
    """
    try:
        with graph.session() as session:
            _source_links(session, [])
            _step_counts(session)
    except Exception as exc:  # noqa: BLE001 - 준비 실패가 서버 기동을 막지 않게 한다
        print(f"[browse] 집계표 예열 실패(화면 첫 열기가 느려질 수 있습니다): {exc}")


def warm_lists(fetch_overview, fetch_entities) -> None:
    """둘러보기 첫 화면과 종류별 목록 첫 페이지를 미리 만든다.

    natural_key 에 인덱스가 없어 종류마다 첫 페이지가 10초대다(실측). 데모에서 메뉴를
    처음 누른 사람이 그 시간을 보게 두지 않는다.
    """
    try:
        fetch_overview(None)
    except Exception as exc:  # noqa: BLE001
        print(f"[browse] 둘러보기 첫 화면 예열 실패: {exc}")
    for slug in EXPLORE_TYPES:
        try:
            fetch_entities(slug, "", "", "", 50, 0, None)
        except Exception as exc:  # noqa: BLE001
            print(f"[browse] {slug} 목록 예열 실패: {exc}")


def warm_runs(fetch_runs, fetch_run) -> None:
    """실행 목록·상세 응답도 미리 만든다.

    `pipeline_run_id` 에 인덱스가 없어 실행 상세 한 번이 12초대다(실측). 라우터 함수를
    그대로 받아 한 번 호출해 응답 캐시를 채운다.
    """
    try:
        listed = fetch_runs(None)
        for item in listed.get("runs", []):
            fetch_run(item["run_id"], None)
    except Exception as exc:  # noqa: BLE001
        print(f"[browse] 실행 기록 예열 실패: {exc}")


def build_router(graph: ReadOnlyGraph, resolve) -> APIRouter:
    """조회 라우터. `resolve` 는 main.py 의 계정 판정 함수를 그대로 받는다."""

    router = APIRouter(prefix="/browse", tags=["browse"])

    def allowed(account) -> frozenset[str]:
        return account.policy.allowed_sensitivity

    def blocked_sources(session: Session, account) -> set[str]:
        """이 계정이 못 보는 자료. 답변 경로와 같은 기준(sensitivity)이다."""
        allow = allowed(account)
        rows = session.run(
            "MATCH (s:Source) WHERE s.sensitivity IS NOT NULL "
            "RETURN s.source_id AS sid, toLower(s.sensitivity) AS sens"
        )
        return {row["sid"] for row in rows if (row["sens"] or "internal") not in allow}

    # ------------------------------------------------------------------ overview

    @router.get("/overview")
    def overview(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        account = resolve(authorization)
        global _OVERVIEW_CACHE
        if _OVERVIEW_CACHE is None:
            with graph.session() as session:
                domains = [dict(r) for r in session.run(_OVERVIEW_DOMAINS)]
                ev = {r["id"]: r["c"] or 0 for r in session.run(_DOMAIN_EVIDENCE_PLAIN)}
                counts = [(r["label"], r["c"]) for r in session.run(_OVERVIEW_COUNTS)]
                totals = session.run(_OVERVIEW_TOTALS).single()

            for d in domains:
                d["evidence_count"] = ev.get(d["id"], 0)

            _OVERVIEW_CACHE = {
                "domains": domains,
                "counts": _counts(counts),
                "total_nodes": totals["nodes"] if totals else 0,
                "total_edges": totals["edges"] if totals else 0,
                "last_ingest_at": totals["last_ingest_at"] if totals else None,
            }

        # hidden_sources 는 계정마다 다르다. 스냅샷 캐시에 넣으면 예열 계정(demo 는 전부
        # 허용이라 0)의 값이 서버 재시작 전까지 모든 계정에 재생돼, 제한 계정이 자기가
        # 못 보는 자료의 존재를 알 수 없게 된다. Source 270여 행 질의라 매 요청 세도 싸다.
        with graph.session() as session:
            hidden = len(blocked_sources(session, account))
        return {**_OVERVIEW_CACHE, "hidden_sources": hidden}

    # ------------------------------------------------------------------ entities

    @router.get("/entities")
    def entities(
        type: str = Query(default="account"),
        q: str = Query(default=""),
        domain: str = Query(default=""),
        status: str = Query(default=""),
        limit: int = Query(default=50),
        offset: int = Query(default=0),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        label = EXPLORE_TYPES.get(type)
        if not label:
            raise HTTPException(
                status_code=400,
                detail=f"둘러볼 수 없는 종류입니다. 쓸 수 있는 값: {', '.join(EXPLORE_TYPES)}",
            )
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)

        # 사업영역으로 좁힐 때 따라가는 경로는 종류마다 다르다.
        domain_clause = ""
        if domain:
            if label == "Account":
                domain_clause = (
                    "AND EXISTS { MATCH (x)<-[:WITH_ACCOUNT]-(:Deal)-[:IN_DOMAIN]->"
                    "(bd:BusinessDomain) WHERE bd.name = $domain }"
                )
            elif label == "Need":
                domain_clause = (
                    "AND EXISTS { MATCH (x)<-[:HAS_NEED]-(:Account)<-[:WITH_ACCOUNT]-"
                    "(:Deal)-[:IN_DOMAIN]->(bd:BusinessDomain) WHERE bd.name = $domain }"
                )
            elif label == "Deal":
                domain_clause = (
                    "AND EXISTS { MATCH (x)-[:IN_DOMAIN]->(bd:BusinessDomain) "
                    "WHERE bd.name = $domain }"
                )
            elif label == "BusinessDomain":
                domain_clause = "AND x.name = $domain"

        status_clause = "AND coalesce(x.status, x.bd_status, '') = $status" if status else ""

        list_query = f"""
        MATCH (x:{label}) WHERE x.natural_key IS NOT NULL
          AND ($q = '' OR toLower({_display('x')}) CONTAINS toLower($q))
          {domain_clause}
          {status_clause}
        WITH x, {_display('x')} AS name
        RETURN x.natural_key AS id,
               labels(x) AS labels,
               name AS name,
               coalesce(x.work_desc, x.statement, x.industry_scope, x.account_kind, '')
                 AS subtitle,
               coalesce(x.status, x.bd_status) AS status,
               count {{ (x)--() }} AS degree
        ORDER BY degree DESC, name
        SKIP $offset LIMIT $limit
        """

        count_query = f"""
        MATCH (x:{label}) WHERE x.natural_key IS NOT NULL
          AND ($q = '' OR toLower({_display('x')}) CONTAINS toLower($q))
          {domain_clause}
          {status_clause}
        RETURN count(x) AS total
        """

        with graph.session() as session:
            params = {"q": q, "domain": domain, "status": status, "limit": limit, "offset": offset}
            rows = [dict(r) for r in session.run(list_query, **params)]
            total = session.run(count_query, **params).single()["total"]
            keys = [r["id"] for r in rows]
            extra = _entity_extras(session, label, keys)
            facets = _facets(session, label)

        items = []
        for r in rows:
            info = extra.get(r["id"], {})
            items.append(
                {
                    "id": r["id"],
                    "type": _first_label(r["labels"]),
                    "name": r["name"] or r["id"],
                    "subtitle": _text(r["subtitle"]),
                    "status": r["status"],
                    "evidence_count": info.get("evidence_count", 0),
                    "domains": info.get("domains", []),
                    "degree": r["degree"],
                }
            )
        return {"total": total, "items": items, "facets": facets}

    # ------------------------------------------------------------------ entity

    @router.get("/entity/{key:path}")
    def entity(key: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if not _SAFE_KEY.match(key):
            raise HTTPException(status_code=400, detail="대상 식별자 형식이 아닙니다.")
        account = resolve(authorization)

        with graph.session() as session:
            row = session.run(
                f"""
                MATCH (x) WHERE x.natural_key = $key
                RETURN labels(x) AS labels, properties(x) AS props, {_display('x')} AS name
                """,
                key=key,
            ).single()
            if row is None:
                raise HTTPException(status_code=404, detail="그런 대상이 지식 월드에 없습니다.")

            label = _first_label(row["labels"])
            name = row["name"] or key
            props = dict(row["props"])

            related = _related(session, label, key)
            claims = _claims_about(session, label, key)
            blocked = blocked_sources(session, account)
            evidence = _evidence_for(
                session,
                [eid for c in claims for eid in c["evidence_ids"]],
                blocked,
                limit=24,
            )
            # 주장이 없는 대상도 관찰로 언급될 수 있다. 그쪽 근거도 함께 본다.
            if len(evidence) < 12:
                mentioned = session.run(
                    f"""
                    MATCH (x:{label}) WHERE x.natural_key = $key
                    MATCH (o:Observation)-[:MENTIONS]->(x)
                    RETURN o.evidence_ids AS ids LIMIT 40
                    """,
                    key=key,
                )
                ids: list[str] = []
                for r in mentioned:
                    ids.extend(r["ids"] or [])
                have = {e["evidence_id"] for e in evidence}
                extra = _evidence_for(session, ids, blocked, limit=24 - len(evidence))
                evidence += [e for e in extra if e["evidence_id"] not in have]

            sources = _sources_of(session, props.get("source_ids") or [], blocked)
            sub = _neighbour_subgraph(session, key, label, name, props)

        summary = _text(
            props.get("work_desc")
            or props.get("statement")
            or props.get("expected_effects")
            or props.get("industry_scope")
            or ""
        )

        return {
            "id": key,
            "type": label,
            "type_ko": LABEL_KO.get(label, label),
            "name": name,
            "summary": summary,
            "status": props.get("status") or props.get("bd_status"),
            "properties": _props(props),
            "claims": claims,
            "evidence": evidence,
            "sources": sources,
            "related": related,
            "subgraph": sub,
        }

    # ------------------------------------------------------------------ sources

    @router.get("/sources")
    def sources(
        q: str = Query(default=""),
        group: str = Query(default=""),
        source_type: str = Query(default=""),
        domain: str = Query(default=""),
        account: str = Query(default=""),
        limit: int = Query(default=60),
        offset: int = Query(default=0),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        acct = resolve(authorization)
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)

        # 그룹은 source_type 묶음이라 서버에서 타입 목록으로 바꿔 넘긴다.
        group_types = [t for t, g in SOURCE_GROUPS.items() if g == group] if group else []

        with graph.session() as session:
            blocked = blocked_sources(session, acct)
            rows = [
                dict(r)
                for r in session.run(
                    """
                    MATCH (s:Source)
                    WHERE ($q = '' OR toLower(coalesce(s.canonical_location, '')) CONTAINS toLower($q)
                           OR toLower(coalesce(s.source_of_record_for, '')) CONTAINS toLower($q)
                           OR toLower(s.source_id) CONTAINS toLower($q))
                      AND ($types = [] OR s.source_type IN $types)
                      AND ($source_type = '' OR s.source_type = $source_type)
                    RETURN s.source_id AS source_id,
                           s.source_type AS source_type,
                           s.canonical_location AS canonical_location,
                           s.source_of_record_for AS description,
                           s.created_at AS ingested_at,
                           s.sensitivity AS sensitivity,
                           count { (:Evidence)-[:FROM_SOURCE]->(s) } AS evidence_count
                    ORDER BY evidence_count DESC, s.source_id
                    """,
                    q=q,
                    types=group_types,
                    source_type=source_type,
                )
            ]
            rows = [r for r in rows if r["source_id"] not in blocked]
            links = _source_links(session, [r["source_id"] for r in rows])
            facets = _source_facets(session, blocked)

        items = []
        for r in rows:
            link = links.get(r["source_id"], {})
            if domain and domain not in link.get("domains", []):
                continue
            if account and account not in link.get("accounts", []):
                continue
            items.append(_source_row(r, link))

        total = len(items)
        return {"total": total, "items": items[offset : offset + limit], "facets": facets}

    # ------------------------------------------------------------------ source

    @router.get("/source/{source_id}")
    def source(source_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        acct = resolve(authorization)
        with graph.session() as session:
            blocked = blocked_sources(session, acct)
            if source_id in blocked:
                raise HTTPException(
                    status_code=403,
                    detail="이 자료는 지금 계정으로 볼 수 없습니다(민감도 제한).",
                )
            row = session.run(
                """
                MATCH (s:Source) WHERE s.source_id = $sid
                RETURN properties(s) AS props,
                       count { (:Evidence)-[:FROM_SOURCE]->(s) } AS evidence_count
                """,
                sid=source_id,
            ).single()
            if row is None:
                raise HTTPException(status_code=404, detail="그런 자료가 없습니다.")

            props = dict(row["props"])
            links = _source_links(session, [source_id]).get(source_id, {})
            preview = [
                {
                    "locator": r["locator"] or "",
                    "where": r["locator"] or "",
                    "text": r["snippet"] or "",
                }
                for r in session.run(
                    """
                    MATCH (e:Evidence)-[:FROM_SOURCE]->(s:Source {source_id: $sid})
                    RETURN e.locator AS locator,
                           coalesce(e.snippet, e.excerpt, '') AS snippet
                    ORDER BY e.locator
                    LIMIT 400
                    """,
                    sid=source_id,
                )
            ]
            # 이 자료에서 뽑아낸 지식 수는 이미 만들어 둔 표에서 꺼낸다(전체 스캔을 다시 하지 않는다).
            step = _step_counts(session).get(source_id, {"added": [], "touched": []})
            merged: dict[str, int] = {}
            for label, count in step["added"] + step["touched"]:
                merged[label] = merged.get(label, 0) + count
            extracted = _counts(sorted(merged.items(), key=lambda kv: -kv[1]))
            claims = [
                {
                    "claim_id": r["claim_id"],
                    "statement": r["statement"] or "",
                    "status": r["status"] or "",
                    "lane": list(r["lane"] or []),
                    "evidence_ids": list(r["evidence_ids"] or []),
                    "updated_at": r["updated_at"],
                }
                for r in session.run(
                    """
                    MATCH (c:Claim) WHERE $sid IN coalesce(c.source_ids, [])
                    RETURN c.claim_id AS claim_id, c.statement AS statement,
                           c.status AS status, c.lane AS lane,
                           c.evidence_ids AS evidence_ids, c.updated_at AS updated_at
                    ORDER BY c.updated_at DESC LIMIT 40
                    """,
                    sid=source_id,
                )
            ]
            entities_rows = [
                {"id": r["id"], "type": _first_label(r["labels"]), "name": r["name"] or r["id"]}
                for r in session.run(
                    f"""
                    MATCH (n) WHERE ({_ENTITY_LABEL_FILTER})
                      AND n.source_ids IS NOT NULL AND $sid IN n.source_ids
                      AND n.natural_key IS NOT NULL
                    RETURN n.natural_key AS id, labels(n) AS labels, {_display('n')} AS name
                    ORDER BY name LIMIT 60
                    """,
                    sid=source_id,
                )
            ]
            evidence = _evidence_for(
                session,
                [
                    r["eid"]
                    for r in session.run(
                        """
                        MATCH (e:Evidence)-[:FROM_SOURCE]->(:Source {source_id: $sid})
                        RETURN e.evidence_id AS eid LIMIT 24
                        """,
                        sid=source_id,
                    )
                ],
                blocked,
                limit=24,
            )
            sub = _source_subgraph(session, source_id, props)

        base = _source_row(
            {
                "source_id": source_id,
                "source_type": props.get("source_type") or "",
                "canonical_location": props.get("canonical_location"),
                "description": props.get("source_of_record_for"),
                "ingested_at": props.get("created_at"),
                "sensitivity": props.get("sensitivity"),
                "evidence_count": row["evidence_count"],
            },
            links,
        )
        base.update(
            {
                "preview": preview[:60],
                "preview_total": len(preview),
                "extracted": extracted,
                "claims": claims,
                "entities": entities_rows,
                "evidence": evidence,
                "metadata": _props(props),
                "subgraph": sub,
                "all_excerpts": preview,
            }
        )
        return base

    # ------------------------------------------------------------------ runs

    @router.get("/runs")
    def runs(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        resolve(authorization)
        global _RUNS_CACHE
        if _RUNS_CACHE is not None:
            return _RUNS_CACHE
        with graph.session() as session:
            out = []
            for r in session.run(
                """
                MATCH (n) WHERE n.pipeline_run_id IS NOT NULL
                WITH n.pipeline_run_id AS run, labels(n)[0] AS label, count(*) AS c,
                     min(n.created_at) AS started, max(n.created_at) AS ended
                WITH run, collect({label: label, c: c}) AS parts,
                     min(started) AS started, max(ended) AS ended,
                     sum(c) AS nodes
                RETURN run, parts, started, ended, nodes
                ORDER BY nodes DESC
                """
            ):
                counts = _counts((p["label"], p["c"]) for p in r["parts"])
                sources_count = next(
                    (c["count"] for c in counts if c["label"] == "Source"), 0
                )
                out.append(
                    {
                        "run_id": r["run"],
                        "started_at": r["started"],
                        "ended_at": r["ended"],
                        "source_count": sources_count,
                        "node_count": r["nodes"],
                        "edge_count": 0,
                        "counts": counts,
                    }
                )
            # 관계 수는 실행 표시가 노드에만 있어 양 끝 노드로 센다.
            for item in out:
                item["edge_count"] = session.run(
                    """
                    MATCH (a)-[r]->(b)
                    WHERE a.pipeline_run_id = $run AND b.pipeline_run_id = $run
                    RETURN count(r) AS c
                    """,
                    run=item["run_id"],
                ).single()["c"]
        _RUNS_CACHE = {"runs": out}
        return _RUNS_CACHE

    # ------------------------------------------------------------------ run

    @router.get("/run/{run_id}")
    def run(run_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        acct = resolve(authorization)
        # 민감도 필터가 결과를 바꾸므로 계정까지 키에 넣는다.
        cache_key = f"{acct.username}:{run_id}"
        hit = _RUN_CACHE.get(cache_key)
        if hit is not None:
            return hit
        with graph.session() as session:
            blocked = blocked_sources(session, acct)
            summary = session.run(
                """
                MATCH (n) WHERE n.pipeline_run_id = $run
                WITH labels(n)[0] AS label, count(*) AS c,
                     min(n.created_at) AS started, max(n.created_at) AS ended
                RETURN collect({label: label, c: c}) AS parts,
                       min(started) AS started, max(ended) AS ended, sum(c) AS nodes
                """,
                run=run_id,
            ).single()
            if not summary or not summary["nodes"]:
                raise HTTPException(status_code=404, detail="그런 수집 실행 기록이 없습니다.")

            counts = _counts((p["label"], p["c"]) for p in summary["parts"])
            edge_count = session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE a.pipeline_run_id = $run AND b.pipeline_run_id = $run
                RETURN count(r) AS c
                """,
                run=run_id,
            ).single()["c"]

            steps = _run_steps(session, run_id, blocked)
            changes, sub = _run_changes(session, run_id)

        payload = {
            "run_id": run_id,
            "started_at": summary["started"],
            "ended_at": summary["ended"],
            "source_count": next((c["count"] for c in counts if c["label"] == "Source"), 0),
            "node_count": summary["nodes"],
            "edge_count": edge_count,
            "counts": counts,
            "steps": steps,
            "changes": changes,
            "subgraph": sub,
            # 있는 척 만들지 않는다. 지금 데이터로 못 보여주는 것을 화면에 그대로 적는다.
            "limits": [
                "속성이 A 에서 B 로 바뀐 내역은 기록돼 있지 않습니다. 지금은 각 노드의 "
                "마지막 상태와 처음 들어온 시각만 있습니다.",
                "삭제된 노드·관계는 남지 않습니다. 이 화면은 늘어난 것만 보여 줍니다.",
                "'이 자료에서만 나온 것' 과 '다른 자료와 함께 뒷받침하는 것' 은 근거 자료 수로 "
                "가른 값입니다. 시간순 diff 가 아닙니다.",
            ],
        }
        _RUN_CACHE[cache_key] = payload
        return payload

    # ------------------------------------------------------------------ element

    @router.get("/element")
    def element(
        id: str = Query(...),
        kind: str = Query(default="node"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        acct = resolve(authorization)
        with graph.session() as session:
            blocked = blocked_sources(session, acct)
            if kind == "edge":
                return _edge_detail(session, id, blocked)
            row = session.run(
                f"""
                MATCH (x) WHERE x.natural_key = $key OR x.claim_id = $key
                RETURN labels(x) AS labels, properties(x) AS props, {_display('x')} AS name
                """,
                key=id,
            ).single()
            if row is None:
                raise HTTPException(status_code=404, detail="그런 항목이 없습니다.")
            props = dict(row["props"])
            label = _first_label(row["labels"])
            key = props.get("natural_key") or id
            return {
                "kind": "node",
                "id": key,
                "type": label,
                "type_ko": LABEL_KO.get(label, label),
                "label": row["name"] or key,
                "status": props.get("status") or props.get("bd_status"),
                "status_reason": _status_reason(props),
                "properties": _props(props),
                "sources": _sources_of(session, props.get("source_ids") or [], blocked),
                "evidence": _evidence_for(
                    session, props.get("evidence_ids") or [], blocked, limit=12
                ),
                "created_at": props.get("created_at"),
                "updated_at": props.get("updated_at"),
                "subgraph": _neighbour_subgraph(
                    session, key, label, row["name"] or key, props, limit=14
                ),
            }

    # ------------------------------------------------------------------ health

    @router.get("/health")
    def health(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        resolve(authorization)
        with graph.session() as session:
            groups = _health_groups(session)
            checked = session.run("RETURN toString(datetime()) AS now").single()["now"]
        return {"groups": groups, "checked_at": checked}

    # -------------------------------------------------------------- 원본 열기

    @router.post("/open-source")
    def open_source(
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """자료 원본 파일을 이 컴퓨터의 기본 앱으로 연다.

        **경로를 받지 않는다.** `source_id` 만 받고 경로는 그래프에 등록된
        `canonical_location` 에서 읽는다. 사람이 보낸 문자열로 파일을 열면 그래프 밖의
        어떤 파일이든 열리게 되므로, 여는 대상은 적재된 자료로만 한정한다.

        서버와 사람이 같은 컴퓨터에 있을 때만 뜻이 있다. 다른 곳에 올려 두면 서버 쪽
        컴퓨터에서 파일이 열려 아무 도움이 안 되므로, 그때는 `BWM_ALLOW_OPEN_FILE=0` 으로
        끄고 화면이 경로만 보여 준다.
        """
        acct = resolve(authorization)
        source_id = str(payload.get("source_id") or "").strip()
        if not source_id:
            raise HTTPException(status_code=400, detail="source_id 가 없습니다.")

        if os.getenv("BWM_ALLOW_OPEN_FILE", "1") == "0":
            raise HTTPException(
                status_code=403,
                detail="이 서버는 파일 열기를 끈 상태입니다. 경로를 복사해 직접 여세요.",
            )

        opener = _file_opener()
        if opener is None:
            raise HTTPException(
                status_code=501,
                detail=f"{platform.system()} 에서는 파일을 열 방법이 없습니다. 경로를 복사해 주세요.",
            )

        with graph.session() as session:
            if source_id in blocked_sources(session, acct):
                raise HTTPException(
                    status_code=403,
                    detail="이 자료는 지금 계정으로 볼 수 없습니다(민감도 제한).",
                )
            row = session.run(
                "MATCH (s:Source {source_id: $sid}) RETURN s.canonical_location AS location",
                sid=source_id,
            ).single()

        if row is None:
            raise HTTPException(status_code=404, detail="그런 자료가 없습니다.")
        location = str(row["location"] or "")
        if not location:
            raise HTTPException(status_code=404, detail="이 자료는 원본 위치가 기록돼 있지 않습니다.")

        path = Path(location)
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"등록된 위치에 파일이 없습니다: {location}",
            )

        try:
            subprocess.run([opener, str(path)], check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=500, detail=f"파일을 열지 못했습니다: {exc}") from exc

        return {"opened": True, "path": str(path)}

    # 예열이 라우터 함수를 그대로 호출할 수 있게 붙여 둔다(경로를 두 번 짜지 않는다).
    router.fetch_runs = runs  # type: ignore[attr-defined]
    router.fetch_run = run  # type: ignore[attr-defined]
    router.fetch_overview = overview  # type: ignore[attr-defined]
    router.fetch_entities = entities  # type: ignore[attr-defined]

    return router


# --------------------------------------------------------------------------- 헬퍼


def _file_opener() -> str | None:
    """이 컴퓨터에서 파일을 기본 앱으로 여는 명령. 없으면 None."""
    system = platform.system()
    if system == "Darwin":
        return shutil.which("open")
    if system == "Linux":
        return shutil.which("xdg-open")
    if system == "Windows":
        return shutil.which("start")
    return None


#: 대상별 근거 수·사업영역. 스냅샷 한 벌이라 한 번 잰 값을 다시 쓴다.
_EXTRA_CACHE: dict[str, dict[str, Any]] = {}


def _entity_extras(
    session: Session, label: str, keys: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """목록에 붙는 근거 수·사업영역.

    **라벨을 반드시 붙인다.** `MATCH (x) WHERE x.natural_key IN $keys` 처럼 라벨 없이 쓰면
    natural_key 에 인덱스가 없어 노드 3만 개를 통째로 훑는다(실측: 고객사 50개 한 페이지에
    44초). 라벨을 붙이면 그 라벨의 노드만 본다.

    한 번 잰 값은 담아 둔다 — 목록을 스크롤하며 같은 대상을 다시 세지 않게.
    """
    missing = [k for k in keys if k and k not in _EXTRA_CACHE]
    if missing:
        fresh: dict[str, dict[str, Any]] = {
            k: {"evidence_count": 0, "domains": []} for k in missing
        }

        for row in session.run(
            f"""
            MATCH (x:{label}) WHERE x.natural_key IN $keys
            OPTIONAL MATCH (k)-[:MENTIONS|ABOUT]->(x) WHERE k:Observation OR k:Claim
            WITH x, collect(k.evidence_ids) AS nested
            WITH x, reduce(acc = [], l IN nested | acc + coalesce(l, [])) AS ids
            UNWIND (CASE WHEN size(ids) = 0 THEN [null] ELSE ids END) AS eid
            RETURN x.natural_key AS key, count(DISTINCT eid) AS c
            """,
            keys=missing,
        ):
            fresh[row["key"]]["evidence_count"] = row["c"] or 0

        # 대상이 어느 사업영역에 걸리는지는 **종류마다 경로가 다르다.**
        #   사업영역·산업·딜   → 직접 IN_DOMAIN·BELONGS_TO
        #   고객사             → 딜을 한 번 낀다
        #   요구               → 고객사와 딜을 두 번 낀다
        # 그래서 경로를 세 갈래로 나눠 모은 뒤 합친다. 집계 결과를 다음 집계와 한 RETURN 에
        # 섞으면 Cypher 가 거부하므로 WITH 로 한 단씩 내려간다.
        for row in session.run(
            f"""
            MATCH (x:{label}) WHERE x.natural_key IN $keys
            OPTIONAL MATCH (x)-[:IN_DOMAIN|BELONGS_TO]->(bd:BusinessDomain)
            WITH x, collect(DISTINCT bd.name) AS direct
            OPTIONAL MATCH (x)<-[:WITH_ACCOUNT]-(:Deal)-[:IN_DOMAIN]->(bd2:BusinessDomain)
            WITH x, direct, collect(DISTINCT bd2.name) AS viaDeal
            OPTIONAL MATCH (x)<-[:HAS_NEED]-(:Account)<-[:WITH_ACCOUNT]-(:Deal)
                           -[:IN_DOMAIN]->(bd3:BusinessDomain)
            WITH x, direct, viaDeal, collect(DISTINCT bd3.name) AS viaAccount
            RETURN x.natural_key AS key,
                   [n IN direct + viaDeal + viaAccount WHERE n IS NOT NULL] AS names
            """,
            keys=missing,
        ):
            fresh[row["key"]]["domains"] = sorted(set(row["names"] or []))[:4]

        _EXTRA_CACHE.update(fresh)

    return {
        k: _EXTRA_CACHE.get(k, {"evidence_count": 0, "domains": []}) for k in keys if k
    }


#: 종류별 필터 값. 스냅샷 고정값이라 한 번만 만든다.
_FACET_CACHE: dict[str, dict[str, list[str]]] = {}


def _facets(session: Session, label: str) -> dict[str, list[str]]:
    hit = _FACET_CACHE.get(label)
    if hit is not None:
        return hit
    domains = [
        r["name"]
        for r in session.run(
            "MATCH (bd:BusinessDomain) WHERE bd.name IS NOT NULL "
            "RETURN bd.name AS name ORDER BY name"
        )
    ]
    statuses = [
        r["s"]
        for r in session.run(
            f"MATCH (x:{label}) WITH coalesce(x.status, x.bd_status) AS s "
            "WHERE s IS NOT NULL RETURN DISTINCT s ORDER BY s"
        )
    ]
    _FACET_CACHE[label] = {"domains": domains, "statuses": statuses}
    return _FACET_CACHE[label]


def _related(session: Session, label: str, key: str) -> list[dict[str, Any]]:
    """관계별로 묶은 상대 목록. 업무 관계만 본다(근거 사슬은 근거 목록이 맡는다)."""
    groups: dict[str, list[dict[str, str]]] = {}
    for row in session.run(
        f"""
        MATCH (x:{label}) WHERE x.natural_key = $key
        MATCH (x)-[r]-(o)
        WHERE type(r) IN $types AND o.natural_key IS NOT NULL
        RETURN type(r) AS t, o.natural_key AS id, labels(o) AS labels,
               {_display('o')} AS name
        ORDER BY t, name
        LIMIT 400
        """,
        key=key,
        types=_EDGE_LIST,
    ):
        groups.setdefault(row["t"], []).append(
            {
                "id": row["id"],
                "type": _first_label(row["labels"]),
                "name": row["name"] or row["id"],
            }
        )
    return [
        {"type": t, "type_ko": EDGE_KO.get(t, t), "items": items[:40]}
        for t, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    ]


def _claims_about(session: Session, label: str, key: str) -> list[dict[str, Any]]:
    """이 대상에 붙은 주장.

    **라벨을 붙여 대상을 인덱스로 먼저 잡는다.** 라벨 없이 `(c:Claim)-[:ABOUT]->(x)` 로 쓰면
    주장 4,146개에서 밖으로 뻗어 나가며 찾아 대상 하나 여는 데 35초가 걸렸다(실측).
    """
    return [
        {
            "claim_id": r["claim_id"],
            "statement": r["statement"] or "",
            "status": r["status"] or "",
            "lane": list(r["lane"] or []),
            "evidence_ids": list(r["evidence_ids"] or []),
            "updated_at": r["updated_at"],
        }
        for r in session.run(
            f"""
            MATCH (x:{label}) WHERE x.natural_key = $key
            MATCH (c:Claim)-[:ABOUT|MENTIONS]->(x)
            RETURN c.claim_id AS claim_id, c.statement AS statement, c.status AS status,
                   c.lane AS lane, c.evidence_ids AS evidence_ids, c.updated_at AS updated_at
            ORDER BY
              CASE WHEN 'critical' IN coalesce(c.lane, []) THEN 0
                   WHEN 'conflict' IN coalesce(c.lane, []) THEN 1 ELSE 2 END,
              c.updated_at DESC
            LIMIT 40
            """,
            key=key,
        )
    ]


def _evidence_for(
    session: Session, evidence_ids: Sequence[str], blocked: set[str], limit: int = 24
) -> list[dict[str, Any]]:
    ids = [e for e in dict.fromkeys(evidence_ids) if e][: limit * 3]
    if not ids or limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    for r in session.run(
        """
        MATCH (e:Evidence) WHERE e.evidence_id IN $ids
        OPTIONAL MATCH (e)-[:FROM_SOURCE]->(s:Source)
        RETURN e.evidence_id AS evidence_id, e.source_id AS source_id,
               e.locator AS locator, coalesce(e.snippet, e.excerpt, '') AS snippet,
               e.authored_at AS observed_at,
               s.source_type AS source_type, s.canonical_location AS location
        """,
        ids=ids,
    ):
        sid = r["source_id"] or ""
        if sid in blocked:
            continue
        out.append(
            {
                "evidence_id": r["evidence_id"],
                "source_id": sid,
                "source_title": _source_title(r["location"], sid),
                "source_type": r["source_type"] or "",
                "authority_tier": None,
                "locator": r["locator"] or "",
                "snippet": r["snippet"] or "",
                "observed_at": r["observed_at"],
                "open": _open_target(r["location"], r["locator"] or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def _source_title(location: str | None, source_id: str) -> str:
    if not location:
        return source_id
    return location.rsplit("/", 1)[-1] or source_id


def _sources_of(
    session: Session, source_ids: Sequence[str], blocked: set[str]
) -> list[dict[str, Any]]:
    ids = [s for s in dict.fromkeys(source_ids) if s and s not in blocked]
    if not ids:
        return []
    return [
        {
            "source_id": r["sid"],
            "title": _source_title(r["location"], r["sid"]),
            "source_type": r["stype"] or "",
        }
        for r in session.run(
            """
            MATCH (s:Source) WHERE s.source_id IN $ids
            RETURN s.source_id AS sid, s.canonical_location AS location,
                   s.source_type AS stype
            ORDER BY s.source_id
            """,
            ids=ids,
        )
    ]


#: 자료 → (사업영역·고객사·지식 수) 표. 스냅샷 한 벌을 읽는 데모라 프로세스 안에 한 번만 만든다.
#: ingest 를 다시 돌리면 API 를 재시작해야 반영된다 — 이 데모의 전제(1회 수집)와 같다.
_LINK_CACHE: dict[str, dict[str, Any]] | None = None


def _source_links(session: Session, source_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """자료마다 어떤 사업영역·고객사와 이어져 있나. 서재 필터와 카드가 쓴다.

    **자료마다 전체 노드를 훑지 않는다.** 처음에 그렇게 짰더니 267종 × 노드 3만 개가 되어
    서재 목록 한 번에 30초가 걸렸다(실측). 방향을 뒤집어 **노드를 한 번만 훑으면서
    source_ids 를 펼친다.** 두 쿼리로 갈랐다.
      1) 사업영역·고객사 이름 (426개 노드만 본다)
      2) 자료별 지식 수 (natural_key 를 가진 노드 한 번 훑기)
    """
    global _LINK_CACHE

    if _LINK_CACHE is None:
        table: dict[str, dict[str, Any]] = {}
        # 인자로 받은 목록과 무관하게 표 전체를 한 번에 만든다(예열에서 빈 목록으로도 부른다).

        def slot(sid: str) -> dict[str, Any]:
            return table.setdefault(sid, {"domains": [], "accounts": [], "entity_count": 0})

        for row in session.run(
            """
            MATCH (n) WHERE (n:BusinessDomain OR n:Account) AND n.source_ids IS NOT NULL
            UNWIND n.source_ids AS sid
            WITH sid,
                 collect(DISTINCT CASE WHEN n:BusinessDomain THEN n.name END) AS domains,
                 collect(DISTINCT CASE WHEN n:Account THEN n.canonical_name END) AS accounts
            RETURN sid,
                   [d IN domains WHERE d IS NOT NULL] AS domains,
                   [a IN accounts WHERE a IS NOT NULL] AS accounts
            """
        ):
            entry = slot(row["sid"])
            entry["domains"] = sorted(row["domains"] or [])
            entry["accounts"] = sorted(row["accounts"] or [])[:12]

        for row in session.run(
            """
            MATCH (n) WHERE n.source_ids IS NOT NULL AND n.natural_key IS NOT NULL
            UNWIND n.source_ids AS sid
            RETURN sid, count(*) AS c
            """
        ):
            slot(row["sid"])["entity_count"] = row["c"] or 0

        _LINK_CACHE = table

    return {
        sid: _LINK_CACHE.get(sid, {"domains": [], "accounts": [], "entity_count": 0})
        for sid in source_ids
        if sid
    }


def _source_row(raw: dict[str, Any], link: dict[str, Any]) -> dict[str, Any]:
    stype = raw.get("source_type") or ""
    return {
        "source_id": raw["source_id"],
        "title": _source_title(raw.get("canonical_location"), raw["source_id"]),
        "source_type": stype,
        "source_type_ko": SOURCE_TYPE_KO.get(stype, stype),
        "group": SOURCE_GROUPS.get(stype, "그 밖"),
        "description": _text(raw.get("description")),
        "domains": link.get("domains", []),
        "accounts": link.get("accounts", []),
        "ingested_at": raw.get("ingested_at"),
        "evidence_count": raw.get("evidence_count") or 0,
        "entity_count": link.get("entity_count", 0),
        "sensitivity": raw.get("sensitivity"),
        "open": _open_target(raw.get("canonical_location")),
    }


def _source_facets(session: Session, blocked: set[str]) -> dict[str, Any]:
    types: list[dict[str, Any]] = []
    group_count: dict[str, int] = {}
    for r in session.run(
        """
        MATCH (s:Source) WHERE NOT s.source_id IN $blocked
        RETURN s.source_type AS t, count(*) AS c ORDER BY c DESC
        """,
        blocked=list(blocked),
    ):
        t = r["t"] or ""
        g = SOURCE_GROUPS.get(t, "그 밖")
        # group 을 함께 내보낸다. 화면이 그룹 → 종류 폴더 트리를 이걸로 조립한다
        # (SOURCE_GROUPS 매핑을 화면에 한 벌 더 두지 않으려는 것이다).
        types.append(
            {"name": t, "name_ko": SOURCE_TYPE_KO.get(t, t), "count": r["c"], "group": g}
        )
        group_count[g] = group_count.get(g, 0) + r["c"]

    domains = [
        r["name"]
        for r in session.run(
            "MATCH (bd:BusinessDomain) WHERE bd.name IS NOT NULL "
            "RETURN bd.name AS name ORDER BY name"
        )
    ]
    accounts = [
        r["name"]
        for r in session.run(
            """
            MATCH (a:Account) WHERE a.canonical_name IS NOT NULL
            RETURN a.canonical_name AS name, count { (a)--() } AS d
            ORDER BY d DESC LIMIT 40
            """
        )
    ]
    return {
        "groups": [
            {"name": g, "count": c}
            for g, c in sorted(group_count.items(), key=lambda kv: -kv[1])
        ],
        "source_types": types,
        "domains": domains,
        "accounts": accounts,
    }


def _neighbour_subgraph(
    session: Session,
    key: str,
    label: str,
    name: str,
    props: dict[str, Any],
    limit: int = 24,
) -> dict[str, Any]:
    """고른 대상을 중심으로 한 관계도. 업무 관계만 그린다(그림이 읽히게).

    **두 걸음까지 넓힌다.** 한 걸음만 보면 그림이 너무 얇아 관계로 읽히지 않는다.
    실측: 가나손해보험은 관계가 239개인데 직접 업무 관계는 2개뿐이었고(나머지는 근거 사슬),
    노드 3개짜리 그림이 나왔다. 두 걸음이면 고객사 → 딜 → 사업영역, 고객사 → 요구 → 역량이
    한 화면에 들어온다.
    """
    nodes = {key: _node(key, [label], name, "focal", props.get("status"))}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    # 한 걸음과 두 걸음을 **따로** 부른다.
    #
    # 처음에는 `[r*1..2]` 가변 경로 하나에 후보마다 `count { (o)--() }` 를 붙였다. 이웃이
    # 많은 대상(요구 등)에서 조합이 폭발해 대상 상세 한 번이 10초를 넘겼다(실측).
    # 두 걸음을 나누고 정렬을 이름으로 바꾸면 같은 그림을 훨씬 싸게 얻는다 —
    # 중요도 순서는 화면의 coreSubgraph 가 엣지를 보고 다시 고른다.
    first_hop = [
        row
        for row in session.run(
            f"""
            MATCH (x:{label}) WHERE x.natural_key = $key
            MATCH (x)-[r]-(o)
            WHERE type(r) IN $types AND o.natural_key IS NOT NULL
            RETURN DISTINCT o.natural_key AS other, labels(o) AS labels,
                   {_display('o')} AS name, coalesce(o.status, o.bd_status) AS status
            ORDER BY name LIMIT $limit
            """,
            key=key,
            types=_EDGE_LIST,
            limit=limit,
        )
    ]
    for row in first_hop:
        nodes[row["other"]] = _node(
            row["other"], row["labels"], row["name"] or row["other"], "cited", row["status"]
        )

    room = limit - len(first_hop)
    if room > 0 and first_hop:
        for row in session.run(
            f"""
            MATCH (a)-[r]-(o)
            WHERE a.natural_key IN $ones AND type(r) IN $types
              AND o.natural_key IS NOT NULL AND NOT o.natural_key IN $known
            RETURN DISTINCT o.natural_key AS other, labels(o) AS labels,
                   {_display('o')} AS name, coalesce(o.status, o.bd_status) AS status
            ORDER BY name LIMIT $room
            """,
            ones=[r["other"] for r in first_hop],
            known=list(nodes),
            types=_EDGE_LIST,
            room=room,
        ):
            nodes[row["other"]] = _node(
                row["other"],
                row["labels"],
                row["name"] or row["other"],
                "supporting",
                row["status"],
            )

    # 남긴 노드 사이의 관계를 전부 그린다. 중심에서 뻗은 선만 그리면 별 모양이 되고,
    # 그건 관계가 아니라 목록으로 읽힌다.
    ids = list(nodes)
    for row in session.run(
        """
        MATCH (a)-[r]->(b)
        WHERE a.natural_key IN $ids AND b.natural_key IN $ids AND type(r) IN $types
        RETURN a.natural_key AS a, b.natural_key AS b, type(r) AS t LIMIT 160
        """,
        ids=ids,
        types=_EDGE_LIST,
    ):
        marker = (row["a"], row["b"], row["t"])
        if marker in seen:
            continue
        seen.add(marker)
        edges.append({"from": row["a"], "to": row["b"], "type": row["t"]})

    return _subgraph(list(nodes.values()), edges)


def _source_subgraph(
    session: Session, source_id: str, props: dict[str, Any]
) -> dict[str, Any]:
    """자료 → 여기서 나온 엔티티 → 그 엔티티끼리의 관계."""
    title = _source_title(props.get("canonical_location"), source_id)
    nodes = {source_id: _node(source_id, ["Source"], title, "focal")}
    edges: list[dict[str, Any]] = []

    for row in session.run(
        f"""
        MATCH (n) WHERE ({_ENTITY_LABEL_FILTER})
          AND n.source_ids IS NOT NULL AND $sid IN n.source_ids
          AND n.natural_key IS NOT NULL
        RETURN n.natural_key AS id, labels(n) AS labels, {_display('n')} AS name,
               coalesce(n.status, n.bd_status) AS status, count {{ (n)--() }} AS degree
        ORDER BY degree DESC LIMIT 20
        """,
        sid=source_id,
    ):
        nodes[row["id"]] = _node(
            row["id"], row["labels"], row["name"] or row["id"], "cited", row["status"]
        )
        edges.append({"from": source_id, "to": row["id"], "type": "FROM_SOURCE"})

    ids = [i for i in nodes if i != source_id]
    if ids:
        for row in session.run(
            """
            MATCH (a)-[r]->(b)
            WHERE a.natural_key IN $ids AND b.natural_key IN $ids AND type(r) IN $types
            RETURN a.natural_key AS a, b.natural_key AS b, type(r) AS t LIMIT 100
            """,
            ids=ids,
            types=_EDGE_LIST,
        ):
            edges.append({"from": row["a"], "to": row["b"], "type": row["t"]})

    return _subgraph(list(nodes.values()), edges)


#: 자료 → 늘어난 지식 표. 스냅샷 한 벌이라 프로세스 안에 한 번만 만든다(_LINK_CACHE 와 같은 이유).
_STEP_CACHE: dict[str, dict[str, list[tuple[str, int]]]] | None = None


def _step_counts(session: Session) -> dict[str, dict[str, list[tuple[str, int]]]]:
    """자료별로 무엇이 얼마나 늘었나. **노드를 한 번만 훑는다.**

    처음에는 자료마다 쿼리를 돌렸다. 267종 × 2쿼리 = 534회가 되어 변경 상세가 2분을 넘겨
    화면이 열리지 않았다(실측). 방향을 뒤집어 노드 한 번 훑으면서 source_ids 를 펼친다.
    """
    global _STEP_CACHE
    if _STEP_CACHE is not None:
        return _STEP_CACHE

    table: dict[str, dict[str, list[tuple[str, int]]]] = {}
    for row in session.run(
        """
        MATCH (n) WHERE n.source_ids IS NOT NULL AND size(n.source_ids) > 0
        WITH n, labels(n)[0] AS label, size(n.source_ids) = 1 AS only_here
        UNWIND n.source_ids AS sid
        RETURN sid, label, only_here, count(*) AS c
        """
    ):
        entry = table.setdefault(row["sid"], {"added": [], "touched": []})
        key = "added" if row["only_here"] else "touched"
        entry[key].append((row["label"], row["c"]))

    _STEP_CACHE = table
    return table


def _run_steps(session: Session, run_id: str, blocked: set[str]) -> list[dict[str, Any]]:
    """자료 하나를 처리하면서 지식이 얼마나 늘었나.

    `added` 는 **그 자료만이 근거인 것**, `touched` 는 **다른 자료와 함께 뒷받침하는 것**이다.
    시간순 diff 가 아니다. 지금 데이터에 속성 변경 이력이 없어서 그 이상은 말할 수 없고,
    화면의 `limits` 가 그 사실을 그대로 적는다.
    """
    counts = _step_counts(session)
    steps: list[dict[str, Any]] = []

    for row in session.run(
        """
        MATCH (s:Source) WHERE s.pipeline_run_id = $run
        RETURN s.source_id AS sid, s.created_at AS at,
               s.canonical_location AS location, s.source_type AS stype
        ORDER BY s.created_at, s.source_id
        LIMIT 400
        """,
        run=run_id,
    ):
        sid = row["sid"]
        if sid in blocked:
            continue
        entry = counts.get(sid, {"added": [], "touched": []})
        stype = row["stype"] or ""
        steps.append(
            {
                "at": row["at"],
                "source_id": sid,
                "source_title": _source_title(row["location"], sid),
                "source_type_ko": SOURCE_TYPE_KO.get(stype, stype),
                "added": _counts(sorted(entry["added"], key=lambda kv: -kv[1])),
                "touched": _counts(sorted(entry["touched"], key=lambda kv: -kv[1])),
            }
        )
    # 지식이 많이 늘어난 자료를 위로 올린다. 267개를 시각순으로만 늘어놓으면 훑을 수 없다.
    steps.sort(key=lambda s: -sum(c["count"] for c in s["added"]))
    return steps[:80]


def _run_changes(session: Session, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """이번 실행에서 새로 생긴 대표 노드·관계와 그것만 그린 관계도."""
    changes: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}

    for row in session.run(
        f"""
        MATCH (n) WHERE n.pipeline_run_id = $run AND n.natural_key IS NOT NULL
          AND NOT n:Source AND NOT n:Observation
        RETURN n.natural_key AS id, labels(n) AS labels, {_display('n')} AS name,
               coalesce(n.status, n.bd_status) AS status, count {{ (n)--() }} AS degree
        ORDER BY degree DESC LIMIT 20
        """,
        run=run_id,
    ):
        label = _first_label(row["labels"])
        name = row["name"] or row["id"]
        changes.append(
            {
                "kind": "node",
                "id": row["id"],
                "type": label,
                "type_ko": LABEL_KO.get(label, label),
                "label": name,
                "status": row["status"],
            }
        )
        nodes[row["id"]] = _node(row["id"], row["labels"], name, "cited", row["status"])

    edges: list[dict[str, Any]] = []
    ids = list(nodes)
    if ids:
        for row in session.run(
            f"""
            MATCH (a)-[r]->(b)
            WHERE a.natural_key IN $ids AND b.natural_key IN $ids AND type(r) IN $types
            RETURN a.natural_key AS a, b.natural_key AS b, type(r) AS t,
                   {_display('a')} AS an, {_display('b')} AS bn
            LIMIT 80
            """,
            ids=ids,
            types=_EDGE_LIST,
        ):
            edges.append({"from": row["a"], "to": row["b"], "type": row["t"]})
            if len(changes) < 40:
                changes.append(
                    {
                        "kind": "edge",
                        "id": f"{row['a']}__{row['t']}__{row['b']}",
                        "type": row["t"],
                        "type_ko": EDGE_KO.get(row["t"], row["t"]),
                        "label": row["t"],
                        "from_label": row["an"] or row["a"],
                        "to_label": row["bn"] or row["b"],
                    }
                )

    return changes, _subgraph(list(nodes.values()), edges)


def _edge_detail(session: Session, edge_id: str, blocked: set[str]) -> dict[str, Any]:
    parts = edge_id.split("__")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="관계 식별자 형식이 아닙니다.")
    a, edge_type, b = parts
    row = session.run(
        f"""
        MATCH (x)-[r]->(y)
        WHERE x.natural_key = $a AND y.natural_key = $b AND type(r) = $t
        RETURN {_display('x')} AS an, {_display('y')} AS bn, properties(r) AS props,
               labels(x) AS al, labels(y) AS bl,
               coalesce(x.status, x.bd_status) AS ast,
               coalesce(y.status, y.bd_status) AS bst
        """,
        a=a,
        b=b,
        t=edge_type,
    ).single()
    if row is None:
        raise HTTPException(status_code=404, detail="그런 관계가 없습니다.")

    props = dict(row["props"])
    claim_ids = list(props.get("claim_ids") or [])
    evidence_ids: list[str] = []
    if claim_ids:
        for r in session.run(
            "MATCH (c:Claim) WHERE c.claim_id IN $ids RETURN c.evidence_ids AS ids",
            ids=claim_ids,
        ):
            evidence_ids.extend(r["ids"] or [])

    nodes = [
        _node(a, row["al"], row["an"] or a, "focal", row["ast"]),
        _node(b, row["bl"], row["bn"] or b, "cited", row["bst"]),
    ]
    return {
        "kind": "edge",
        "id": edge_id,
        "type": edge_type,
        "type_ko": EDGE_KO.get(edge_type, edge_type),
        "label": f"{row['an'] or a} → {row['bn'] or b}",
        "status": props.get("status"),
        "status_reason": _status_reason(props),
        "properties": _props(props),
        "sources": _sources_of(session, props.get("source_ids") or [], blocked),
        "evidence": _evidence_for(session, evidence_ids, blocked, limit=12),
        "created_at": props.get("created_at"),
        "updated_at": props.get("updated_at"),
        "subgraph": _subgraph(nodes, [{"from": a, "to": b, "type": edge_type}]),
    }


def _status_reason(props: dict[str, Any]) -> str:
    """상태를 왜 그렇게 봤는지. 지어내지 않고 데이터에 있는 근거만 적는다."""
    status = props.get("status") or props.get("bd_status") or ""
    lane = list(props.get("lane") or [])
    evidence = len(props.get("evidence_ids") or [])
    sources = len(props.get("source_ids") or [])
    bits: list[str] = []
    if status:
        bits.append(f"상태 {status}")
    if "critical" in lane:
        bits.append("놓치면 안 되는 항목으로 표시됨")
    if "conflict" in lane:
        bits.append("자료끼리 어긋나는 항목으로 표시됨")
    if evidence:
        bits.append(f"근거 {evidence}건")
    if sources:
        bits.append(f"자료 {sources}종")
    if props.get("needs_human_confirm"):
        bits.append("사람 확인이 필요하다고 표시됨")
    return " · ".join(bits)


def _health_groups(session: Session) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []

    def group(key: str, title: str, why: str, rows, make) -> None:
        items = [make(r) for r in rows]
        groups.append(
            {"key": key, "title": title, "why": why, "count": len(items), "items": items[:40]}
        )

    group(
        "evidence_poor_domains",
        "근거가 적은 사업영역",
        "이 영역에 대해 답할 때 근거가 얇습니다. 자료를 더 넣거나, 답변의 근거 세기가 낮게 "
        "나오는 것을 그대로 받아들여야 합니다.",
        session.run(
            """
            MATCH (bd:BusinessDomain)
            OPTIONAL MATCH (k)-[:MENTIONS|ABOUT]->(bd) WHERE k:Observation OR k:Claim
            WITH bd, collect(k.evidence_ids) AS nested
            WITH bd, reduce(acc = [], l IN nested | acc + coalesce(l, [])) AS ids
            WITH bd, size(ids) AS c WHERE c < 8
            RETURN bd.natural_key AS id, bd.name AS name, c ORDER BY c, name
            """
        ),
        lambda r: {
            "target_kind": "entity",
            "target_id": r["id"],
            "target_type": "domain",
            "label": r["name"] or r["id"],
            "detail": f"근거 {r['c']}건",
        },
    )

    group(
        "entities_without_evidence",
        "근거가 하나도 없는 대상",
        "자료에서 이름은 나왔지만 그것을 뒷받침하는 발췌가 없습니다. 답변에 쓰면 근거 없이 "
        "말하는 셈이 됩니다.",
        session.run(
            """
            MATCH (x) WHERE (x:Account OR x:Need OR x:Capability OR x:Feature)
              AND x.natural_key IS NOT NULL
              AND NOT EXISTS { MATCH (k)-[:MENTIONS|ABOUT]->(x) WHERE k:Observation OR k:Claim }
            RETURN x.natural_key AS id, labels(x) AS labels,
                   coalesce(x.canonical_name, x.name) AS name
            ORDER BY name LIMIT 200
            """
        ),
        lambda r: {
            "target_kind": "entity",
            "target_id": r["id"],
            "target_type": _explore_slug(_first_label(r["labels"])),
            "label": r["name"] or r["id"],
            "detail": LABEL_KO.get(_first_label(r["labels"]), ""),
        },
    )

    group(
        "unmapped_needs",
        "대응 역량이 안 붙은 요구",
        "고객이 요구한 것은 확인됐는데 우리 역량·기능과 연결되지 않았습니다. 제품 공백 후보이고, "
        "실제로 없다는 뜻은 아닙니다.",
        session.run(
            """
            MATCH (n:Need) WHERE n.natural_key IS NOT NULL
              AND NOT EXISTS { MATCH (n)-[:ADDRESSED_BY]->() }
            RETURN n.natural_key AS id, n.name AS name,
                   count { (n)<-[:HAS_NEED]-(:Account) } AS accounts
            ORDER BY accounts DESC, name LIMIT 200
            """
        ),
        lambda r: {
            "target_kind": "entity",
            "target_id": r["id"],
            "target_type": "need",
            "label": r["name"] or r["id"],
            "detail": f"이 요구를 가진 고객사 {r['accounts']}곳",
        },
    )

    group(
        "conflicts",
        "자료끼리 어긋나는 주장",
        "한쪽을 지우지 않고 둘 다 남겨 둡니다. 사람이 어느 쪽이 맞는지 정해야 합니다.",
        session.run(
            """
            MATCH (c:Claim)
            WHERE 'conflict' IN coalesce(c.lane, []) OR c.status = 'DISPUTED'
            RETURN c.claim_id AS id, c.statement AS statement, c.status AS status
            ORDER BY c.updated_at DESC LIMIT 100
            """
        ),
        lambda r: {
            "target_kind": "none",
            "label": (r["statement"] or r["id"])[:120],
            "detail": r["status"] or "",
        },
    )

    group(
        "critical_unverified",
        "놓치면 안 되는데 아직 미검증",
        "한 번만 등장한 큰 딜·계약 전제·규제 항목 같은 것입니다. 정본 자료로 한 번 확인해야 합니다.",
        session.run(
            """
            MATCH (c:Claim)
            WHERE 'critical' IN coalesce(c.lane, [])
              AND coalesce(c.status, '') IN ['CANDIDATE', 'UNVERIFIED', 'CRITICAL']
            RETURN c.claim_id AS id, c.statement AS statement, c.status AS status
            ORDER BY c.updated_at DESC LIMIT 100
            """
        ),
        lambda r: {
            "target_kind": "none",
            "label": (r["statement"] or r["id"])[:120],
            "detail": r["status"] or "",
        },
    )

    group(
        "resolution_candidates",
        "같은 대상일 수 있는 이름",
        "표기가 비슷해 같은 회사일 수 있습니다. **합치기 전에 사람이 확인해야 합니다** — 실제로는 "
        "다른 법인인 경우가 있습니다(마바손해보험과 마바캐피탈처럼).",
        session.run(
            """
            MATCH (a:Account), (b:Account)
            WHERE a.canonical_name < b.canonical_name
              AND size(a.canonical_name) >= 3 AND size(b.canonical_name) >= 3
              AND (b.canonical_name STARTS WITH a.canonical_name
                   OR a.canonical_name STARTS WITH b.canonical_name)
            RETURN a.natural_key AS id, a.canonical_name AS an, b.canonical_name AS bn
            ORDER BY an LIMIT 60
            """
        ),
        lambda r: {
            "target_kind": "entity",
            "target_id": r["id"],
            "target_type": "account",
            "label": f"{r['an']} · {r['bn']}",
            "detail": "표기가 겹칩니다",
        },
    )

    return groups


def _explore_slug(label: str) -> str:
    for slug, name in EXPLORE_TYPES.items():
        if name == label:
            return slug
    return "account"
