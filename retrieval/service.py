"""Retrieval 3종 + 공통 후처리 (REVISED §4 ⑦ · §6 A5).

    Q-E  엔티티 링킹 → 1~2hop → Evidence 수집
    Q-M  이름 붙은 Cypher 템플릿 + deterministic 집계
    Q-S  집계를 **먼저** 모으고, 그 결과와 선별 Evidence 만 합성 입력으로 넘긴다

세 경로가 끝나면 공통 후처리를 같은 순서로 밟는다.
    (1) sensitivity·시간 하드 필터
    (2) critical 합류 — lane=critical 이 상위 K 밖으로 밀려 사라지지 않게 별도 경로로 붙인다
    (3) Evidence fulltext top-K 병행 — 결과는 "추가 원문 근거"로 **구분해서** 돌려준다
    (4) citation 검증은 답변이 만들어진 뒤에 돈다. 여기서는 검증기를 제공한다(A6 가 호출)

읽기 전용 세션만 쓴다. 쓰기 핸들이 들어오면 생성자에서 막는다.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from graph.connection import ReadOnlyGraph, require_read_only

from . import store
from .access import DEMO_POLICY, AccessPolicy, apply_hard_filters
from .citations import verify_citations
from .gaps import GapEngine
from .linking import EntityLinker
from .pool import CandidatePool
from .router import Router, domain_statuses_in
from .subgraph import SubgraphBuilder
from .templates import run_template
from .text import content_terms, derive_gap_subject, search_terms, term_overlap
from .types import (
    EntityRef,
    EvidenceRef,
    GapFinding,
    RawSignal,
    RetrievalResult,
    RouteDecision,
)

#: gap 판정을 만들어야 하는 질문의 표지. Q-S 는 이것과 무관하게 항상 gap 을 판정한다(§6 A5 순서).
GAP_SIGNAL = re.compile(
    r"제공(되|하)|지원(되|하)|부족한|미흡한|대응할|해결할|없나|전제|가능한가",
    re.IGNORECASE,
)

BRIDGING_OBSERVATION_LIMIT = 8
CRITICAL_NODE_LIMIT = 6

#: 후보 근거의 상한. 여기서 잘린 발췌는 답변에 실릴 기회 자체가 없다.
#: 합성 입력은 이것과 별개로 SYNTHESIS_EVIDENCE_LIMIT 이 묶고 있어서, 이 숫자를 올려도
#: LLM 비용은 그대로다. 상한을 40 으로 두었을 때 가나손해보험·B2B유통 질문의 핵심 발췌가
#: 후보에도 못 들어오는 것을 실측해서 올렸다.
EVIDENCE_POOL_LIMIT = 140

#: 근거가 어디서 왔는지 A6 에게 알리는 그룹 이름 접두. A6 의 선별이 이 접두로 몫을 나눈다.
RAW_GROUP_PREFIX = "raw#"
RARE_GROUP_PREFIX = "rare#"

#: 발췌가 이만큼 이하인 자료는 "그 자료엔 이게 전부"인 자료로 본다.
#: 하늘IT 검토 메모가 7건이고, 그 안에만 있는 멀티테넌트 전제가 Gate 1A 4번의 시험 대상이다.
RARE_SOURCE_EVIDENCE = 12

#: 자료 **안의** 묶음(xlsx 시트)이 이만큼 이하면 그 묶음도 rare 로 본다.
#: 자료 단위로만 재면 발췌 79건짜리 BD Overview 안의 `Guest통제` 2행이 큰 자료 취급을 받아,
#: 원문 검색 몫에서 Slack 상위 발췌에 매번 밀린다(실측 GQ-G1: 후보에는 있는데 24칸에 못 든다).
#: 실측(2026-08-08) xlsx 시트 40개의 분포로 정했다 — 4 이하가 8개(Guest통제 2 · 전략매출 2 ·
#: 영업활동일지 날짜 시트 6), 5~12 가 10개. 12 로 두면 `대응 불가·제약`(12)·`오픈 후 운영 불편`(12)
#: 같은 보통 크기 시트까지 rare 가 되어, rare 가 원문 검색 몫 6칸을 다 먹고 다른 자료가 사라진다.
RARE_SECTION_EVIDENCE = 4


class RetrievalService:
    SYNTHESIS_EVIDENCE_LIMIT = 24
    SYNTHESIS_SNIPPET_CHARS = 300
    SYNTHESIS_ROWS_PER_STAGE = 10
    RAW_SIGNAL_K = 8
    #: 질문의 중심 엔티티를 다루는 자료 안에서 한 번 더 찾을 때의 상한.
    ANCHORED_SIGNAL_K = 24
    ANCHOR_SOURCE_LIMIT = 6
    #: 발췌가 몇 줄뿐인 자료에 따로 떼어 주는 앵커 자리. 위 상한과 별도로 센다.
    #: 앵커 목록의 순서는 관측 수·fulltext 점수라 발췌가 많은 자료가 앞에 선다. 그 순서로
    #: 앞에서 6개만 남기면 작은 자료가 상한에 걸려 통째로 빠지고, 그 자료에만 적힌 사실은
    #: 코퍼스 어디에도 없으므로 되찾을 길이 없다(PRD 1A AC-6).
    RARE_ANCHOR_LIMIT = 8
    #: 발췌가 많은 자료가 후보로 낼 수 있는 최대 건수. 자료 하나가 순위표를 다 채우지 못하게
    #: 하는 몫이다. 자료가 작을수록 더 준다(아래 `_source_quota`).
    ANCHORED_PER_SOURCE = 3
    #: 발췌가 이만큼 이하인 자료는 통째로 읽어서 직접 고른다(전역 상위 N 에 밀리지 않게).
    #: 실측(2026-08-08) 자료 267개의 발췌 분포가 기준이다. 이 상한을 넘는 것은 Slack
    #: 영업활동공유(7,776)와 AI 대화 샘플(2,353) 둘뿐이고, 그 둘은 발췌가 많아서 전역 상위 N
    #: 안에 반드시 들어오므로 순위 경쟁에 맡겨도 잃는 것이 없다. 나머지 265개는 통째로 읽으니
    #: 전역 컷에 밀려 사라질 수 없다.
    #: 500 으로 두었을 때 Slack 팀채널 3개(528·654·1,137)가 전역 순위 경쟁으로 넘어갔고,
    #: 그 경쟁은 발췌 7,776건짜리 채널이 이긴다.
    SMALL_SOURCE_SCAN = 1200
    #: 통째로 읽을 때 한 질문에서 훑는 발췌 총량의 상한. 작은 자료부터 채운다.
    SMALL_SOURCE_ROW_BUDGET = 4000
    #: 통째로 읽은 자료 중 발췌가 이만큼 이하인 것에 주는 몫. 그 위는 `ANCHORED_PER_SOURCE`.
    #: 읽는 범위(SMALL_SOURCE_SCAN)와 몫을 분리해 둔다. 둘을 한 숫자로 묶으면 발췌 천 단위인
    #: Slack 채널이 작은 메모와 같은 몫을 받아 순위표를 채우고, 정작 그 메모의 뒤쪽 발췌가
    #: 잘린다(실측: SMALL_SOURCE_SCAN 을 500→1200 으로 올렸을 때 하늘IT 메모 7건 중 p.2 두
    #: 건이 그렇게 사라졌다).
    SMALL_SOURCE_QUOTA_CEILING = 500
    #: 그렇게 읽은 작은 자료에서 후보로 올릴 최대 발췌 수.
    SMALL_SOURCE_EVIDENCE = 12
    #: 이름으로 새로 찾아낸 자료를 몇 개까지 후보 자료로 받을지.
    #: 이름 검색도 상위는 발췌 많은 Slack 이 차지하므로, 작은 자료가 뒤에 붙을 자리를 남긴다.
    NAMED_SOURCE_LIMIT = 12

    #: Q-S 의 집계 행 하나가 subgraph 로 데려오는 연결 대상 수. Q-M 의 같은 처리와 맞춘다.
    #: 노드 상한 50 이 계약이라, 한 행이 넓게 데려오면 다른 행의 연결이 잘려 다시 고아가 된다.
    QS_ROW_FANOUT = 5

    #: Q-S 의 집계 수집 순서. §6 A5 가 못 박은 순서 그대로다.
    QS_COLLECTION_ORDER: tuple[str, ...] = (
        "business_domain",
        "industry",
        "need",
        "account_deal",
        "capability",
        "gap",
        "competitor",
        "evidence",
    )

    def __init__(
        self,
        graph: ReadOnlyGraph,
        *,
        router: Router | None = None,
        policy: AccessPolicy = DEMO_POLICY,
        gap_engine: GapEngine | None = None,
    ):
        self._graph = require_read_only(graph)
        self._router = router or Router()
        self._policy = policy
        self._gaps = gap_engine or GapEngine()
        self._linker = EntityLinker(self._graph.session)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def retrieve(self, question: str, *, policy: AccessPolicy | None = None) -> RetrievalResult:
        policy = policy or self._policy
        # 의문 어미를 먼저 떼고 낱말을 뽑는다. "무엇인가"·"어떻게 되나"가 검색어로 남으면
        # 원문 검색이 아무 문서나 물고 온다.
        terms = content_terms(derive_gap_subject(question))

        with self._graph.session() as session:
            focal = self._linker.link(session, question)
            decision = self._router.route(question, focal)

            if decision.retriever == "Q-M":
                collected = self._collect_multi_hop(session, question, decision, focal, terms)
            elif decision.retriever == "Q-S":
                collected = self._collect_strategic(session, question, focal, terms)
            else:
                collected = self._collect_entity_centric(session, question, focal, terms)

            return self._finish(session, question, decision, focal, terms, collected, policy)

    def run_template(self, name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._graph.session() as session:
            return run_template(session, name, params)

    # ------------------------------------------------------------------
    # Q-E
    # ------------------------------------------------------------------
    def _collect_entity_centric(
        self,
        session,
        question: str,
        focal: Sequence[EntityRef],
        terms: Sequence[str],
    ) -> dict[str, Any]:
        builder = SubgraphBuilder()
        keys = [e.key for e in focal]
        for entity in focal:
            builder.add_node(entity.key, entity.labels, entity.name, "focal")

        pool = CandidatePool()
        observations = store.observations_for(session, keys)
        claims = store.claims_for(session, keys)
        neighbours = store.neighbours_for(session, keys)
        pool.add_rows("observation", observations)
        pool.add_rows("claim", claims)

        bridged = 0
        for row in observations:
            if not row.get("event_key") or bridged >= BRIDGING_OBSERVATION_LIMIT:
                continue
            bridged += 1
            # DECISIONS A-1: Event 와 Account 사이에는 직접 엣지가 없다. Observation 이 다리다.
            builder.add_node(
                row["observation_id"], ("Observation",), row["statement"][:80], "supporting"
            )
            builder.add_node(
                row["event_key"], ("Event",), row["event_title"] or row["event_type"], "supporting"
            )
            builder.add_node(
                row["entity_key"], (row["entity_label"],), row["entity_text"], "cited"
            )
            builder.add_edge(row["observation_id"], row["event_key"], "OBSERVED_IN")
            builder.add_edge(row["observation_id"], row["entity_key"], "MENTIONS")

        criticals_shown = 0
        for row in claims:
            builder.add_node(row["entity_key"], (row["entity_label"],), row["entity_text"], "cited")
            if "critical" in (row["lane"] or []) and criticals_shown < CRITICAL_NODE_LIMIT:
                criticals_shown += 1
                builder.add_node(
                    row["claim_id"],
                    ("Claim",),
                    row["statement"][:80],
                    "supporting",
                    status=row["status"],
                )
                builder.add_edge(row["claim_id"], row["entity_key"], "ABOUT")

        for row in neighbours:
            builder.add_node(
                row["source_key"], (row["source_label"],), row["source_text"], "supporting"
            )
            builder.add_node(
                row["target_key"], (row["target_label"],), row["target_text"], "supporting"
            )
            builder.add_edge(row["source_key"], row["target_key"], row["type"])

        # 질문이 비즈니스 엔티티를 이름으로 대지 않으면 focal 이 비어 위계가 성립하지 않는다.
        # Q-S 가 '집계 첫 행을 focal 로' 하는 것과 같은 규칙을 여기에도 적용한다.
        builder.ensure_focal()

        aggregates: dict[str, Any] = {
            "observation_count": len(observations),
            "claim_count": len(claims),
            "neighbour_count": len(neighbours),
        }
        need_rows, capability_rows = self._need_and_capability_scope(session, terms, keys)
        return {
            "builder": builder,
            "evidence_ids": pool.balanced(EVIDENCE_POOL_LIMIT),
            "evidence_groups": pool.origins(),
            "aggregates": aggregates,
            "keys": keys,
            "need_rows": need_rows,
            "capability_rows": capability_rows,
            "always_gap": False,
        }

    # ------------------------------------------------------------------
    # Q-M
    # ------------------------------------------------------------------
    def _collect_multi_hop(
        self,
        session,
        question: str,
        decision: RouteDecision,
        focal: Sequence[EntityRef],
        terms: Sequence[str],
    ) -> dict[str, Any]:
        builder = SubgraphBuilder()
        name = decision.template or "common_needs_across_domains"
        params = self._template_params(name, focal, terms, question)

        rows = run_template(session, name, params)
        relaxed = False
        if not rows:
            # 용어 필터가 너무 좁으면 빈 결과가 된다. 조건을 풀되 그 사실을 남긴다.
            loose = {k: [] for k in params if k.endswith("_terms")}
            if loose:
                rows = run_template(session, name, {**params, **loose})
                relaxed = bool(rows)

        pool = CandidatePool()
        # 행 하나가 대상 여러 개(고객사 등)를 묶으면 대상마다 자리를 준다. 행 단위로만 묶으면
        # 뒤쪽 대상이 그룹 감쇠에 걸려 통째로 밀리고, 답이 그 대상을 말하지 못한다.
        for index, row in enumerate(rows):
            per_target = row.pop("evidence_ids_by_target", None)
            if per_target:
                for slot, ids in enumerate(per_target):
                    pool.add(f"template#{index}.{slot}", ids)
            else:
                pool.add(f"template#{index}", row.get("evidence_ids"))
        for row in rows:
            self._add_template_row_to_subgraph(builder, name, row)

        aggregates = {
            "template": name,
            "template_params": {k: v for k, v in params.items() if k != "limit"},
            "template_rows": rows,
            "row_count": len(rows),
            "term_filter_relaxed": relaxed,
        }
        need_rows, capability_rows = self._need_and_capability_scope(session, terms, [])
        return {
            "builder": builder,
            "evidence_ids": pool.balanced(EVIDENCE_POOL_LIMIT),
            "evidence_groups": pool.origins(),
            "aggregates": aggregates,
            "keys": [e.key for e in focal],
            "need_rows": need_rows,
            "capability_rows": capability_rows,
            "always_gap": False,
        }

    def _template_params(
        self, name: str, focal: Sequence[EntityRef], terms: Sequence[str], question: str = ""
    ) -> dict[str, Any]:
        lowered = [t.lower() for t in terms]
        if name == "accounts_with_need":
            return {"need_terms": lowered}
        if name == "industries_using_capability":
            return {"capability_terms": lowered}
        if name == "deals_by_domain":
            return {"domain_terms": lowered}
        if name == "domains_by_status":
            # 질문에 상태 낱말이 없으면 빈 목록 = 전체 상태를 보여준다.
            return {"statuses": domain_statuses_in(question)}
        if name == "feature_version_history":
            feature = next((e for e in focal if e.primary_label == "Feature"), None)
            return {"name": feature.name if feature else "", "terms": lowered}
        return {}

    @staticmethod
    def _add_template_row_to_subgraph(builder: SubgraphBuilder, name: str, row: dict[str, Any]):
        if name == "common_needs_across_domains":
            builder.add_node(row["need_id"] or row["need"], ("Need",), row["need"], "focal")
            for domain in row.get("domains") or []:
                builder.add_node(domain, ("BusinessDomain",), domain, "cited")
                builder.add_edge(row["need_id"] or row["need"], domain, "IN_DOMAIN")
            for account in (row.get("accounts") or [])[:5]:
                builder.add_node(account, ("Account",), account, "supporting")
                builder.add_edge(account, row["need_id"] or row["need"], "HAS_NEED")
        elif name == "accounts_with_need":
            builder.add_node(row["need_id"] or row["need"], ("Need",), row["need"], "focal")
            builder.add_node(row["account"], ("Account",), row["account"], "cited")
            builder.add_edge(row["account"], row["need_id"] or row["need"], "HAS_NEED")
        elif name == "industries_using_capability":
            builder.add_node(
                row["capability_id"] or row["capability"],
                ("Capability",),
                row["capability"],
                "focal",
            )
            for industry in row.get("industries") or []:
                builder.add_node(industry, ("Industry",), industry, "cited")
        elif name == "deals_by_domain":
            builder.add_node(row["bd_id"] or row["domain"], ("BusinessDomain",), row["domain"], "focal")
            for deal in (row.get("deals") or [])[:5]:
                account = deal.get("account")
                if account:
                    builder.add_node(account, ("Account",), account, "cited")
                    builder.add_edge(row["bd_id"] or row["domain"], account, "IN_DOMAIN")
        elif name == "domains_by_status":
            builder.add_node(
                row["bd_id"] or row["domain"], ("BusinessDomain",), row["domain"], "focal"
            )
            for industry in row.get("industries") or []:
                builder.add_node(industry, ("Industry",), industry, "supporting")
                builder.add_edge(row["bd_id"] or row["domain"], industry, "TARGETS")
        elif name == "feature_version_history":
            builder.add_node(row["feature_key"], ("Feature",), row["feature"], "focal")
            for capability in row.get("capabilities") or []:
                builder.add_node(capability, ("Capability",), capability, "cited")
                builder.add_edge(row["feature_key"], capability, "IMPLEMENTS")

    # ------------------------------------------------------------------
    # Q-S
    # ------------------------------------------------------------------
    def _collect_strategic(
        self,
        session,
        question: str,
        focal: Sequence[EntityRef],
        terms: Sequence[str],
    ) -> dict[str, Any]:
        """집계를 먼저 모은다. 자료를 통째로 LLM 에 넣지 않기 위한 골격이다."""
        builder = SubgraphBuilder()
        # 원문 검색으로 짐작한 엔티티는 전략 집계의 범위를 좁히는 데 쓰지 않는다.
        # 짐작 하나 때문에 "전사 어느 Capability 가 확장될까" 가 한 고객사 이야기로 쪼그라든다.
        named = [e for e in focal if e.match_kind != "fulltext"]
        keys = [e.key for e in named]
        lowered = [t.lower() for t in terms]

        relaxed: list[str] = []
        aggregates: dict[str, Any] = {"collection_order": list(self.QS_COLLECTION_ORDER)}
        aggregates["business_domain"] = self._scope_or_global(
            relaxed,
            "business_domain",
            lambda: store.business_domains(session, lowered),
            lambda: store.business_domains(session, []),
        )
        aggregates["industry"] = self._scope_or_global(
            relaxed,
            "industry",
            lambda: store.industries(session, lowered),
            lambda: store.industries(session, []),
        )
        aggregates["need"] = self._scope_or_global(
            relaxed,
            "need",
            lambda: store.needs_in_scope(session, lowered, keys),
            lambda: store.needs_in_scope(session, [], []),
        )
        aggregates["account_deal"] = store.accounts_and_deals(session, keys)
        aggregates["capability"] = self._scope_or_global(
            relaxed,
            "capability",
            lambda: store.capabilities_in_scope(session, lowered),
            lambda: store.capabilities_in_scope(session, []),
        )
        aggregates["gap"] = []  # 자리를 먼저 잡아 둔다. 판정은 후처리에서 채운다
        aggregates["competitor"] = store.competitors(session)
        aggregates["evidence"] = []
        aggregates["scope_relaxed"] = relaxed

        pool = CandidatePool()
        # 질문의 중심 엔티티가 스스로 남긴 근거를 빼먹지 않는다. 집계 행만 모으면
        # "B2B유통 BD 는 무엇을 하는가" 같은 질문에서 정작 그 BD 의 원문이 사라진다.
        pool.add_rows("observation", store.observations_for(session, keys, limit=15))
        pool.add_rows("claim", store.claims_for(session, keys, limit=15))
        pool.add_rows("business_domain", aggregates["business_domain"])
        pool.add_rows("need", aggregates["need"])
        pool.add_rows("account_deal", aggregates["account_deal"])
        pool.add_rows("capability", aggregates["capability"])

        # 집계 행은 관계를 **이름 문자열**로 들고 있는데(accounts·capabilities·needs·domains)
        # 노드 키는 need_id·capability_id·bd_id 다. 이름으로 그대로 add_edge 하면 build() 의
        # kept_ids 필터에서 조용히 전부 떨어진다. 그래서 이름 → 노드 키 사전을 먼저 만든다.
        # 상대 집계가 좁혀져 있으면 그 안에서는 이름을 못 찾는다(실측 GQ-G1: Capability 집계
        # 20행이 가리키는 Need 중 9개만 Need 집계에 있었다). 그래프에서 직접 푼다.
        capability_keys = {
            **store.keys_by_name(session, "Capability"),
            **self._keys_by_name(aggregates["capability"], "name", "capability_id"),
        }
        need_keys = {
            **store.keys_by_name(session, "Need"),
            **self._keys_by_name(aggregates["need"], "need", "need_id"),
        }
        domain_keys = self._keys_by_name(aggregates["business_domain"], "name", "bd_id")

        # Need ↔ Capability 는 두 집계가 서로를 가리킨다. 어느 쪽 행에서 걸어도 같은 관계라
        # 먼저 양쪽에서 모으고, 그다음에 노드를 세운다.
        need_node_keys = {row["need_id"] or row["need"] for row in aggregates["need"]}
        capability_label = {
            (row["capability_id"] or row["name"]): row["name"] for row in aggregates["capability"]
        }
        addressed_by: list[tuple[str, str]] = []
        for row in aggregates["need"]:
            need_key = row["need_id"] or row["need"]
            for capability in (row.get("capabilities") or [])[: self.QS_ROW_FANOUT]:
                key = capability_keys.get(capability)
                if key:
                    capability_label.setdefault(key, capability)
                    addressed_by.append((need_key, key))
        for row in aggregates["capability"]:
            capability_key = row["capability_id"] or row["name"]
            for need_name in (row.get("needs") or [])[: self.QS_ROW_FANOUT]:
                key = need_keys.get(need_name)
                # 그림에 없는 Need 로는 걸지 않는다. 그 Need 를 노드로 데려오면 노드 상한 50 을
                # 질문 범위 밖 항목이 차지해, 정작 이어진 Industry·Account 가 잘려 나간다.
                if key in need_node_keys:
                    addressed_by.append((key, capability_key))
        connected_capabilities = {cap for _, cap in addressed_by}

        # 어떤 Need 와도 이어지지 않는 Capability 는 그리지 않는다. 좁힌 집계가 얇을 때
        # `_scope_or_global` 이 전사 행으로 보충하기 때문에, 질문과 무관한 Capability 가
        # 딸려 온다. 그것들이 노드 상한을 먹으면 이어진 노드가 밀려 고아가 늘어난다
        # (실측 GQ-G1: 그리던 시절 Industry 5개가 통째로 잘리고 focal 퇴직연금이 고아였다).
        # 집계 자체는 손대지 않으므로 답변·LLM 입력에서 빠지는 것은 없다. 그림에서만 뺀다.
        for key in connected_capabilities:
            builder.add_node(key, ("Capability",), capability_label.get(key, key), "supporting")
        # 질문이 이름을 대지 않았다면 집계 첫 행이 그 질문의 초점이다. focal 이 하나도 없는
        # subgraph 는 §10 의 시각 위계가 성립하지 않는다.
        for index, row in enumerate(aggregates["business_domain"]):
            rank = "cited" if (named or index) else "focal"
            builder.add_node(row["bd_id"] or row["name"], ("BusinessDomain",), row["name"], rank)
            for industry in row.get("industries") or []:
                builder.add_node(industry, ("Industry",), industry, "supporting")
                builder.add_edge(row["bd_id"] or row["name"], industry, "TARGETS")
        # Need 행은 어느 고객사가 그 Need 를 갖는지를 이미 들고 있다. 노드만 세우고 그 필드를
        # 버리면 화면이 고아 노드 무더기가 된다.
        for row in aggregates["need"]:
            need_key = row["need_id"] or row["need"]
            builder.add_node(need_key, ("Need",), row["need"], "cited")
            for account in (row.get("accounts") or [])[: self.QS_ROW_FANOUT]:
                builder.add_node(account, ("Account",), account, "supporting")
                builder.add_edge(account, need_key, "HAS_NEED")
        for need_key, capability_key in addressed_by:
            builder.add_edge(need_key, capability_key, "ADDRESSED_BY")
        for row in aggregates["account_deal"]:
            builder.add_node(row["account"], ("Account",), row["account"], "cited")
            # 딜을 빼면 그 딜에 붙은 CRITICAL 이 합류 경로를 잃는다(store.business_neighbour_keys 주석).
            if row.get("deal"):
                builder.add_node(row["deal"], ("Deal",), row["deal"], "cited")
                builder.add_edge(row["deal"], row["account"], "WITH_ACCOUNT")
                for domain in row.get("domains") or []:
                    key = domain_keys.get(domain)
                    if key:
                        builder.add_edge(row["deal"], key, "IN_DOMAIN")
        for entity in named:
            builder.add_node(entity.key, entity.labels, entity.name, "focal")

        return {
            "builder": builder,
            "evidence_ids": pool.balanced(EVIDENCE_POOL_LIMIT),
            "evidence_groups": pool.origins(),
            "aggregates": aggregates,
            "keys": keys,
            "need_rows": aggregates["need"],
            "capability_rows": aggregates["capability"],
            "always_gap": True,
        }

    #: 좁힌 집계가 이보다 얇으면 전사 집계로 보충한다.
    SCOPE_FLOOR = 3

    @classmethod
    def _scope_or_global(
        cls, relaxed: list[str], stage: str, scoped, fallback
    ) -> list[dict[str, Any]]:
        """질문 낱말로 좁힌 집계가 얇으면 전체 집계로 **보충**한다(대체가 아니다).

        "어떤 Capability 가 여러 산업으로 확장될까" 처럼 낱말이 한글 이름과 안 겹치는 질문에서
        정작 물어본 축이 통째로 비는 것을 막는다. 보충했다는 사실은 남긴다.

        비었을 때만 되돌리면 부족하다는 것을 실측이 보여 줬다. 위 질문에서 '가능성'이 우연히
        걸린 Need 한 행이 나오는 바람에 전사 Need 집계가 통째로 사라졌고, 그 한 행은 질문과
        아무 상관이 없었다. 한 행이 우연히 걸렸다는 이유로 전사 그림을 버리지 않는다.
        """
        rows = list(scoped())
        if len(rows) >= cls.SCOPE_FLOOR:
            return rows

        seen = {cls._row_key(row) for row in rows}
        added = 0
        for row in fallback():
            key = cls._row_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        if added:
            relaxed.append(stage)
        return rows

    @staticmethod
    def _keys_by_name(
        rows: Sequence[dict[str, Any]], name_field: str, id_field: str
    ) -> dict[str, str]:
        """이름 → subgraph 노드 키. 집계 행끼리 이름으로 서로를 가리키기 때문에 필요하다."""
        out: dict[str, str] = {}
        for row in rows:
            name = row.get(name_field)
            if name:
                out.setdefault(name, row.get(id_field) or name)
        return out

    #: 집계 행을 구분하는 열쇠. 단계마다 이름이 다르므로 있는 것을 앞에서부터 쓴다.
    _ROW_KEY_FIELDS = ("bd_id", "need_id", "capability_id", "deal", "need", "account", "name")

    @classmethod
    def _row_key(cls, row: dict[str, Any]) -> str:
        for field_name in cls._ROW_KEY_FIELDS:
            value = row.get(field_name)
            if value:
                return f"{field_name}={value}"
        return repr(sorted(row.items(), key=lambda pair: pair[0]))

    def _need_and_capability_scope(
        self, session, terms: Sequence[str], keys: Sequence[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        lowered = [t.lower() for t in terms]
        if not lowered and not keys:
            return [], []
        return (
            store.needs_in_scope(session, lowered, keys, limit=10),
            store.capabilities_in_scope(session, lowered, limit=10),
        )

    # ------------------------------------------------------------------
    # 공통 후처리
    # ------------------------------------------------------------------
    def _finish(
        self,
        session,
        question: str,
        decision: RouteDecision,
        focal: Sequence[EntityRef],
        terms: Sequence[str],
        collected: dict[str, Any],
        policy: AccessPolicy,
    ) -> RetrievalResult:
        builder: SubgraphBuilder = collected["builder"]
        aggregates: dict[str, Any] = collected["aggregates"]

        # (1) sensitivity·시간 하드 필터
        fetched = store.fetch_evidence(session, collected["evidence_ids"])
        graph_evidence, filtered_any = apply_hard_filters(fetched.values(), policy)

        # (2) critical 합류 — 세 경로 전부에 붙인다.
        # focal 노드에 직접 붙은 것만 찾으면 한 칸 옆(딜·Need)에 달린 CRITICAL 이 사라진다.
        focal_keys = list(collected["keys"])
        critical_keys = list(
            dict.fromkeys(
                focal_keys
                + [n.id for n in builder.build().nodes]
                + store.business_neighbour_keys(session, focal_keys)
            )
        )
        criticals = store.critical_items(session, critical_keys)
        critical_evidence_ids = [eid for item in criticals for eid in item.evidence_ids]
        critical_fetched = store.fetch_evidence(session, critical_evidence_ids)
        critical_kept, critical_filtered = apply_hard_filters(critical_fetched.values(), policy)
        filtered_any = filtered_any or critical_filtered
        known = {e.evidence_id for e in graph_evidence}
        for item in critical_kept:
            if item.evidence_id not in known:
                graph_evidence.append(item)
                known.add(item.evidence_id)

        # (3) Evidence fulltext top-K 병행 — 합성 인용과 섞지 않는다
        raw_signals, raw_refs, raw_filtered = self._raw_signals(
            session,
            question,
            terms,
            known,
            policy,
            anchor_source_ids=self._anchor_sources(
                session, collected["keys"], focal, graph_evidence
            ),
        )
        filtered_any = filtered_any or raw_filtered

        # gap 3값 판정
        gaps = self._assess_gaps(
            question, collected, graph_evidence + raw_refs, focal
        )
        if "gap" in aggregates:
            aggregates["gap"] = [g.to_contract() for g in gaps]
        if "evidence" in aggregates:
            aggregates["evidence"] = [e.evidence_id for e in graph_evidence]

        # 근거가 어디서 왔는지 A6 에게 넘긴다. critical 과 원문 검색분도 각자 자리를 갖는다 —
        # 그러지 않으면 24건을 고를 때 집계 행들이 전부 가져가고, 딱 한 자료에만 적힌 사실이
        # 다시 떨어진다(실측: 하늘IT 메모·BD Overview Guest통제 행).
        evidence_groups = dict(collected.get("evidence_groups") or {})
        for item in criticals:
            for eid in item.evidence_ids:
                evidence_groups.setdefault(eid, f"critical#{item.id}")
        # 발췌가 몇 줄뿐인 자료는 따로 표시한다. 그 자료에 적힌 사실은 코퍼스 전체에서
        # 그게 전부라, 다른 자료와 같은 잣대로 자르면 1회 등장 정보가 매번 밀려난다.
        source_ids = [s.source_id for s in raw_signals]
        rare = store.rare_sources(session, source_ids, RARE_SOURCE_EVIDENCE)
        rare_parts = store.rare_sections(session, source_ids, RARE_SECTION_EVIDENCE)
        for signal in raw_signals:
            section = store.locator_section(signal.locator)
            if signal.source_id in rare:
                group = f"{RARE_GROUP_PREFIX}{signal.source_id}"
            elif (signal.source_id, section) in rare_parts:
                # 큰 자료 안의 몇 줄짜리 묶음. 자료 전체는 크지만 이 사실의 원본은 여기뿐이다.
                group = f"{RARE_GROUP_PREFIX}{signal.source_id}|{section}"
            else:
                group = f"{RAW_GROUP_PREFIX}{signal.source_id or signal.evidence_id}"
            evidence_groups.setdefault(signal.evidence_id, group)

        subgraph = builder.build()
        notices = {
            "results_may_be_incomplete": filtered_any,
            "critical_unverified_included": any(
                item.status != "VERIFIED" for item in criticals
            ),
            "raw_signal_count": len(raw_signals),
            "stale_labeled_count": 0,
        }

        return RetrievalResult(
            question=question,
            route=decision,
            focal_entities=list(focal),
            graph_evidence=graph_evidence,
            raw_signals=raw_signals,
            evidence_groups=evidence_groups,
            aggregates=aggregates,
            gaps=gaps,
            critical_items=criticals,
            subgraph=subgraph,
            synthesis_input=self._synthesis_input(
                question, decision, focal, aggregates, graph_evidence, gaps, criticals
            ),
            notices=notices,
        )

    def _raw_signals(
        self,
        session,
        question: str,
        terms: Sequence[str],
        already_cited: set[str],
        policy: AccessPolicy,
        anchor_source_ids: Sequence[str] = (),
    ) -> tuple[list[RawSignal], list[EvidenceRef], bool]:
        # 온톨로지 이름은 검색어에서 뺀다(text.search_terms). 넣으면 스키마를 설명한
        # 코드·설계 문서가 상위를 차지해 업무 자료가 밀린다.
        lookup = search_terms(list(terms))
        if not lookup:
            return [], [], False
        query = " ".join(lookup)
        hits = store.fulltext_evidence(session, query, self.RAW_SIGNAL_K)
        # 전역 top-K 는 흔한 낱말이 많은 문서가 차지하기 쉽다. 질문의 중심 엔티티를 다루는
        # 자료 안에서 한 번 더 찾아, 그 자료에 딱 한 번 적힌 사실이 밀려나지 않게 한다.
        seen = {h.get("evidence_id") for h in hits}
        for hit in self._anchored_hits(session, query, anchor_source_ids):
            if hit.get("evidence_id") not in seen:
                seen.add(hit["evidence_id"])
                hits.append(hit)

        ids = [h["evidence_id"] for h in hits if h.get("evidence_id")]
        if not ids:
            return [], [], False

        refs = store.fetch_evidence(session, ids)
        kept, filtered = apply_hard_filters(refs.values(), policy)
        kept_ids = {e.evidence_id for e in kept}
        in_graph = store.evidence_in_graph(session, list(kept_ids))

        signals: list[RawSignal] = []
        for hit in hits:
            eid = hit.get("evidence_id")
            if eid not in kept_ids or eid in already_cited:
                continue
            ref = refs[eid]
            signals.append(
                RawSignal(
                    evidence_id=eid,
                    source_id=ref.source_id,
                    locator=ref.locator,
                    snippet=ref.snippet,
                    match_terms=tuple(term_overlap(list(terms), ref.snippet)),
                    in_graph=in_graph.get(eid, False),
                    score=float(hit.get("score") or 0.0),
                )
            )
        return signals, kept, filtered

    def _anchor_sources(
        self,
        session,
        keys: Sequence[str],
        focal: Sequence[EntityRef],
        graph_evidence: Sequence[EvidenceRef],
    ) -> list[str]:
        """질문의 중심 엔티티를 다루는 자료 목록.

        그래프 순회로 닿는 자료만 모으면, 추출이 아무것도 만들지 못한 문서(Observation·Claim 이
        하나도 없는 자료)가 통째로 빠진다. 이름으로 원문을 한 번 더 찾아 그런 자료도 넣는다.

        본문에 이름이 없어도 **자료의 표기**에 있는 것을 함께 찾는다. 고객사 제안서는 누구에게
        낸 것인지가 파일 이름에만 적혀 있고 본문은 일반 솔루션 설명이라, 본문 검색으로는 그
        고객사 질문에서 한 건도 걸리지 않는다(실측: 소미생명 제안서, 발췌 32건 중 0건).
        """
        names = [e.name for e in focal]
        # 표기가 그 이름인 자료를 맨 앞에 세운다. 「그 회사 것인 자료」가 「그 회사를 어딘가
        # 언급한 자료」보다 강한 신호다. 뒤에 붙이면 `_anchor_selection` 의 상한에서 잘린다
        # (실측: 소미생명 제안서가 앵커 11개 중 마지막이라 선정 7개에 못 들었다).
        sources = store.sources_named(session, names)
        sources += store.sources_for_entities(session, keys)
        sources += store.sources_mentioning(session, names, limit=self.NAMED_SOURCE_LIMIT)
        if not sources:
            sources = [e.source_id for e in graph_evidence]
        return list(dict.fromkeys(s for s in sources if s))

    def _anchored_hits(
        self, session, query: str, anchor_source_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        """중심 엔티티를 다루는 자료 안에서 다시 찾되, 자료별로 몫을 나눠 준다.

        전역 순위만 쓰면 발췌가 수천 개인 Slack 이 상위를 다 가져가고, 메모·제안서처럼 작은
        자료에 딱 한 번 적힌 사실이 영영 안 나온다. 작은 자료는 통째로 읽어서 여기서 직접
        고른다. fulltext 가 전역 상위 N 을 먼저 자르고 그다음에 자료로 거르기 때문에, 작은
        자료는 그 N 안에 들지 못해 검색 결과에 아예 나타나지 않는다.
        """
        listed = list(dict.fromkeys(anchor_source_ids))
        if not listed:
            return []
        totals = store.evidence_count_by_source(session, listed)
        sources = self._anchor_selection(listed, totals)
        terms = [t for t in query.split() if t]

        # 자료의 표기가 질문 낱말과 겹치면 그 겹침은 **그 자료의 모든 발췌**에 걸린다.
        # 「소미생명 제안서」는 그 회사 이름이 본문에 없어서, 낱말 겹침만 요구하면
        # 자료를 찾아 놓고도 발췌를 한 건도 못 고른다.
        named = set(store.sources_named(session, terms, sources))
        small = self._within_row_budget(
            [s for s in sources if 0 < totals.get(s, 0) <= self.SMALL_SOURCE_SCAN], totals
        )
        by_source = self._read_small_sources(session, terms, small, totals, named=named)

        ranked: dict[str, list[dict[str, Any]]] = {}
        for source_id in sources:
            quota = self._source_quota(totals.get(source_id, 0))
            if source_id in by_source:
                ranked[source_id] = by_source[source_id][:quota]
            else:
                # 큰 자료는 자료마다 따로 묻는다. 한 번에 물으면 Slack 이 자리를 다 채운다.
                ranked[source_id] = store.fulltext_evidence_in_sources(
                    session, query, [source_id], quota
                )

        # 발췌가 몇 줄뿐인 자료는 돌아가며 담는 순번에서 아예 뺀다. 그 자료의 발췌는 코퍼스
        # 전체에서 그게 전부라 순번 경쟁에 넣으면 자료 목록이 길어질 때마다 뒤쪽 발췌가 잘린다.
        # 실측: 하늘IT 메모 7건 중 뒤쪽 두 건이 답의 근거인데, 앵커 자료가 하나 늘어나는 것만으로
        # 그 두 건이 사라졌다(PRD 1A AC-6 가 깨지는 자리).
        rare = [s for s in sources if 0 < totals.get(s, 0) <= RARE_SOURCE_EVIDENCE]
        picked: list[dict[str, Any]] = [row for s in rare for row in ranked.get(s) or []]

        rest = [s for s in sources if s not in set(rare)]
        # 나머지는 자료마다 한 건씩 돌아가며 담는다. 앞 자료부터 몫을 다 채우면 뒤 자료는
        # 상한에 걸려 한 건도 못 낸다. 자료 목록의 순서는 fulltext 점수라, 발췌가 많은 자료가
        # 앞에 서기 때문이다.
        budget = self.ANCHORED_SIGNAL_K
        taken = 0
        depth = 0
        while taken < budget:
            progressed = False
            for source_id in rest:
                items = ranked.get(source_id) or []
                if depth >= len(items):
                    continue
                picked.append(items[depth])
                taken += 1
                progressed = True
                if taken >= budget:
                    break
            if not progressed:
                break
            depth += 1
        return picked

    def _source_quota(self, total: int) -> int:
        """자료 하나가 후보로 낼 수 있는 발췌 수. 작은 자료에 더 준다.

        발췌가 열 몇 건뿐인 자료는 자르지 않는다(PRD 1A AC-6: 1회 등장 정보가 빈도 때문에
        묻히지 않는다). 발췌가 수백·수천인 자료는 몫을 좁게 준다. 그 자료의 사실은 다른
        발췌에도 여러 번 적혀 있고, 넓게 주면 순위표를 통째로 채운다.
        """
        if 0 < total <= RARE_SOURCE_EVIDENCE:
            return total
        if total <= self.SMALL_SOURCE_QUOTA_CEILING:
            return self.SMALL_SOURCE_EVIDENCE
        return self.ANCHORED_PER_SOURCE

    def _anchor_selection(
        self, listed: Sequence[str], totals: dict[str, int]
    ) -> list[str]:
        """앵커 자료를 고를 때 발췌가 몇 줄뿐인 자료를 먼저 자리에 앉힌다.

        앵커 목록의 순서는 관측 수와 fulltext 점수라 발췌가 많은 자료가 앞에 선다. 그 순서로
        앞에서 몇 개만 남기면, 발췌가 몇 줄뿐인 자료가 상한에 걸려 통째로 빠진다. 그 자료에만
        적힌 사실은 코퍼스 어디에도 없으니 빠지면 되찾을 길이 없다(PRD 1A AC-6).

        실측(2026-08-08): GQ-D1·GQ-G1 은 바다손해보험 제안서(발췌 10건)를, GQ-M2 는 카카오
        로그인 설정 문서(12건)를 이 상한에서 잃고 있었다. GQ-D6 의 하늘IT 메모(7건)는 우연히
        목록 두 번째라 살아 있었을 뿐, 큰 자료가 하나만 더 앞에 서면 같이 사라진다.
        """
        rare = [s for s in listed if 0 < totals.get(s, 0) <= RARE_SOURCE_EVIDENCE]
        rare_kept = rare[: self.RARE_ANCHOR_LIMIT]
        rest = [s for s in listed if s not in set(rare_kept)]
        return rare_kept + rest[: self.ANCHOR_SOURCE_LIMIT]

    def _within_row_budget(
        self, source_ids: Sequence[str], totals: dict[str, int]
    ) -> list[str]:
        """통째로 읽을 자료를 작은 것부터 담는다. 총 발췌 수가 예산을 넘으면 거기서 멈춘다.

        예산에서 빠진 자료는 전역 순위 경쟁으로 넘어간다. 작은 것부터 담으므로, 밀려나는
        것은 언제나 발췌가 많아 전역 상위에 스스로 오를 수 있는 자료다.
        """
        out: list[str] = []
        spent = 0
        for source_id in sorted(source_ids, key=lambda s: totals.get(s, 0)):
            cost = totals.get(source_id, 0)
            if out and spent + cost > self.SMALL_SOURCE_ROW_BUDGET:
                break
            out.append(source_id)
            spent += cost
        return out

    def _read_small_sources(
        self,
        session,
        terms: Sequence[str],
        source_ids: Sequence[str],
        totals: dict[str, int] | None = None,
        named: set[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """작은 자료들을 한 번에 읽고, 자료별로 질문 낱말이 겹치는 순서로 세운다.

        `named` 는 **자료의 표기**가 질문 낱말과 겹친 자료다. 그 자료는 본문에 낱말이 하나도
        없어도 발췌를 낸다 — 겹침은 이미 자료 이름에서 일어났다. 겹침이 있는 발췌가 앞에 서고
        나머지가 뒤에 붙으므로 순서는 그대로다.
        """
        if not source_ids:
            return {}
        named = named or set()
        totals = totals or {}
        budget = sum(min(totals.get(s, self.SMALL_SOURCE_SCAN), self.SMALL_SOURCE_SCAN) for s in source_ids)
        rows = store.evidence_of_sources(
            session, source_ids, limit=budget or self.SMALL_SOURCE_SCAN * len(source_ids)
        )
        scored: dict[str, list[tuple[int, dict[str, Any]]]] = {s: [] for s in source_ids}
        for row in rows:
            bucket = scored.get(row.get("source_id") or "")
            if bucket is None:
                continue
            overlap = term_overlap(list(terms), row.get("snippet") or "")
            if overlap or (row.get("source_id") or "") in named:
                bucket.append((len(overlap), row))
        out: dict[str, list[dict[str, Any]]] = {}
        for source_id, items in scored.items():
            items.sort(key=lambda pair: (-pair[0], pair[1]["evidence_id"]))
            out[source_id] = [
                {**row, "score": float(count)}
                for count, row in items[: self.SMALL_SOURCE_EVIDENCE]
            ]
        return out

    def _assess_gaps(
        self,
        question: str,
        collected: dict[str, Any],
        scan_pool: Sequence[EvidenceRef],
        focal: Sequence[EntityRef],
    ) -> list[GapFinding]:
        if not collected["always_gap"] and not GAP_SIGNAL.search(question):
            return []

        by_id = {e.evidence_id: e for e in scan_pool}
        need_ids = [
            eid
            for row in collected["need_rows"]
            for eid in (row.get("evidence_ids") or [])
            if eid in by_id
        ]
        capability_ids = [
            eid
            for row in collected["capability_rows"]
            for eid in (row.get("evidence_ids") or [])
            if eid in by_id
        ]
        finding = self._gaps.assess(
            subject=derive_gap_subject(question),
            need_evidence=[by_id[eid] for eid in dict.fromkeys(need_ids)],
            capability_evidence=[by_id[eid] for eid in dict.fromkeys(capability_ids)],
            scan_evidence=list(scan_pool),
        )
        return [finding]

    def _synthesis_input(
        self,
        question: str,
        decision: RouteDecision,
        focal: Sequence[EntityRef],
        aggregates: dict[str, Any],
        evidence: Sequence[EvidenceRef],
        gaps: Sequence[GapFinding],
        criticals: Sequence[Any],
    ) -> dict[str, Any]:
        """LLM 에 넣을 축약본. 원문 코퍼스가 아니라 집계 결과와 선별 발췌만 들어간다."""
        strategic = decision.retriever == "Q-S"
        return {
            "question": question,
            "route": decision.retriever,
            "focal_entities": [e.name for e in focal],
            "aggregates": self._compact_aggregates(aggregates) if strategic else {},
            "evidence": [
                {
                    "evidence_id": e.evidence_id,
                    "locator": e.locator,
                    "source_type": e.source_type,
                    "snippet": e.snippet[: self.SYNTHESIS_SNIPPET_CHARS],
                }
                for e in list(evidence)[: self.SYNTHESIS_EVIDENCE_LIMIT]
            ],
            "gaps": [g.to_contract() for g in gaps],
            "critical": [
                {"id": c.id, "status": c.status, "statement": c.statement[:200]}
                for c in list(criticals)[:CRITICAL_NODE_LIMIT]
            ],
            "estimated_label_required": strategic,
            "rules": [
                "집계에 없는 숫자를 만들지 마라.",
                "근거가 없는 것은 '확인된 자료 없음'이라고 적어라.",
                "추정은 사실과 분리해서 적어라.",
            ],
        }

    def _compact_aggregates(self, aggregates: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, value in aggregates.items():
            if key == "evidence":
                # evidence_id 원문 목록은 게이트·화면용이다. 프롬프트에 실리면 모델이
                # 옮겨 적고, 게이트가 그 안의 숫자를 무근거로 판정해 문장째 지운다
                # (api/synthesis.py 모듈 주석의 실측 사고와 같은 유형).
                continue
            if key == "collection_order":
                compact[key] = value
            elif isinstance(value, list):
                rows: list[Any] = [
                    self._compact_row(row) for row in value[: self.SYNTHESIS_ROWS_PER_STAGE]
                ]
                if len(value) > self.SYNTHESIS_ROWS_PER_STAGE:
                    # 자른 그 자리에서 밝힌다. 말없이 자르면 모델이 10행을 전부로 센다.
                    rows.append(
                        f"(전체 {len(value)}행 중 {self.SYNTHESIS_ROWS_PER_STAGE}행만 실었다."
                        " 없다는 뜻이 아니라 자리가 없어 못 실은 것이다)"
                    )
                compact[key] = rows
            else:
                compact[key] = value
        return compact

    @staticmethod
    def _compact_row(row: Any) -> Any:
        if not isinstance(row, dict):
            return row
        return {
            k: (v[:200] if isinstance(v, str) else v)
            for k, v in row.items()
            if k != "evidence_ids"
        }


__all__ = ["RetrievalService", "verify_citations"]
