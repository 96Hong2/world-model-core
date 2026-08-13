"""Snapshot Ingest 파이프라인 검증.

기대값의 근거는 계약(contracts/)·정책(config/)·마스터플랜이다. 파이프라인 출력에서
복사해 오지 않는다. 차단 케이스가 중심이다 — 통과 케이스만 보면 규칙이 죽어 있어도 모른다.

DB 를 건드리는 것은 writer 왕복 검증 하나뿐이고, 그것도 `pytest::ingest::` 네임스페이스
안에서만 노드를 만들고 지운다. 실제 데모 그래프는 이 파일이 손대지 않는다.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.pipeline.model import (  # noqa: E402
    BUSINESS_EDGE_TYPES,
    GraphBatch,
    MissingClaimEvidenceError,
    ontology_validator,
)
from ingestion.pipeline.llm_stage import (  # noqa: E402
    LLMStatusWriteRejected,
    build_candidate_schema,
    reject_status_writes,
)
from ingestion.pipeline.resolve import Resolver  # noqa: E402
from ingestion.pipeline.runner import IngestPipeline, PipelineOptions  # noqa: E402
from ingestion.pipeline.settings import Settings  # noqa: E402
from ingestion.pipeline.verdict import VerdictEngine  # noqa: E402

TEST_PREFIX = "pytest::ingest::"


def _claim_statements(batch: GraphBatch) -> dict[str, str]:
    return {
        node.props["claim_id"]: node.props.get("statement", "")
        for node in batch.nodes_by_label("Claim")
    }


# ---------------------------------------------------------------------------
# 공용 빌드 (데이터를 여러 번 읽지 않게 모듈 스코프로 캐시한다)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings.load()


def _build(*source_ids: str):
    # 통합 픽스처: 실자료(data/) 위에서 파이프라인을 돌린다. 자료를 안 받은 환경
    # (막 클론한 레포, CI)에서는 검증 대상 자체가 없으므로 skip 이 정답이다.
    from ingestion import source_registry
    from pathlib import Path as _P

    entries = source_registry.source_index()
    for sid in source_ids:
        entry = entries.get(sid)
        if entry is None or not _P(entry["path"]).exists():
            pytest.skip(f"실자료가 없어 통합 빌드를 건너뛴다: {sid}")
    return IngestPipeline(PipelineOptions(only=source_ids, use_llm=False)).build()


@pytest.fixture(scope="module")
def bd_build():
    return _build("src_bd_overview")


@pytest.fixture(scope="module")
def pain_build():
    return _build("src_pain_registry")


@pytest.fixture(scope="module")
def activity_build():
    return _build("src_sales_activity_log")


@pytest.fixture(scope="module")
def featuremap_build():
    return _build("src_featuremap_v21")


# ---------------------------------------------------------------------------
# 차단 케이스 1 — 근거 없는 비즈니스 엣지는 만들 수 없다
# ---------------------------------------------------------------------------


class TestBusinessEdgeRequiresClaim:
    def test_claim_ids_없으면_생성_거부(self):
        batch = GraphBatch()
        batch.node("Account", "테스트고객", canonical_name="테스트고객", source_ids=["src_x"])
        batch.node("Need", "need.test_x", need_id="need.test_x", need_type="pain", source_ids=["src_x"])

        with pytest.raises(MissingClaimEvidenceError):
            batch.business_edge(
                "HAS_NEED", ("Account", "테스트고객"), ("Need", "need.test_x"), claim_ids=[]
            )
        assert batch.edges_of_type("HAS_NEED") == []

    def test_지식_엣지_경로로_비즈니스_타입을_우회할_수_없다(self):
        batch = GraphBatch()
        for rel_type in sorted(BUSINESS_EDGE_TYPES):
            with pytest.raises(ValueError):
                batch.knowledge_edge(rel_type, ("Account", "a"), ("Need", "n"))

    def test_비즈니스_엣지_생성_경로는_business_edge_하나뿐(self):
        """claim_ids 를 안 받는 다른 진입점이 생기면 이 테스트가 깨진다."""
        import inspect

        from ingestion.pipeline import model

        source = inspect.getsource(model.GraphBatch)
        creators = [
            name
            for name, member in inspect.getmembers(GraphBatch, predicate=inspect.isfunction)
            if "EdgeSpec(" in inspect.getsource(member)
        ]
        assert creators == ["_add_edge"], creators
        # _add_edge 는 비공개이며 business 타입에는 claim_ids 검사를 강제한다.
        assert "MissingClaimEvidenceError" in source

    def test_claim_ids_없는_비즈니스_엣지는_계약에서도_거부된다(self):
        validator = ontology_validator()
        payload = {
            "nodes": [],
            "edges": [{"type": "HAS_NEED", "from": "a", "to": "n"}],
        }
        assert list(validator.iter_errors(payload)), "계약이 claim_ids 없는 엣지를 통과시켰다"

    def test_실제_적재_결과에_claim_ids_없는_비즈니스_엣지가_0건(self, pain_build):
        offenders = [
            edge
            for edge in pain_build.batch.edges.values()
            if edge.type in BUSINESS_EDGE_TYPES and not edge.props.get("claim_ids")
        ]
        assert offenders == []


# ---------------------------------------------------------------------------
# 차단 케이스 2 — 일반화 Claim 은 자동 VERIFIED 가 되지 않는다 (BT-1)
# ---------------------------------------------------------------------------


class TestGeneralizationNotVerified:
    def test_시장성_평가는_CANDIDATE_이고_Observation_은_확정(self, bd_build):
        market_claims = [
            node
            for node in bd_build.batch.nodes_by_label("Claim")
            if node.props.get("claim_kind") == "market_assessment"
        ]
        assert market_claims, "BD Overview 시장사이즈에서 market_assessment Claim 이 하나도 안 나왔다"
        assert {node.props["status"] for node in market_claims} == {"CANDIDATE"}

        # 같은 Evidence 에서 나온 Observation 은 '이 자료에 이렇게 적혀 있다' 로 확정된다.
        observations = bd_build.batch.nodes_by_label("Observation")
        market_evidence = {
            ev for node in market_claims for ev in node.props.get("evidence_ids", [])
        }
        observed = {ev for node in observations for ev in node.props.get("evidence_ids", [])}
        assert market_evidence & observed

    def test_B2B유통_계열_평가가_VERIFIED_로_올라가지_않는다(self, bd_build):
        targets = ("제조", "식자재유통", "물류", "B2B유통")
        verified = [
            node
            for node in bd_build.batch.nodes_by_label("Claim")
            if node.props.get("status") == "VERIFIED"
            and node.props.get("claim_kind") in ("market_assessment", "strategic_judgment")
            and any(name in node.props.get("statement", "") for name in targets)
        ]
        assert verified == []

    def test_규칙표_밖의_조합은_VERIFIED_가_될_수_없다(self, settings):
        engine = VerdictEngine(settings)
        verdict = engine.decide(source_type="bd_registry", claim_kind="market_assessment")
        assert verdict.status == "CANDIDATE"
        assert verdict.observation_confirmed is True
        assert verdict.verified_allowed is False

    def test_정본_명시_사실은_VERIFIED_가_된다(self, settings):
        """대조군. 이게 없으면 '전부 CANDIDATE' 로 굳어 있어도 위 테스트가 통과한다."""
        engine = VerdictEngine(settings)
        verdict = engine.decide(source_type="release_spec", claim_kind="product_spec")
        assert verdict.status == "VERIFIED"

    def test_기능맵_product_spec_은_실제로_VERIFIED_가_적재된다(self, featuremap_build):
        verified = [
            node
            for node in featuremap_build.batch.nodes_by_label("Claim")
            if node.props.get("status") == "VERIFIED"
        ]
        assert len(verified) > 100, len(verified)


# ---------------------------------------------------------------------------
# 차단 케이스 3 — 같은 뜻의 Need 표현은 canonical Need 하나로 모인다
# ---------------------------------------------------------------------------


class TestNeedCanonicalization:
    # config/need-taxonomy.yaml need.channel_control_absent 의 raw_expressions 에서
    # 서로 다른 고객사 3곳의 표현을 골랐다(카톡 통제 / 개인 메신저 / 대화 관리).
    RAW_THREE = (
        "업무 대화가 개인 카톡에 산재해 회사가 관리·통제 못 함",
        "매니저 개인 카톡이라 회사가 소통 공간을 들여다볼 수 없음",
        "영업 소통이 개인폰·개인카톡에 산재해 통화·문자·고객정보를 회사가 관리 못 함",
    )
    CANONICAL = "need.channel_control_absent"

    def test_표현_3종이_같은_canonical_로_매핑된다(self, settings):
        resolver = Resolver(settings)
        mapped = {resolver.map_need(raw).need_id for raw in self.RAW_THREE}
        assert mapped == {self.CANONICAL}

    def test_그래프에는_canonical_Need_가_하나만_생긴다(self, pain_build):
        needs = [
            node
            for node in pain_build.batch.nodes_by_label("Need")
            if node.props.get("need_id") == self.CANONICAL
        ]
        assert len(needs) == 1
        assert needs[0].props.get("canonical") is True

    def test_원문_표현은_Evidence_에_그대로_남는다(self, pain_build):
        snippets = [
            node.props.get("snippet", "")
            for node in pain_build.batch.nodes_by_label("Evidence")
        ]
        for raw in self.RAW_THREE:
            assert any(raw in snippet for snippet in snippets), raw

    def test_서로_다른_고객사가_같은_Need_를_가리킨다(self, pain_build):
        accounts = {
            edge.start[1]
            for edge in pain_build.batch.edges_of_type("HAS_NEED")
            if edge.end[1] == self.CANONICAL
        }
        assert len(accounts) >= 2, accounts

    def test_LLM_매핑_제안이_그래프에_실제로_반영된다(self, settings):
        """제안만 받고 버리면 비용만 쓰고 Cross-BD 분석은 그대로 깨진다."""
        from ingestion.pipeline.direct import LoadContext, add_evidence, build_feature_capability_index
        from ingestion.pipeline.model import GraphBatch
        from ingestion.pipeline.verdict import CriticalityEngine, VerdictEngine

        resolver = Resolver(settings)
        ctx = LoadContext(
            settings=settings,
            resolver=resolver,
            verdicts=VerdictEngine(settings),
            criticality=CriticalityEngine(settings),
            batch=GraphBatch(),
            feature_capability_index=build_feature_capability_index(settings),
        )
        ctx.source_type["src_pain_registry"] = "pain_registry"
        record = {
            "evidence_id": "ev_llm_mapping_probe",
            "source_id": "src_pain_registry",
            "locator": "Pain Point 목록!E999",
            "excerpt": "사전에 없는 표현이지만 같은 문제를 말한다",
            "excerpt_hash": "probe1hash",
            "structured": {"record_kind": "pain_row", "고객사": "카파전선"},
            "authored_at": None,
        }
        evidence_ref = add_evidence(ctx, record)

        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._apply_need_proposals(
            ctx,
            {record["excerpt"]: self.CANONICAL},
            {record["excerpt"]: [record]},
            {record["evidence_id"]: evidence_ref},
        )

        edges = [
            edge
            for edge in ctx.batch.edges_of_type("HAS_NEED")
            if edge.end[1] == self.CANONICAL
        ]
        assert edges, "LLM 이 이어 준 Need 가 그래프에 반영되지 않았다"
        claim_ids = {cid for edge in edges for cid in edge.props["claim_ids"]}
        statuses = {
            node.props["status"]
            for node in ctx.batch.nodes_by_label("Claim")
            if node.props["claim_id"] in claim_ids
        }
        assert statuses <= {"CANDIDATE", "CRITICAL", "UNVERIFIED"}, statuses
        assert "VERIFIED" not in statuses

    def test_taxonomy_에_없는_id_제안은_무시된다(self, settings):
        """대조군. 신규 canonical 을 만들어 내지 않는다."""
        from ingestion.pipeline.direct import LoadContext, add_evidence, build_feature_capability_index
        from ingestion.pipeline.model import GraphBatch
        from ingestion.pipeline.verdict import CriticalityEngine, VerdictEngine

        ctx = LoadContext(
            settings=settings,
            resolver=Resolver(settings),
            verdicts=VerdictEngine(settings),
            criticality=CriticalityEngine(settings),
            batch=GraphBatch(),
            feature_capability_index=build_feature_capability_index(settings),
        )
        ctx.source_type["src_pain_registry"] = "pain_registry"
        record = {
            "evidence_id": "ev_llm_mapping_probe2",
            "source_id": "src_pain_registry",
            "locator": "Pain Point 목록!E998",
            "excerpt": "표현",
            "excerpt_hash": "probe2hash",
            "structured": {"record_kind": "pain_row", "고객사": "카파전선"},
            "authored_at": None,
        }
        evidence_ref = add_evidence(ctx, record)
        IngestPipeline(PipelineOptions(use_llm=False), settings)._apply_need_proposals(
            ctx,
            {record["excerpt"]: "need.존재하지_않는_항목"},
            {record["excerpt"]: [record]},
            {record["evidence_id"]: evidence_ref},
        )
        assert ctx.batch.edges_of_type("HAS_NEED") == []
        assert ctx.batch.nodes_by_label("Need") == []

    def test_사전에_없는_표현은_신규_canonical_을_만들지_않는다(self, settings):
        resolver = Resolver(settings)
        mapping = resolver.map_need("이 문장은 어떤 taxonomy 항목에도 없는 표현이다")
        assert mapping.need_id is None
        assert mapping.canonical is False
        assert mapping.unmapped_raw


# ---------------------------------------------------------------------------
# 차단 케이스 4 — 오병합 금지
# ---------------------------------------------------------------------------


class TestDoNotMerge:
    def test_마바손해보험과_마바캐피탈은_다른_Account(self, settings):
        resolver = Resolver(settings)
        a = resolver.resolve_account("마바손해보험")
        b = resolver.resolve_account("마바캐피탈")
        assert a.canonical_name != b.canonical_name

    def test_config_의_do_not_merge_35쌍_전부가_분리된다(self, settings):
        resolver = Resolver(settings)
        pairs = settings.aliases["do_not_merge"]
        assert len(pairs) == 35
        for entry in pairs:
            left, right = entry["pair"]
            assert (
                resolver.resolve_account(left).canonical_name
                != resolver.resolve_account(right).canonical_name
            ), entry["pair"]

    def test_별칭은_정상적으로_합쳐진다(self, settings):
        """대조군. 전부 분리만 하면 위 테스트는 항상 통과한다."""
        resolver = Resolver(settings)
        assert resolver.resolve_account("가나은행").canonical_name == "가나은행"
        assert resolver.resolve_account("모다패션").canonical_name == "모다패션"

    def test_활동일지_적재에서_두_Account_가_모두_존재한다(self, activity_build):
        names = {
            node.props.get("canonical_name")
            for node in activity_build.batch.nodes_by_label("Account")
        }
        assert "마바손해보험" in names
        assert "마바캐피탈" in names


# ---------------------------------------------------------------------------
# 차단 케이스 5 — LLM 은 status 를 쓸 수 없다 (BT-3 / 계약 I4)
# ---------------------------------------------------------------------------


class TestLLMCannotWriteStatus:
    def test_추출_스키마에_status_를_넣을_수_없다(self):
        from llm.extraction import ExtractionSchemaError

        with pytest.raises(ExtractionSchemaError):
            build_candidate_schema({"status": {"type": "string"}})

    def test_status_를_담은_응답은_거부된다(self):
        items = [
            {
                "source_id": "src_x",
                "locator": "slack:C1/1",
                "evidence_quote": "원문",
                "account": "가나손해보험",
                "status": "VERIFIED",
            }
        ]
        with pytest.raises(LLMStatusWriteRejected):
            reject_status_writes(items)

    def test_상태와_무관한_필드는_통과한다(self):
        items = [
            {
                "source_id": "src_x",
                "locator": "slack:C1/1",
                "evidence_quote": "원문",
                "account": "가나손해보험",
            }
        ]
        assert reject_status_writes(items) == items

    def test_LLM_추출물은_규칙표와_무관하게_CANDIDATE(self, settings):
        engine = VerdictEngine(settings)
        verdict = engine.decide(
            source_type="release_spec", claim_kind="product_spec", extractor="llm"
        )
        assert verdict.status == "CANDIDATE"
        assert verdict.verified_allowed is False


# ---------------------------------------------------------------------------
# 차단 케이스 6 — '상담' 업무 도메인에는 IN_DOMAIN 엣지를 만들지 않는다
# ---------------------------------------------------------------------------


class TestCounselDomainNotLinked:
    def test_config_가_상담을_사람_확인_대상으로_둔다(self, settings):
        entry = settings.activity_domain_entry("상담")
        assert entry["needs_human_confirm"] is True
        assert entry.get("bd_ids") == ["bd.contact_center"]

    def test_상담_행에서_IN_DOMAIN_엣지가_나오지_않는다(self, activity_build):
        assert activity_build.unmapped["activity_domain"].get("상담", 0) > 0
        statements = _claim_statements(activity_build.batch)
        blocked = [
            statements[claim_id]
            for edge in activity_build.batch.edges_of_type("IN_DOMAIN")
            for claim_id in edge.props["claim_ids"]
            if "업무 도메인 '상담'" in statements.get(claim_id, "")
            or "업무 도메인 '상담&협업'" in statements.get(claim_id, "")
        ]
        assert blocked == []

    def test_IN_DOMAIN_근거_claim_에서_도메인_원문을_되짚을_수_있다(self, activity_build):
        """대조군. 근거 문장에 도메인 원문이 없으면 위 차단 검사가 항상 빈 목록이 된다."""
        statements = _claim_statements(activity_build.batch)
        traceable = [
            claim_id
            for edge in activity_build.batch.edges_of_type("IN_DOMAIN")
            for claim_id in edge.props["claim_ids"]
            if "업무 도메인 '" in statements.get(claim_id, "")
        ]
        assert len(traceable) >= len(activity_build.batch.edges_of_type("IN_DOMAIN"))

    def test_근거가_확실한_도메인은_연결된다(self, activity_build):
        """대조군. IN_DOMAIN 이 통째로 안 만들어지고 있어도 위 테스트는 통과한다."""
        linked = {
            edge.end[1]
            for edge in activity_build.batch.edges_of_type("IN_DOMAIN")
        }
        assert "bd.insurance_planning_support" in linked
        assert "bd.auto_finance" in linked


# ---------------------------------------------------------------------------
# ⑨ idempotent — 같은 입력을 두 번 넣으면 노드·엣지가 그대로다
# ---------------------------------------------------------------------------


class TestIdempotent:
    def test_두_번_빌드하면_노드_엣지_집합이_같다(self):
        first = _build("src_bd_overview", "src_pain_registry")
        second = _build("src_bd_overview", "src_pain_registry")
        assert set(first.batch.nodes) == set(second.batch.nodes)
        assert set(first.batch.edges) == set(second.batch.edges)
        assert first.batch.fingerprint() == second.batch.fingerprint()

    def test_writer_왕복_2회_실행시_수가_불변(self):
        from graph.connection import writable_graph

        from ingestion.pipeline.writer import GraphWriter

        batch = GraphBatch()
        source_key = TEST_PREFIX + "source"
        ev_key = TEST_PREFIX + "evidence"
        claim_key = TEST_PREFIX + "claim"
        acct_key = TEST_PREFIX + "account"
        need_key = TEST_PREFIX + "need"

        batch.node(
            "Source",
            source_key,
            source_id="src_pytest_ingest",
            source_type="pain_registry",
            canonical_location="pytest://ingest",
            sensitivity="internal",
            visibility="internal",
            source_ids=["src_pytest_ingest"],
        )
        batch.node(
            "Evidence",
            ev_key,
            evidence_id="ev_pytest_ingest",
            source_id="src_pytest_ingest",
            locator="pytest!A1",
            snippet="개인 카톡 기반 소통으로 회사가 들여다볼 수 없음",
            source_ids=["src_pytest_ingest"],
        )
        batch.node(
            "Claim",
            claim_key,
            claim_id="clm_pytest_ingest",
            statement="테스트 주장",
            status="CANDIDATE",
            lane=["default"],
            claim_kind="customer_generalization",
            evidence_ids=["ev_pytest_ingest"],
            source_ids=["src_pytest_ingest"],
        )
        batch.node(
            "Account",
            acct_key,
            canonical_name="pytest 테스트회사",
            account_kind="customer",
            source_ids=["src_pytest_ingest"],
        )
        batch.node(
            "Need",
            need_key,
            need_id="need.pytest_ingest",
            name="테스트 니즈",
            need_type="pain",
            canonical=True,
            source_ids=["src_pytest_ingest"],
        )
        batch.knowledge_edge("FROM_SOURCE", ("Evidence", ev_key), ("Source", source_key))
        batch.knowledge_edge("SUPPORTS", ("Evidence", ev_key), ("Claim", claim_key))
        batch.business_edge(
            "HAS_NEED",
            ("Account", acct_key),
            ("Need", need_key),
            claim_ids=["clm_pytest_ingest"],
        )

        errors = list(ontology_validator().iter_errors(batch.to_payload()))
        assert errors == [], errors[0].message if errors else ""

        with writable_graph() as graph:
            writer = GraphWriter(graph, run_id="pytest-ingest")
            try:
                writer.purge_prefix(TEST_PREFIX)
                writer.write(batch)
                first = writer.count_prefix(TEST_PREFIX)
                writer.write(batch)
                second = writer.count_prefix(TEST_PREFIX)
            finally:
                removed = writer.purge_prefix(TEST_PREFIX)

        assert first == {"nodes": 5, "edges": 3}, first
        assert second == first
        assert removed == 5


class TestPatchWrite:
    """부분 적재(patch)는 기존 노드의 값을 덮으면 안 된다.

    인메모리 병합(_merge_props)은 「리스트는 합집합, 스칼라는 빈 칸만」인데,
    기본 쓰기(SET n +=)는 마지막 쓴 값이 이긴다. --only 부분 적재·백필 스크립트가
    1건짜리 source_ids 로 기존 리스트를 덮어 온 결함의 회귀 테스트다.
    """

    PREFIX = TEST_PREFIX + "patch::"

    def _write(self, batch, *, patch=False, force_props=frozenset()):
        from graph.connection import writable_graph

        from ingestion.pipeline.writer import GraphWriter

        with writable_graph() as graph:
            writer = GraphWriter(graph, run_id="pytest-patch")
            writer.write(batch, patch=patch, force_props=force_props)

    def _node_props(self, key):
        from graph.connection import writable_graph

        with writable_graph() as graph, graph.read_session() as session:
            row = session.run(
                "MATCH (n {natural_key: $k}) RETURN properties(n) AS p", k=key
            ).single()
            return dict(row["p"]) if row else None

    def _edge_props(self, start, end):
        from graph.connection import writable_graph

        with writable_graph() as graph, graph.read_session() as session:
            row = session.run(
                "MATCH ({natural_key: $s})-[r]->({natural_key: $e}) "
                "RETURN properties(r) AS p",
                s=start,
                e=end,
            ).single()
            return dict(row["p"]) if row else None

    def _purge(self):
        from graph.connection import writable_graph

        from ingestion.pipeline.writer import GraphWriter

        with writable_graph() as graph:
            GraphWriter(graph, run_id="pytest-patch").purge_prefix(self.PREFIX)

    def _full_batch(self):
        batch = GraphBatch()
        batch.node(
            "Account",
            self.PREFIX + "account",
            canonical_name="pytest 패치회사",
            account_kind="customer",
            raw_names=["패치회사", "(주)패치회사"],
            source_ids=["src_pytest_full_a", "src_pytest_full_b"],
        )
        batch.node(
            "Need",
            self.PREFIX + "need",
            need_id="need.pytest_patch",
            name="패치 니즈",
            need_type="pain",
            canonical=True,
            source_ids=["src_pytest_full_a"],
        )
        batch.node(
            "Deal",
            self.PREFIX + "deal",
            deal_key="pytest-patch-deal",
            account_name="pytest 패치회사",
            stage_system="wip",
            amount_raw="0",
            source_ids=["src_pytest_full_a"],
        )
        batch.business_edge(
            "HAS_NEED",
            ("Account", self.PREFIX + "account"),
            ("Need", self.PREFIX + "need"),
            claim_ids=["clm_pytest_full"],
        )
        return batch

    def _partial_batch(self):
        """부분 실행이 같은 노드를 다시 만났을 때의 모습: 리스트가 1건짜리다."""
        batch = GraphBatch()
        batch.node(
            "Account",
            self.PREFIX + "account",
            canonical_name="pytest 패치회사",
            account_kind="customer",
            raw_names=["패치회사엔지"],
            source_ids=["src_pytest_inc"],
        )
        batch.node(
            "Need",
            self.PREFIX + "need",
            need_id="need.pytest_patch",
            name="패치 니즈",
            need_type="pain",
            canonical=True,
            source_ids=["src_pytest_inc"],
        )
        batch.node(
            "Deal",
            self.PREFIX + "deal",
            deal_key="pytest-patch-deal",
            account_name="pytest 패치회사",
            amount_raw="2.99억",
            amount_krw=299_000_000,
            source_ids=["src_pytest_inc"],
        )
        batch.business_edge(
            "HAS_NEED",
            ("Account", self.PREFIX + "account"),
            ("Need", self.PREFIX + "need"),
            claim_ids=["clm_pytest_inc"],
        )
        return batch

    def test_patch_는_리스트를_합집합으로_합친다(self):
        self._purge()
        try:
            self._write(self._full_batch())
            self._write(self._partial_batch(), patch=True)
            acct = self._node_props(self.PREFIX + "account")
            assert acct["source_ids"] == [
                "src_pytest_full_a",
                "src_pytest_full_b",
                "src_pytest_inc",
            ], acct["source_ids"]
            assert acct["raw_names"] == ["패치회사", "(주)패치회사", "패치회사엔지"]
        finally:
            self._purge()

    def test_patch_는_스칼라의_빈_칸만_채우고_있는_값은_지킨다(self):
        self._purge()
        try:
            self._write(self._full_batch())
            self._write(self._partial_batch(), patch=True)
            deal = self._node_props(self.PREFIX + "deal")
            # 빈 칸(amount_krw)은 채워지고, 있는 값(amount_raw='0'·stage_system)은 남는다
            assert deal["amount_krw"] == 299_000_000
            assert deal["amount_raw"] == "0"
            assert deal["stage_system"] == "wip"
        finally:
            self._purge()

    def test_patch_force_props_는_명시한_속성만_덮는다(self):
        """금액 짝(amount_raw·amount_krw)은 함께 바꿔야 어긋나지 않는다."""
        self._purge()
        try:
            self._write(self._full_batch())
            self._write(
                self._partial_batch(),
                patch=True,
                force_props=frozenset({"amount_raw", "amount_krw"}),
            )
            deal = self._node_props(self.PREFIX + "deal")
            assert deal["amount_raw"] == "2.99억"
            assert deal["amount_krw"] == 299_000_000
            assert deal["stage_system"] == "wip"  # force 밖 스칼라는 그대로
        finally:
            self._purge()

    def test_patch_는_새_노드를_온전히_만든다(self):
        self._purge()
        try:
            self._write(self._partial_batch(), patch=True)
            acct = self._node_props(self.PREFIX + "account")
            assert acct["source_ids"] == ["src_pytest_inc"]
            assert acct["canonical_name"] == "pytest 패치회사"
        finally:
            self._purge()

    def test_patch_는_비즈니스_엣지의_claim_ids_를_합집합으로_합친다(self):
        self._purge()
        try:
            self._write(self._full_batch())
            self._write(self._partial_batch(), patch=True)
            edge = self._edge_props(self.PREFIX + "account", self.PREFIX + "need")
            assert edge["claim_ids"] == ["clm_pytest_full", "clm_pytest_inc"]
        finally:
            self._purge()

    def test_기본_replace_모드는_기존_동작_그대로_리스트를_교체한다(self):
        """전체 재적재의 정본 갱신 동작(특성 테스트). patch 와의 차이를 못 박는다."""
        self._purge()
        try:
            self._write(self._full_batch())
            self._write(self._partial_batch())  # patch 아님
            acct = self._node_props(self.PREFIX + "account")
            assert acct["source_ids"] == ["src_pytest_inc"]
        finally:
            self._purge()


# ---------------------------------------------------------------------------
# 그 밖의 계약 준수
# ---------------------------------------------------------------------------


class TestContractCompliance:
    def test_적재_payload_가_온톨로지_계약을_통과한다(self, bd_build):
        errors = list(ontology_validator().iter_errors(bd_build.batch.to_payload()))
        assert errors == [], errors[0].message if errors else ""

    def test_sensitivity_없는_소스는_거부된다(self, settings):
        from ingestion.pipeline.runner import SourceRegistrationError, register_source

        record = {
            "source_id": "src_no_sensitivity",
            "source_type": "proposal",
            "canonical_location": "/tmp/x",
            "content_hash": "0" * 64,
        }
        with pytest.raises(SourceRegistrationError):
            register_source(record, settings)

    def test_BD_는_config_시드_수만큼_생긴다(self, bd_build, settings):
        domains = bd_build.batch.nodes_by_label("BusinessDomain")
        assert len(domains) == len(settings.bd_seed["business_domains"])
        assert len(domains) >= 15

    def test_Evidence_는_전부_Source_에_연결된다(self, pain_build):
        evidence = {node.natural_key for node in pain_build.batch.nodes_by_label("Evidence")}
        linked = {
            edge.start[1] for edge in pain_build.batch.edges_of_type("FROM_SOURCE")
        }
        assert evidence == linked

    def test_모든_Claim_이_Evidence_를_갖는다(self, activity_build):
        for node in activity_build.batch.nodes_by_label("Claim"):
            assert node.props.get("evidence_ids"), node.natural_key

    def test_비즈니스_엣지의_claim_ids_가_실재하는_Claim_을_가리킨다(self, activity_build):
        """Gate 1A 의 evidence traceability — 깨진 인용이 하나라도 있으면 실패한다."""
        batch = activity_build.batch
        known = {node.props["claim_id"] for node in batch.nodes_by_label("Claim")}
        dangling = [
            (edge.type, claim_id)
            for edge in batch.edges.values()
            if edge.type in BUSINESS_EDGE_TYPES
            for claim_id in edge.props["claim_ids"]
            if claim_id not in known
        ]
        assert dangling == []

    def test_Claim_의_evidence_ids_가_실재하는_Evidence_를_가리킨다(self, activity_build):
        batch = activity_build.batch
        known = {node.props["evidence_id"] for node in batch.nodes_by_label("Evidence")}
        dangling = [
            (node.props["claim_id"], evidence_id)
            for node in batch.nodes_by_label("Claim")
            for evidence_id in node.props.get("evidence_ids") or []
            if evidence_id not in known
        ]
        assert dangling == []

    def test_criticality_가_실제로_발화한다(self, activity_build):
        """대조군 — 규칙이 전부 죽어 있으면 critical 이 0 이 되고 이 테스트가 잡는다."""
        critical = [
            node
            for node in activity_build.batch.nodes_by_label("Claim")
            if "critical" in (node.props.get("lane") or [])
        ]
        assert critical, "criticality 규칙이 한 건도 발화하지 않았다"
        assert {node.props["status"] for node in critical} <= {"CRITICAL", "UNVERIFIED"}

    def test_전략계정_규칙이_가나손해보험에서_발화한다(self, settings):
        from ingestion.pipeline.verdict import CriticalityEngine

        engine = CriticalityEngine(settings)
        fired = engine.evaluate(
            applies_to="Claim",
            text="가나손해보험 제안 진행",
            fields={"account_canonical": "가나손해보험"},
        )
        assert "CR-STRATEGIC-ACCOUNT" in fired

    def test_비전략계정에서는_그_규칙이_발화하지_않는다(self, settings):
        from ingestion.pipeline.verdict import CriticalityEngine

        engine = CriticalityEngine(settings)
        fired = engine.evaluate(
            applies_to="Claim",
            text="테스트 문장",
            fields={"account_canonical": "이름없는회사"},
        )
        assert "CR-STRATEGIC-ACCOUNT" not in fired


class TestStatsShape:
    def test_stats_가_필요한_집계를_전부_담는다(self, pain_build):
        stats = pain_build.batch.summary()
        assert set(stats) >= {"nodes_by_label", "edges_by_type", "claims_by_status", "critical_claims"}
        assert stats["nodes_by_label"].get("Evidence", 0) > 0
        assert isinstance(stats["critical_claims"], int)

    def test_unmapped_기록이_남는다(self, activity_build):
        assert "activity_domain" in activity_build.unmapped
        assert isinstance(activity_build.unmapped["activity_domain"], dict)


# ---------------------------------------------------------------------------
# CLI — 표 출력과 실행 기록
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("bwm_ingest_cli", ROOT / "scripts" / "ingest.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestCLI:
    def test_run_stats_reset_세_명령이_있다(self, cli):
        parser_actions = cli.main.__doc__  # noqa: F841 (문서가 아니라 파싱으로 확인한다)
        for argv, command in (
            (["run", "--no-llm", "--dry-run"], "run"),
            (["stats"], "stats"),
            (["reset", "--yes"], "reset"),
        ):
            args = _parse(cli, argv)
            assert args.command == command

    def test_run_옵션이_파이프라인_설정으로_이어진다(self, cli):
        args = _parse(cli, ["run", "--only", "src_bd_overview", "--budget", "3", "--max-calls", "50"])
        assert args.only == ["src_bd_overview"]
        assert args.budget == 3.0
        assert args.max_calls == 50
        assert args.no_llm is False  # --no-llm 을 안 주면 LLM 을 쓴다

    def test_표에_합계가_붙는다(self, cli):
        rendered = cli.table("라벨별 노드", [("Evidence", 11), ("Claim", 4)], headers=("라벨", "노드 수"))
        assert "Evidence" in rendered and "11" in rendered
        assert "합계" in rendered and "15" in rendered

    def test_빈_표는_없음으로_표시된다(self, cli):
        assert "(없음)" in cli.table("빈 표", [], headers=("a", "b"))

    def test_건너뛴_LLM_작업이_실행기록에_남는다(self, cli):
        """예산 상한에 걸린 것을 조용히 넘기지 않는다."""
        from ingestion.pipeline.llm_stage import LLMBudget, LLMStageReport

        report = LLMStageReport(budget=LLMBudget(max_usd=8.0, max_calls=400))
        report.budget.record(0.02, cache_hit=False)
        report.skipped["meeting_note"] = 42
        summary = cli.llm_summary(report)
        assert summary["skipped"] == {"meeting_note": 42}
        assert summary["calls"] == 1
        assert summary["spent_usd"] == 0.02

    def test_캐시_히트는_신규_호출로_세지_않는다(self, cli):
        """재실행할 때마다 호출 수가 부풀면 예산 판단이 틀어진다."""
        from ingestion.pipeline.llm_stage import LLMBudget, LLMStageReport

        report = LLMStageReport(budget=LLMBudget())
        report.budget.record(0.0, cache_hit=True)
        report.budget.record(0.0, cache_hit=True)
        report.budget.record(0.05, cache_hit=False)
        summary = cli.llm_summary(report)
        assert summary["calls"] == 3 and summary["cache_hits"] == 2
        assert summary["calls"] - summary["cache_hits"] == 1

    def test_실행기록_파일_경로가_data_cache_안이다(self, cli):
        assert cli.PROGRESS_PATH.parent.name == "cache"
        assert cli.PROGRESS_PATH.name == "ingest_progress.json"

    def test_추출이_매핑_몫을_먹지_않는다(self):
        """매핑 단계는 순서상 맨 마지막이라 예산을 늘 굶는다.

        미팅 발췌가 2,770건이라 식욕이 무제한이고, 그 뒤에 오는 니즈 매핑은 어떤 상한을
        걸어도 차례가 오기 전에 예산이 빈다. 매핑 몫을 떼어 둬야 돈이 적은 쪽에 먼저 간다
        (매핑 1콜 $0.07 로 HAS_NEED 이 생기고, 추출 1콜 $0.086 은 관측 몇 줄이다).
        """
        from ingestion.pipeline.llm_stage import LLMBudget

        budget = LLMBudget(max_usd=3.0, reserved_usd=2.5)
        budget.record(0.6, cache_hit=False)

        assert budget.can_spend() is False, "추출이 매핑 몫까지 쓰고 있다"
        assert budget.can_spend(use_reserve=True) is True, "매핑이 자기 몫을 못 쓴다"

    def test_떼어_둔_몫이_전체_상한을_넘지_않는다(self):
        """예산보다 큰 몫을 떼면 추출이 한 콜도 못 하고 굶는다."""
        from ingestion.pipeline.llm_stage import LLMBudget

        budget = LLMBudget(max_usd=1.0, reserved_usd=5.0)
        assert budget.reserved_usd <= budget.max_usd
        assert budget.can_spend(use_reserve=True) is True

    def test_매핑_단계가_떼어_둔_몫을_쓴다(self):
        """예산 계산만 맞고 매핑이 그걸 안 쓰면 아무 효과가 없다."""
        import inspect

        from ingestion.pipeline.llm_stage import LLMStage

        source = inspect.getsource(LLMStage.propose_mappings)
        assert "use_reserve=True" in source, "매핑이 떼어 둔 몫을 쓰지 않는다"

    def test_LLM_을_안_돌린_소스가_이름으로_드러난다(self, cli, capsys):
        """예산 때문에 건너뛴 소스를 조용히 넘기지 않는다."""
        from ingestion.pipeline.settings import PARSED_DIR

        if not list(PARSED_DIR.glob("*.source.json")):
            pytest.skip("파싱 산출물(data/parsed)이 없어 커버리지 표시를 검증할 수 없다")
        cli.print_llm_coverage({"llm_done_sources": []})
        out = capsys.readouterr().out
        assert "아직 LLM 추출을 돌리지 않은 소스" in out
        assert "src_bd_overview" in out

    def test_전부_돌렸으면_남은_소스_목록이_안_뜬다(self, cli, capsys):
        """대조군. 목록이 항상 뜨면 위 검사가 의미를 잃는다."""
        from ingestion.pipeline.settings import PARSED_DIR

        every = [p.name[: -len(".source.json")] for p in PARSED_DIR.glob("*.source.json")]
        cli.print_llm_coverage({"llm_done_sources": every})
        out = capsys.readouterr().out
        assert "아직 LLM 추출을 돌리지 않은 소스" not in out

    def test_reset_은_이_파이프라인이_만든_노드만_지운다(self):
        """남이 넣은 노드까지 지우면 되돌릴 수 없다. 삭제 조건을 못 박아 둔다."""
        import inspect

        from ingestion.pipeline.writer import GraphWriter

        source = inspect.getsource(GraphWriter.reset)
        assert "n.pipeline_run_id IS NOT NULL" in source
        assert "MATCH (n)" in source and "DETACH DELETE" in source


def _parse(cli, argv: list[str]):
    import argparse

    captured = {}

    def fake(args):
        captured["args"] = args
        return 0

    parser_argv = list(argv)
    original = {name: getattr(cli, name) for name in ("cmd_run", "cmd_stats", "cmd_reset")}
    for name in original:
        setattr(cli, name, fake)
    try:
        cli.main(parser_argv)
    except (SystemExit, argparse.ArgumentError):  # pragma: no cover
        raise
    finally:
        for name, func in original.items():
            setattr(cli, name, func)
    return captured["args"]


# ---------------------------------------------------------------------------
# BD → Industry TARGETS — 비금융 BD 가 고아로 남지 않는다 (PRD §21)
# ---------------------------------------------------------------------------


class TestBdTargetsCoverage:
    """멀티BM 시트만으로 TARGETS 를 만들면 금융 BD 8개만 붙고 비금융 12개가 고아가 된다."""

    @staticmethod
    def _targets_by_bd(batch: GraphBatch) -> dict[str, set[str]]:
        names = {
            node.natural_key: node.props["name"]
            for node in batch.nodes_by_label("BusinessDomain")
        }
        industries = {
            node.natural_key: node.props["name"] for node in batch.nodes_by_label("Industry")
        }
        out: dict[str, set[str]] = {name: set() for name in names.values()}
        for edge in batch.edges_of_type("TARGETS"):
            out[names[edge.start[1]]].add(industries[edge.end[1]])
        return out

    def test_TARGETS_가_0인_BD_가_3개_이하(self, bd_build):
        by_bd = self._targets_by_bd(bd_build.batch)
        zero = sorted(name for name, inds in by_bd.items() if not inds)
        assert len(zero) <= 3, f"TARGETS 0인 BD {len(zero)}개: {zero}"

    def test_비금융_BD_가_Industry_에_붙는다(self, bd_build):
        by_bd = self._targets_by_bd(bd_build.batch)
        assert "제조사" in by_bd["제조"]
        assert "프랜차이즈사" in by_bd["프랜차이즈"]
        assert "유통사" in by_bd["식자재유통 (푸드서비스)"]
        assert by_bd["물류"], "물류 BD 가 Industry 에 붙지 않았다"

    def test_산업이_아닌_범위값에는_엣지를_만들지_않는다(self, bd_build):
        """'광범위'·'전 산업' 은 특정 산업이 아니다 — 근거 없는 엣지를 만들면 안 된다."""
        from ingestion.pipeline.direct import industry_names_from_target_company

        for raw in ("광범위", "전 산업"):
            names, rejected = industry_names_from_target_company(raw)
            assert names == [] and rejected == [raw]
        assert self._targets_by_bd(bd_build.batch)["플랫폼"] == set()

    def test_괄호_한정어를_떼고_기존_Industry_로_합친다(self, bd_build):
        from ingestion.pipeline.direct import industry_names_from_target_company

        assert industry_names_from_target_company("(손해)보험사")[0] == ["보험사"]
        assert industry_names_from_target_company("가전(제조사)")[0] == ["제조사"]
        assert industry_names_from_target_company("보험사/GA")[0] == ["보험사", "GA"]


# ---------------------------------------------------------------------------
# ⑤ 문서 LLM 추출 — 대상 선별과 그래프 반영 (PRD 1A AC-1·2)
# ---------------------------------------------------------------------------


class TestDocumentExtractionPool:
    """미팅 발췌 2,770건이 예산을 다 쓰면 문서 추출은 한 건도 못 한다. 대상·순서를 고정한다."""

    @staticmethod
    def _record(source_id: str, n: int) -> dict:
        return {
            "evidence_id": f"ev_{source_id}_{n}",
            "source_id": source_id,
            "locator": f"p{n}",
            "excerpt": f"{source_id} 발췌 {n}",
            "excerpt_hash": f"h{source_id}{n}",
            "structured": None,
            "authored_at": None,
        }

    def test_대상_계열만_남기고_소스를_돌아가며_뽑는다(self):
        from ingestion.pipeline.runner import (
            DOC_EXTRACT_PER_SOURCE,
            _document_extraction_pool,
        )

        big = [self._record("src_doc_big", i) for i in range(DOC_EXTRACT_PER_SOURCE * 3)]
        small = [self._record("src_doc_small", i) for i in range(2)]
        skipped = [self._record("src_repo_tech", i) for i in range(50)]
        pool = _document_extraction_pool(
            {"(none)": big + small + skipped},
            {
                "src_doc_big": "proposal",
                "src_doc_small": "bd_openbook",
                "src_repo_tech": "repo_doc",
            },
        )
        sources = [record["source_id"] for record in pool]
        assert "src_repo_tech" not in sources, "대상 계열이 아닌 소스가 들어왔다"
        assert sources.count("src_doc_big") == DOC_EXTRACT_PER_SOURCE
        assert sources.count("src_doc_small") == 2
        # 작은 소스가 큰 소스 뒤로 밀리지 않는다 — 앞쪽에서 둘 다 나온다.
        assert set(sources[:4]) == {"src_doc_big", "src_doc_small"}

    def test_PII_미검증_소스는_보내지_않는다(self):
        from ingestion.pipeline.runner import DOC_EXTRACT_SKIP, _document_extraction_pool

        skipped = sorted(DOC_EXTRACT_SKIP)[0]
        pool = _document_extraction_pool(
            {"(none)": [self._record(skipped, 0)]}, {skipped: "proposal"}
        )
        assert pool == []

    def test_문서_추출물이_그래프에_반영된다(self, settings):
        """LLM 응답이 Observation·Claim 으로 실제로 들어오는지. 후보이므로 VERIFIED 가 되면 안 된다."""
        from ingestion.pipeline.direct import (
            LoadContext,
            add_evidence,
            build_feature_capability_index,
        )
        from ingestion.pipeline.model import GraphBatch
        from ingestion.pipeline.verdict import CriticalityEngine, VerdictEngine

        ctx = LoadContext(
            settings=settings,
            resolver=Resolver(settings),
            verdicts=VerdictEngine(settings),
            criticality=CriticalityEngine(settings),
            batch=GraphBatch(),
            feature_capability_index=build_feature_capability_index(settings),
        )
        ctx.source_type["src_doc_proposal_probe"] = "proposal"
        record = self._record("src_doc_proposal_probe", 1)
        evidence_ref = add_evidence(ctx, record)

        class FakeStage:
            def __init__(self):
                self.seen = []

            def extract_document_candidates(self, excerpts, *, purpose):
                self.seen.append((purpose, len(excerpts)))
                return [
                    {
                        "source_id": record["source_id"],
                        "locator": record["locator"],
                        "signal": "need",
                        "statement": "제안서가 카카오 채널 통제 부재를 문제로 적었다.",
                    }
                ]

        stage = FakeStage()
        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_records(
            ctx, stage, [record], {record["evidence_id"]: evidence_ref}, extractor="document"
        )

        assert stage.seen == [("document", 1)], stage.seen
        llm_obs = [
            node
            for node in ctx.batch.nodes_by_label("Observation")
            if node.props.get("extractor") == "llm"
        ]
        assert llm_obs, "문서 추출물이 Observation 으로 들어오지 않았다"
        statuses = {node.props["status"] for node in ctx.batch.nodes_by_label("Claim")}
        assert statuses and "VERIFIED" not in statuses, statuses


class TestDocumentNeedSecondChance:
    """문서·미팅 추출이 찾은 니즈가 사전에 안 걸렸을 때 어떻게 되는가.

    Pain 대장 경로는 사전에 안 걸리면 2차 매핑(taxonomy 후보 제안)을 받는데, 문서·미팅 경로는
    그 기회가 없어 니즈를 그대로 버렸다. 실측: 캐시에 남은 추출 응답 1,840건 중 need_raw 를
    담아 온 것이 625건인데 사전 적중이 0건이고, 그중 332종은 계정까지 함께 왔다.
    그래서 HAS_NEED 가 406개 계정 중 4개(Pain 대장 고객)에만 있었다.
    """

    NEED_ID = "need.channel_control_absent"
    ACCOUNT = "누리손해보험"

    @staticmethod
    def _record() -> dict:
        return {
            "evidence_id": "ev_doc_need_probe",
            "source_id": "src_doc_proposal_probe",
            "locator": "p7",
            "excerpt": "고객이 개인 카톡을 업무에서 분리하고 싶다고 했다.",
            "excerpt_hash": "hdocneed7",
            "structured": None,
            "authored_at": None,
        }

    def _context(self, settings):
        from ingestion.pipeline.direct import LoadContext, build_feature_capability_index
        from ingestion.pipeline.model import GraphBatch
        from ingestion.pipeline.verdict import CriticalityEngine, VerdictEngine

        ctx = LoadContext(
            settings=settings,
            resolver=Resolver(settings),
            verdicts=VerdictEngine(settings),
            criticality=CriticalityEngine(settings),
            batch=GraphBatch(),
            feature_capability_index=build_feature_capability_index(settings),
        )
        ctx.source_type["src_doc_proposal_probe"] = "proposal"
        return ctx

    def _stage(self, record, proposals):
        """추출은 사전에 없는 니즈 표현을 주고, 2차 매핑은 시킨 대로 답하는 대역."""

        class FakeStage:
            def __init__(self):
                self.mapping_calls = []

            def extract_document_candidates(self, excerpts, *, purpose):
                return [
                    {
                        "source_id": record["source_id"],
                        "locator": record["locator"],
                        "signal": "need",
                        "statement": "제안서가 개인 카톡 분리 요구를 적었다.",
                        "need_raw": "개인 카톡을 업무에서 분리",
                        "account": TestDocumentNeedSecondChance.ACCOUNT,
                    }
                ]

            def extract_meeting_candidates(self, excerpts):
                return []

            def propose_mappings(self, *, kind, excerpts, allowed_ids, catalog):
                self.mapping_calls.append((kind, [item["text"] for item in excerpts]))
                return dict(proposals)

        return FakeStage()

    def _run(self, settings, proposals):
        from ingestion.pipeline.direct import add_evidence

        ctx = self._context(settings)
        record = self._record()
        evidence_ref = add_evidence(ctx, record)
        stage = self._stage(record, proposals)
        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        refs = {record["evidence_id"]: evidence_ref}
        pipeline._extract_records(ctx, stage, [record], refs, extractor="document")
        pipeline._map_llm_needs(ctx, stage, refs)
        return ctx, stage

    def test_사전에_안_걸린_니즈가_2차_매핑_대상으로_모인다(self, settings):
        _, stage = self._run(settings, {})
        needs = [call for call in stage.mapping_calls if call[0] == "Need"]
        assert needs, "문서 추출이 찾은 니즈가 2차 매핑으로 넘어가지 않았다"
        assert "개인 카톡을 업무에서 분리" in needs[0][1], needs

    def test_2차_매핑이_붙인_니즈는_계정과_이어진다(self, settings):
        ctx, _ = self._run(settings, {"개인 카톡을 업무에서 분리": self.NEED_ID})

        canonical = [
            node
            for node in ctx.batch.nodes_by_label("Need")
            if node.props.get("need_id") == self.NEED_ID
        ]
        assert canonical, "2차 매핑이 성공했는데 canonical Need 가 없다"

        edges = ctx.batch.edges_of_type("HAS_NEED")
        assert edges, "계정과 니즈가 HAS_NEED 로 이어지지 않았다"
        assert all(edge.props.get("claim_ids") for edge in edges), "HAS_NEED 에 근거 Claim 이 없다"

    def test_해당없음이면_니즈를_만들지_않는다(self, settings):
        """사내 과제(「반복 매출 확보」 류)가 고객 니즈로 승격되면 그래프가 오염된다."""
        ctx, _ = self._run(settings, {})

        assert not ctx.batch.edges_of_type("HAS_NEED")
        assert not ctx.batch.nodes_by_label("Need"), "매핑되지 않은 니즈가 노드로 들어왔다"

    def test_실행_흐름에_배선돼_있다(self):
        """위 테스트들은 2차 매핑을 직접 부른다. 실제 실행이 부르는지는 따로 못박는다.

        문서·미팅 두 추출이 **끝난 뒤**에 와야 한다. 앞에 두면 미팅이 찾은 니즈를 놓치고,
        둘 사이에 두면 같은 표현을 두 번 물어 비용이 두 배가 된다.
        """
        import inspect

        source = inspect.getsource(IngestPipeline._run_llm)
        assert "_map_llm_needs" in source, "2차 매핑이 실행 흐름에 배선되지 않았다"

        order = [
            source.index('extractor="document"'),
            source.index('extractor="meeting"'),
            source.index("self._map_llm_needs("),
        ]
        assert order == sorted(order), "2차 매핑이 두 추출보다 앞에 있다"


class TestMeetingAccountBinding:
    """미팅 추출의 이름 대조는 인용이 아니라 발췌 원문과 한다.

    슬랙 미팅 기록은 회사 이름이 발췌 맨 위 제목 줄(`*[나래생명 - …]*`)에 있고 모델이
    고르는 인용은 본문 한 문장이라, 이름을 인용에 묶으면 구조적으로 다 죽는다
    (실측: 증분 0812 추출 38건 중 36건 탈락, 사유 전부 「account 가 인용에 없다」,
    36건 모두 account 는 발췌에 실재). 딜 금액 추출과 같은 처방이다 —
    대조를 없애지 않고 상대를 발췌로 바꾼다. 대조는 파이프라인 몫이다.
    """

    EXCERPT = (
        "[나래생명 - FCC 채팅상담 고객관리 미팅]\n"
        "FCC 채팅상담은 큰 이슈 없이 잘 사용 중이며, CTI 변경 건에 대한 지원을 당부받음"
    )

    def _record(self):
        return {
            "evidence_id": "ev_meeting_bind_probe",
            "source_id": "src_slack_probe",
            "locator": "slack:C0/1.0",
            "excerpt": self.EXCERPT,
            "excerpt_hash": "hmeetbind",
            "structured": None,
            "authored_at": None,
        }

    def _context(self, settings):
        from ingestion.pipeline.direct import LoadContext, build_feature_capability_index
        from ingestion.pipeline.verdict import CriticalityEngine, VerdictEngine

        ctx = LoadContext(
            settings=settings,
            resolver=Resolver(settings),
            verdicts=VerdictEngine(settings),
            criticality=CriticalityEngine(settings),
            batch=GraphBatch(),
            feature_capability_index=build_feature_capability_index(settings),
        )
        ctx.source_type["src_slack_probe"] = "slack_thread"
        return ctx

    def _run_items(self, settings, items):
        from ingestion.pipeline.direct import add_evidence

        ctx = self._context(settings)
        record = self._record()
        evidence_ref = add_evidence(ctx, record)

        class FakeStage:
            def extract_meeting_candidates(self, excerpts):
                return items

        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_records(
            ctx, FakeStage(), [record], {record["evidence_id"]: evidence_ref}, extractor="meeting"
        )
        return ctx

    def _item(self, **overrides):
        base = {
            "source_id": "src_slack_probe",
            "locator": "slack:C0/1.0",
            "evidence_quote": "FCC 채팅상담은 큰 이슈 없이 잘 사용 중이며",
            "signal": "need",
            "statement": "나래생명가 CTI 변경 지원을 요청했다.",
            "account": "나래생명",
        }
        base.update(overrides)
        return base

    def test_게이트는_미팅_account_를_인용에_묶지_않는다(self):
        """이름 대조가 인용에 묶여 있으면 제목 줄 이름이 구조적으로 전부 죽는다."""
        import inspect

        from ingestion.pipeline.llm_stage import LLMStage

        source = inspect.getsource(LLMStage.extract_meeting_candidates)
        assert 'quote_bound_exempt=("account",)' in source, (
            "미팅 추출이 account 를 인용 대조에서 면제하지 않았다"
        )

    def test_인용과_이름이_발췌에_실재하면_흡수된다(self, settings):
        ctx = self._run_items(settings, [self._item()])
        assert ctx.batch.nodes_by_label("Observation"), "정상 항목이 흡수되지 않았다"

    def test_지어낸_인용은_발췌_대조에서_죽는다(self, settings):
        """게이트는 항목만 보고 발췌를 못 본다. 인용이 지어낸 것이면 숫자·이름이
        그 인용 안에 있으니 게이트를 통과한다. 발췌 대조는 파이프라인 몫이다."""
        ctx = self._run_items(
            settings, [self._item(evidence_quote="이 문장은 발췌에 없는 지어낸 인용이다")]
        )
        assert not ctx.batch.nodes_by_label("Observation")
        assert ctx.counters["llm_meeting_quote_not_in_excerpt"] == 1

    def test_발췌에_없는_이름은_죽는다(self, settings):
        ctx = self._run_items(settings, [self._item(account="미르자동차")])
        assert not ctx.batch.nodes_by_label("Observation")
        assert ctx.counters["llm_meeting_account_not_in_excerpt"] == 1


# ---------------------------------------------------------------------------
# 금액 정규화 — 오판보다 미상이 낫다
# ---------------------------------------------------------------------------


class TestAmountNormalization:
    """`amount_raw` 를 원(KRW) 숫자로 바꾸는 규칙.

    기대값의 근거는 `config/criticality-rules.yaml` 의 CR-HIGH-VALUE-DEAL basis·caution 과
    `contracts/ontology.schema.json` 의 `Deal.amount_raw` description 이다. 파이프라인 출력에서
    복사해 오지 않는다. 차단 케이스가 이 표의 절반인 이유는, 금액을 잘못 읽으면 그래프의
    유일한 정량 축이 오염되고 CR-HIGH-VALUE-DEAL(3억 문턱)이 헛발화하기 때문이다.
    """

    @staticmethod
    def _krw(raw):
        from ingestion.pipeline.direct import parse_amount

        return parse_amount(raw)[1]

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # 변경 이력이 한 셀에 담긴 표기. 마지막(최신) 값을 쓴다.
            ("4억 -> 3.15억", 315_000_000),
            ("4억 → 3.15억", 315_000_000),
            # 원 접미사. 자유 서술에서 이 형태로 온다(슬랙 계약 보고 393,000,000원).
            ("393,000,000원", 393_000_000),
            ("2억원", 200_000_000),
            ("5억 원", 500_000_000),
            ("약 5억", 500_000_000),
            # 만·천만·백만 단위.
            ("5,000만원", 50_000_000),
            ("100만원", 1_000_000),
            ("5천만원", 50_000_000),
            ("3백만원", 3_000_000),
            # 기존 실측 11종은 결과가 바뀌지 않는다.
            ("5억", 500_000_000),
            ("0.6억", 60_000_000),
            ("1.9억", 190_000_000),
            ("2.7억", 270_000_000),
            ("2억", 200_000_000),
            ("2.85억", 285_000_000),
            ("4억", 400_000_000),
            ("62,000,000", 62_000_000),
            ("508,000,000", 508_000_000),
        ],
    )
    def test_읽는다(self, raw, expected):
        assert self._krw(raw) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "raw",
        [
            "-",
            "0",
            # 단위를 생략한 맨 숫자. config CR-HIGH-VALUE-DEAL caution 이 「정규화에 실패하면
            # 규칙을 발화시키지 말고 CR-AMOUNT-UNPARSEABLE 로 보낸다」로 정해 둔 값이다.
            "0.5",
            "0.8",
            "2025",  # 연도
            "12,000",  # 단위 없고 6자리 미만
            "0원",  # 원 단위인데 6자리 미만
            "8Core 64GB",
            "약 5억 원 내외로 추산된다는 앵커스 의견",  # 문장 통째
            "900억 투자 유치로 사세 확장",  # 딜 금액이 아닌 문장
        ],
    )
    def test_애매하면_읽지_않는다(self, raw):
        assert self._krw(raw) is None, f"{raw!r} 를 금액으로 읽었다"

    def test_원문은_그대로_보존된다(self):
        from ingestion.pipeline.direct import parse_amount

        raw, krw = parse_amount("4억 -> 3.15억")
        assert raw == "4억 -> 3.15억", "정규화하면서 원문을 고쳤다"
        assert krw == pytest.approx(315_000_000)


# ---------------------------------------------------------------------------
# 자유 서술에서 딜 금액 뽑기 — 귀속을 틀리면 정량 축이 오염된다
# ---------------------------------------------------------------------------


class TestDealAmountFromText:
    """영업 시트의 금액 칸이 비어 있어도 슬랙·문서 본문에는 금액이 적혀 있다.

    실측(2026-08-12): 사람별 시트 118행의 `예상 매출 금액` 칸은 원본에서 전부 비어 있고
    (openpyxl 로 직접 확인), 그래서 딜 107건 중 94건이 금액을 못 가졌다. 반면 금액 표현과
    딜 계정이 같이 등장하는 발췌가 105건 있다.

    다만 **같은 발췌에 있다는 것만으로는 그 딜의 금액이 아니다.** 실제로 섞여 있던 것:
    마인드웨어웍스 900억 투자 유치(수협은행 발췌) · kt ds 100억(바람생명 발췌) ·
    미르자동차 콜센터 750억(고객사의 기존 사업 규모) · 이랜드월드 5천만원(한울텔레콤 발췌).
    그래서 이 테스트의 절반이 「붙이지 않는다」 쪽이다.
    """

    ACCOUNT = "수협은행"

    @staticmethod
    def _record(excerpt: str) -> dict:
        return {
            "evidence_id": "ev_slack_amount_probe",
            "source_id": "src_slack_all_sales_share",
            "locator": "slack:CPROBE/1.0",
            "excerpt": excerpt,
            "excerpt_hash": "hslackamount1",
            "structured": None,
            "authored_at": "2026-01-05",
        }

    def _context(self, settings, *, deal_amount=None, accounts=(ACCOUNT,)):
        from ingestion.pipeline.direct import LoadContext, account_node, build_feature_capability_index
        from ingestion.pipeline.direct import deal_node
        from ingestion.pipeline.model import GraphBatch
        from ingestion.pipeline.verdict import CriticalityEngine, VerdictEngine

        ctx = LoadContext(
            settings=settings,
            resolver=Resolver(settings),
            verdicts=VerdictEngine(settings),
            criticality=CriticalityEngine(settings),
            batch=GraphBatch(),
            feature_capability_index=build_feature_capability_index(settings),
        )
        ctx.source_type["src_slack_all_sales_share"] = "slack_thread"
        ctx.source_type["src_sales_activity_log"] = "sales_activity_log"
        for name in accounts:
            ref = account_node(ctx, name, source_id="src_sales_activity_log")
            canonical = ctx.batch.find_node(*ref).props["canonical_name"]
            deal_node(
                ctx,
                account=canonical,
                scope="기본",
                source_id="src_sales_activity_log",
                stage_system="activity_person_sheet",
                amount_raw=deal_amount,
                amount_krw=None if deal_amount is None else 999_000_000.0,
            )
        return ctx

    @staticmethod
    def _stage(record, items):
        class FakeStage:
            def __init__(self):
                self.seen = []

            def extract_deal_amounts(self, excerpts):
                self.seen.append([item["locator"] for item in excerpts])
                return [
                    {"source_id": record["source_id"], "locator": record["locator"], **item}
                    for item in items
                ]

        return FakeStage()

    def _run(self, settings, excerpt, items, *, deal_amount=None):
        from ingestion.pipeline.direct import add_evidence

        ctx = self._context(settings, deal_amount=deal_amount)
        record = self._record(excerpt)
        refs = {record["evidence_id"]: add_evidence(ctx, record)}
        stage = self._stage(record, items)
        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_deal_amounts(ctx, stage, [record], refs)
        return ctx

    @staticmethod
    def _deal(ctx, canonical="수협은행"):
        return next(
            node
            for node in ctx.batch.nodes_by_label("Deal")
            if node.props["account_canonical"] == canonical
        )

    # -- 붙이는 쪽 ---------------------------------------------------------
    def test_본문에_적힌_계약금액이_딜에_붙는다(self, settings):
        ctx = self._run(
            settings,
            "수협은행 고도화 사업 계약 금액은 393,000,000원으로 확정됐다.",
            [
                {
                    "account": "수협은행",
                    "amount_raw": "393,000,000원",
                    "amount_kind": "contract",
                    "statement": "수협은행 고도화 사업 계약 금액이 393,000,000원으로 확정됐다.",
                    "evidence_quote": "계약 금액은 393,000,000원으로 확정됐다.",
                }
            ],
        )
        deal = self._deal(ctx)
        assert deal.props["amount_krw"] == pytest.approx(393_000_000)
        assert deal.props["amount_raw"] == "393,000,000원", "원문을 보존하지 않았다"

    def test_붙인_금액에는_근거_주장이_따라온다(self, settings):
        ctx = self._run(
            settings,
            "수협은행 사업 예산은 약 10억 규모로 파악된다.",
            [
                {
                    "account": "수협은행",
                    "amount_raw": "10억",
                    "amount_kind": "budget",
                    "statement": "수협은행 사업 예산이 약 10억 규모로 파악된다.",
                    "evidence_quote": "사업 예산은 약 10억 규모로 파악된다.",
                }
            ],
        )
        claims = [
            node
            for node in ctx.batch.nodes_by_label("Claim")
            if node.props.get("claim_kind") == "deal_fact"
        ]
        assert claims, "금액을 붙였는데 근거 Claim 이 없다"
        assert all(node.props.get("evidence_ids") for node in claims), "Claim 에 근거가 없다"
        # 슬랙 딜 사실은 활동일지가 정본이므로 확정으로 올리지 않는다(config 판정 규칙).
        assert all(node.props.get("status") != "VERIFIED" for node in claims), (
            "슬랙에서 온 금액 주장이 VERIFIED 로 올라갔다"
        )

    # -- 안 붙이는 쪽 -------------------------------------------------------
    def test_시트_금액을_덮어쓰지_않는다(self, settings):
        """활동일지가 딜 금액의 정본이다. 슬랙 값이 조용히 갈아치우면 안 된다."""
        ctx = self._run(
            settings,
            "수협은행 사업 예산은 10억 규모로 파악된다.",
            [
                {
                    "account": "수협은행",
                    "amount_raw": "10억",
                    "amount_kind": "budget",
                    "statement": "수협은행 사업 예산이 10억 규모로 파악된다.",
                    "evidence_quote": "사업 예산은 10억 규모로 파악된다.",
                }
            ],
            deal_amount="2억",
        )
        deal = self._deal(ctx)
        assert deal.props["amount_krw"] == pytest.approx(999_000_000), "시트 금액이 덮어써졌다"
        assert deal.props["amount_raw"] == "2억"

    def test_정규화되지_않는_금액은_붙이지_않는다(self, settings):
        ctx = self._run(
            settings,
            "수협은행 사업은 상당한 규모로 예상된다.",
            [
                {
                    "account": "수협은행",
                    "amount_raw": "상당한 규모",
                    "amount_kind": "budget",
                    "statement": "수협은행 사업이 상당한 규모로 예상된다.",
                    "evidence_quote": "사업은 상당한 규모로 예상된다.",
                }
            ],
        )
        assert self._deal(ctx).props.get("amount_krw") is None

    def test_딜이_없는_계정에는_딜을_만들지_않는다(self, settings):
        """슬랙에만 나오는 이름에 딜을 새로 만들면 파이프라인 건수가 조용히 늘어난다."""
        ctx = self._run(
            settings,
            "마인드웨어웍스가 900억 투자를 유치했다.",
            [
                {
                    "account": "마인드웨어웍스",
                    "amount_raw": "900억",
                    "amount_kind": "budget",
                    "statement": "마인드웨어웍스가 900억 투자를 유치했다.",
                    "evidence_quote": "900억 투자를 유치했다.",
                }
            ],
        )
        assert len(ctx.batch.nodes_by_label("Deal")) == 1, "없던 딜이 생겼다"
        assert self._deal(ctx).props.get("amount_krw") is None

    def test_딜이_여러_개인_계정은_건드리지_않는다(self, settings):
        """'다라카드 자동차'·'다라카드 채권'처럼 한 계정에 딜이 여럿이면 어디에 붙일지 모른다."""
        from ingestion.pipeline.direct import add_evidence, deal_node

        ctx = self._context(settings, accounts=("다라카드",))
        deal_node(
            ctx,
            account="다라카드",
            scope="채권",
            source_id="src_sales_activity_log",
            stage_system="activity_person_sheet",
        )
        record = self._record("다라카드 사업 금액은 5억이다.")
        refs = {record["evidence_id"]: add_evidence(ctx, record)}
        stage = self._stage(
            record,
            [
                {
                    "account": "다라카드",
                    "amount_raw": "5억",
                    "amount_kind": "proposal",
                    "statement": "다라카드 사업 금액이 5억이다.",
                    "evidence_quote": "사업 금액은 5억이다.",
                }
            ],
        )
        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_deal_amounts(ctx, stage, [record], refs)

        assert all(
            node.props.get("amount_krw") is None for node in ctx.batch.nodes_by_label("Deal")
        ), "딜이 여러 개인데 아무 곳에나 금액을 붙였다"

    def test_발췌에_없는_숫자는_게이트가_버린다(self):
        """FakeStage 는 게이트를 지나가지 않는다. 게이트 자체를 따로 못박는다."""
        from llm.extraction import t2_gate

        report = t2_gate(
            [
                {
                    "evidence_quote": "수협은행 계약 금액은 393,000,000원으로 확정됐다.",
                    "account": "수협은행",
                    "amount_raw": "393,000,000원",
                },
                {
                    "evidence_quote": "수협은행 계약 금액은 393,000,000원으로 확정됐다.",
                    "account": "수협은행",
                    "amount_raw": "500,000,000원",  # 발췌에 없는 숫자
                },
            ],
            extra_quote_bound_fields=("amount_raw", "account"),
        )
        assert len(report.kept) == 1, report.dropped
        assert report.kept[0]["amount_raw"] == "393,000,000원"

    # -- 후보 발췌 고르기 ---------------------------------------------------
    def test_금액과_딜_계정이_함께_있는_발췌만_고른다(self, settings):
        from ingestion.pipeline.runner import deal_amount_pool

        ctx = self._context(settings)
        keep = self._record("수협은행 계약 금액은 393,000,000원이다.")
        no_amount = dict(keep, evidence_id="ev_b", excerpt="수협은행 담당자와 인사를 나눴다.")
        no_account = dict(keep, evidence_id="ev_c", excerpt="어떤 사업의 금액은 5억이다.")

        chosen = deal_amount_pool(ctx, [keep, no_amount, no_account])

        assert [r["evidence_id"] for r in chosen] == ["ev_slack_amount_probe"]

    def test_실행_흐름에_배선돼_있다(self):
        import inspect

        source = inspect.getsource(IngestPipeline._run_llm)
        assert "_extract_deal_amounts" in source, "금액 추출이 실행 흐름에 배선되지 않았다"


class TestDealAmountGuards:
    """금액 항목을 발췌에 묶는 장치. t2 게이트가 못 보는 자리를 여기서 막는다.

    게이트는 항목만 보고 발췌 원문을 못 본다. 그래서 인용 자체가 지어낸 것이면 게이트를
    통과한다(숫자·이름이 그 인용 안에 있으니까). 발췌 원문과 대조하는 것은 파이프라인 몫이다.

    회사 이름을 **인용**에 묶지 않는 이유는 실측 때문이다. 모델이 고른 인용은 금액이 있는 한
    문장이고(「계약금액 : 299,000,000원(VAT별도)」), 회사 이름은 발췌 맨 위 제목 줄에 있다.
    이름을 인용에 묶으면 실제로 10건 중 10건이 버려졌다(2026-08-12, 3콜 $0.20). 그래서
    금액은 인용에, 이름과 인용은 발췌에 묶는다.
    """

    @staticmethod
    def _run(settings, excerpt, item):
        probe = TestDealAmountFromText()
        from ingestion.pipeline.direct import add_evidence

        ctx = probe._context(settings, accounts=("수협은행", "바람화재"))
        record = probe._record(excerpt)
        refs = {record["evidence_id"]: add_evidence(ctx, record)}
        stage = probe._stage(record, [item])
        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_deal_amounts(ctx, stage, [record], refs)
        return ctx

    @staticmethod
    def _amounts(ctx):
        return {
            node.props["account_canonical"]: node.props.get("amount_krw")
            for node in ctx.batch.nodes_by_label("Deal")
        }

    def test_발췌에_없는_회사명에는_붙이지_않는다(self, settings):
        """발췌는 수협은행 이야기인데 금액을 바람화재에 붙이면 파이프라인 총액이 거짓이 된다."""
        ctx = self._run(
            settings,
            "수협은행 고도화 사업 계약금액 : 299,000,000원(VAT별도)",
            {
                "account": "바람화재",
                "amount_raw": "299,000,000원",
                "amount_kind": "contract",
                "evidence_quote": "계약금액 : 299,000,000원(VAT별도)",
            },
        )
        assert self._amounts(ctx) == {"수협은행": None, "바람화재": None}

    def test_발췌에_없는_인용은_붙이지_않는다(self, settings):
        ctx = self._run(
            settings,
            "수협은행 담당자와 사업 일정을 협의했다.",
            {
                "account": "수협은행",
                "amount_raw": "299,000,000원",
                "amount_kind": "contract",
                "evidence_quote": "계약금액 : 299,000,000원(VAT별도)",  # 발췌에 없는 문장
            },
        )
        assert self._amounts(ctx)["수협은행"] is None

    def test_인용과_이름이_발췌에_있으면_붙는다(self, settings):
        ctx = self._run(
            settings,
            "*[수협은행 - 고도화 사업 계약 체결]*\n• 계약금액 : 299,000,000원(VAT별도)",
            {
                "account": "수협은행",
                "amount_raw": "299,000,000원",
                "amount_kind": "contract",
                "evidence_quote": "계약금액 : 299,000,000원(VAT별도)",
            },
        )
        assert self._amounts(ctx)["수협은행"] == pytest.approx(299_000_000)


class TestHedgedAmount:
    """대화에서 오는 금액은 「1억 내외」처럼 어림수다. 원문을 남기고 값은 읽는다.

    범위 표기는 읽지 않는다. 2.5억인지 3억인지 고를 근거가 없고, 고르면 그 숫자는 어느
    자료에도 없는 값이 된다. 실측으로 이 네 형태가 실제로 왔다(2026-08-12 3콜):
    '1억 내외' · '5천만원 내외' · '9천만원 정도' · '2.5~3억 원 내외'.
    """

    @staticmethod
    def _krw(raw):
        from ingestion.pipeline.direct import parse_amount

        return parse_amount(raw)[1]

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1억 내외", 100_000_000),
            ("5천만원 내외", 50_000_000),
            ("9천만원 정도", 90_000_000),
            ("약 3억 규모", 300_000_000),
            ("5천만원(부가세 별도)", 50_000_000),
        ],
    )
    def test_어림수는_읽는다(self, raw, expected):
        assert self._krw(raw) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "raw",
        [
            "2.5~3억 원 내외",
            "2.5-3억",
            "1억~2억",
            "5천만원 이하",  # 상한만 있다. 금액이 아니라 조건이다
            "3억 이상",
        ],
    )
    def test_범위와_조건은_읽지_않는다(self, raw):
        assert self._krw(raw) is None, f"{raw!r} 를 하나의 금액으로 읽었다"

    def test_이름을_면제해도_숫자_대조는_남는다(self):
        """면제는 「인용 대신 발췌와 대조한다」는 뜻이다. 대조를 없애는 것이 아니다."""
        from llm.extraction import t2_gate

        quote = "계약금액 : 299,000,000원(VAT별도)"
        report = t2_gate(
            [
                {
                    "evidence_quote": quote,
                    "account": "마루캐피탈",  # 인용에는 없다. 면제 대상이라 통과해야 한다
                    "amount_raw": "299,000,000원",
                },
                {
                    "evidence_quote": quote,
                    "account": "마루캐피탈",
                    "amount_raw": "500,000,000원",  # 지어낸 숫자는 여전히 버려져야 한다
                },
            ],
            lexicon=["마루캐피탈"],
            extra_quote_bound_fields=("amount_raw",),
            quote_bound_exempt=("account",),
        )
        assert [item["amount_raw"] for item in report.kept] == ["299,000,000원"], report.dropped


class TestDealAmountPrecedence:
    """한 딜에 금액 후보가 여럿 오면 무엇을 딜 속성으로 쓰나.

    실측(2026-08-12 · 발췌 30건): 마루캐피탈 한 곳에 「라이선스 5천만원 내외」(견적)와
    「계약금액 : 299,000,000원(VAT별도)」(체결)이 함께 왔다. 먼저 온 것이 이기게 두면
    체결 금액이 견적에 밀린다. 실제로 그렇게 돌아서 2.99억이 5천만원에 가려졌다.

    그래서 순서가 아니라 **종류와 시점**으로 고른다: 계약 > 제안·견적 > 예산, 같은 종류면 최신.
    시트 금액은 어느 후보보다 앞선다(활동일지가 딜 금액의 정본이다).
    """

    @staticmethod
    def _run(settings, records_and_items, *, deal_amount=None):
        from ingestion.pipeline.direct import add_evidence

        probe = TestDealAmountFromText()
        ctx = probe._context(settings, deal_amount=deal_amount)
        refs = {}
        records = []
        items = []
        for idx, (excerpt, authored_at, item) in enumerate(records_and_items):
            record = dict(
                probe._record(excerpt),
                evidence_id=f"ev_amount_prec_{idx}",
                locator=f"slack:CPREC/{idx}.0",
                excerpt_hash=f"hprec{idx}",
                authored_at=authored_at,
            )
            refs[record["evidence_id"]] = add_evidence(ctx, record)
            records.append(record)
            items.append(
                {"source_id": record["source_id"], "locator": record["locator"], **item}
            )

        class FakeStage:
            def extract_deal_amounts(self, excerpts):
                return items

        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_deal_amounts(ctx, FakeStage(), records, refs)
        return ctx

    @staticmethod
    def _item(account, raw, kind, quote):
        return {
            "account": account,
            "amount_raw": raw,
            "amount_kind": kind,
            "evidence_quote": quote,
        }

    def test_계약금액이_견적보다_우선한다(self, settings):
        ctx = self._run(
            settings,
            [
                (
                    "수협은행 라이선스 5천만원 내외로 안내",
                    "2026-01-05",
                    self._item("수협은행", "5천만원 내외", "proposal", "라이선스 5천만원 내외로 안내"),
                ),
                (
                    "수협은행 계약금액 : 299,000,000원(VAT별도)",
                    "2026-02-10",
                    self._item("수협은행", "299,000,000원", "contract", "계약금액 : 299,000,000원(VAT별도)"),
                ),
            ],
        )
        deal = TestDealAmountFromText._deal(ctx)
        assert deal.props["amount_krw"] == pytest.approx(299_000_000)
        assert deal.props["amount_raw"] == "299,000,000원"

    def test_순서가_뒤바뀌어도_같은_결과다(self, settings):
        """계약이 먼저 와도 결과가 같아야 한다. 순서 의존이면 재실행마다 값이 바뀐다."""
        ctx = self._run(
            settings,
            [
                (
                    "수협은행 계약금액 : 299,000,000원(VAT별도)",
                    "2026-02-10",
                    self._item("수협은행", "299,000,000원", "contract", "계약금액 : 299,000,000원(VAT별도)"),
                ),
                (
                    "수협은행 라이선스 5천만원 내외로 안내",
                    "2026-01-05",
                    self._item("수협은행", "5천만원 내외", "proposal", "라이선스 5천만원 내외로 안내"),
                ),
            ],
        )
        assert TestDealAmountFromText._deal(ctx).props["amount_krw"] == pytest.approx(299_000_000)

    def test_같은_종류면_최신_기록이_이긴다(self, settings):
        ctx = self._run(
            settings,
            [
                (
                    "수협은행 계약금액 : 250,000,000원",
                    "2026-01-05",
                    self._item("수협은행", "250,000,000원", "contract", "계약금액 : 250,000,000원"),
                ),
                (
                    "수협은행 변경 계약금액 : 299,000,000원",
                    "2026-03-20",
                    self._item("수협은행", "299,000,000원", "contract", "변경 계약금액 : 299,000,000원"),
                ),
            ],
        )
        assert TestDealAmountFromText._deal(ctx).props["amount_krw"] == pytest.approx(299_000_000)

    def test_밀린_후보도_근거로는_남는다(self, settings):
        """딜 속성에서 밀렸다고 사라지면 어긋남을 나중에 물을 수 없다."""
        ctx = self._run(
            settings,
            [
                (
                    "수협은행 라이선스 5천만원 내외로 안내",
                    "2026-01-05",
                    self._item("수협은행", "5천만원 내외", "proposal", "라이선스 5천만원 내외로 안내"),
                ),
                (
                    "수협은행 계약금액 : 299,000,000원(VAT별도)",
                    "2026-02-10",
                    self._item("수협은행", "299,000,000원", "contract", "계약금액 : 299,000,000원(VAT별도)"),
                ),
            ],
        )
        statements = [
            node.props["statement"]
            for node in ctx.batch.nodes_by_label("Claim")
            if node.props.get("claim_kind") == "deal_fact"
        ]
        assert any("5천만원 내외" in s for s in statements), statements
        assert any("299,000,000원" in s for s in statements), statements


class TestAmountBoundIsNotValue:
    """「5천만원 이하」는 금액이 아니라 조건이다.

    실측(2026-08-12): 바다은행 발췌의 「부서장 품의는 5천만원(부가세 별도) 이하이며」에서
    모델이 금액 5천만원을 뽑았다. 결재 한도이지 사업 금액이 아니다. 모델이 상한 표현을
    떼고 숫자만 주면 정규화 규칙으로는 걸러지지 않으므로 **인용 문장**을 보고 판정한다.

    다른 숫자에 붙은 상한은 그 숫자의 조건이다: 「299,000,000원, 선금 30% 이상」의 '이상'은
    선금 비율에 붙은 것이라 계약금액을 무르게 하지 않는다.
    """

    @staticmethod
    def _bound(quote, amount_raw):
        from ingestion.pipeline.runner import amount_is_bound

        return amount_is_bound(quote, amount_raw)

    @pytest.mark.parametrize(
        "quote, raw",
        [
            ("부서장 품의는 5천만원(부가세 별도) 이하이며", "5천만원"),
            ("사업 예산은 3억 이상으로 잡혀 있다", "3억"),
            ("라이선스는 1억 미만 규모다", "1억"),
            ("연간 한도 5천만원", "5천만원"),
        ],
    )
    def test_조건이면_금액으로_안_본다(self, quote, raw):
        assert self._bound(quote, raw) is True

    @pytest.mark.parametrize(
        "quote, raw",
        [
            ("계약금액 : 299,000,000원(VAT별도)", "299,000,000원"),
            ("계약금액 299,000,000원, 선금 30% 이상 지급", "299,000,000원"),
            ("채널 도입 비용으로 9천만원 정도를 안내했다", "9천만원 정도"),
        ],
    )
    def test_금액이면_통과한다(self, quote, raw):
        assert self._bound(quote, raw) is False

    def test_파이프라인이_조건을_붙이지_않는다(self, settings):
        ctx = TestDealAmountGuards._run(
            settings,
            "수협은행 신규 채널 증설 검토 중이며 부서장 품의는 5천만원(부가세 별도) 이하이며",
            {
                "account": "수협은행",
                "amount_raw": "5천만원",
                "amount_kind": "budget",
                "evidence_quote": "부서장 품의는 5천만원(부가세 별도) 이하이며",
            },
        )
        assert TestDealAmountGuards._amounts(ctx)["수협은행"] is None


class TestAmountPairStaysCoherent:
    """`amount_krw` 와 `amount_raw` 는 같은 근거에서 와야 한다.

    실측(2026-08-12): 마바손해보험 딜은 일일보고에 예상매출이 `'0'` 으로 적혀 있어
    `amount_raw='0'` · `amount_krw=None` 상태였다. 여기에 슬랙 계약금액 393,000,000원을
    붙이자 `amount_krw=393,000,000` · `amount_raw='0'` 이 됐다. 계약서가 「amount_krw 는
    amount_raw 없이 단독 인용 금지」라고 정해 뒀는데, 이러면 짝이 어긋나 인용할 수 없다.

    `'0'`·`'-'` 는 금액이 아니라 **미기재 표시**다. 값이 아니므로 갈아치워도 잃는 것이 없다.
    반대로 시트에 실제 금액이 있으면(정규화된 `amount_krw` 가 있으면) 손대지 않는다.
    """

    @staticmethod
    def _run(settings, deal_amount):
        from ingestion.pipeline.direct import add_evidence

        probe = TestDealAmountFromText()
        ctx = probe._context(settings, deal_amount=deal_amount)
        if deal_amount in {"-", "0"}:  # 미기재 표시는 정규화값이 없다
            deal = probe._deal(ctx)
            deal.props["amount_krw"] = None
        record = probe._record("수협은행 계약금액 : 393,000,000원(VAT별도)")
        refs = {record["evidence_id"]: add_evidence(ctx, record)}
        stage = probe._stage(
            record,
            [
                {
                    "account": "수협은행",
                    "amount_raw": "393,000,000원",
                    "amount_kind": "contract",
                    "evidence_quote": "계약금액 : 393,000,000원(VAT별도)",
                }
            ],
        )
        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_deal_amounts(ctx, stage, [record], refs)
        return probe._deal(ctx)

    def test_미기재_표시는_갈아치운다(self, settings):
        deal = self._run(settings, "0")
        assert deal.props["amount_krw"] == pytest.approx(393_000_000)
        assert deal.props["amount_raw"] == "393,000,000원", "짝이 어긋난 채로 남았다"

    def test_시트에_실제_금액이_있으면_손대지_않는다(self, settings):
        deal = self._run(settings, "2억")
        assert deal.props["amount_krw"] == pytest.approx(999_000_000)
        assert deal.props["amount_raw"] == "2억"


class TestHumanExcludedAmount:
    """사람이 「우리 딜보다 범위가 넓다」고 판정한 금액은 딜 금액 칸에 넣지 않는다.

    2026-08-12 판정: 수협은행 「예산은 콜봇/챗봇/채팅 25억 정도」와 미르캐피탈 「콜봇 사업
    금액과 취합해보니 대략 9억」. 둘 다 고객 프로그램 전체나 다른 사업과의 합산이고 우리
    몫이 얼마인지는 원문에 없다. `Deal.amount_krw` 에는 범위를 적을 칸이 없어서(계약 무수정)
    넣으면 나중에 총액을 세는 사람이 우리 딜 규모로 읽는다.

    **근거 주장은 남긴다.** 지우면 그 금액이 자료에 있었다는 사실 자체가 사라진다.
    """

    LOCATOR = "slack:CEXCL/1.0"

    def _run(self, settings, *, exclude):
        from ingestion.pipeline.direct import add_evidence

        probe = TestDealAmountFromText()
        ctx = probe._context(settings)
        record = dict(
            probe._record("수협은행 예산은 콜봇/챗봇/채팅 25억 정도로 잡혀 있다고 하며"),
            locator=self.LOCATOR,
        )
        refs = {record["evidence_id"]: add_evidence(ctx, record)}
        stage = probe._stage(
            record,
            [
                {
                    "account": "수협은행",
                    "amount_raw": "25억",
                    "amount_kind": "budget",
                    "evidence_quote": "예산은 콜봇/챗봇/채팅 25억 정도로 잡혀 있다고 하며",
                }
            ],
        )
        pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)
        pipeline._extract_deal_amounts(
            ctx, stage, [record], refs, exclude_locators=exclude
        )
        return ctx

    def test_뺀_후보는_딜_금액이_되지_않는다(self, settings):
        ctx = self._run(settings, exclude=(self.LOCATOR,))
        assert TestDealAmountFromText._deal(ctx).props.get("amount_krw") is None

    def test_빼도_근거_주장은_남는다(self, settings):
        ctx = self._run(settings, exclude=(self.LOCATOR,))
        statements = [
            node.props["statement"]
            for node in ctx.batch.nodes_by_label("Claim")
            if node.props.get("claim_kind") == "deal_fact"
        ]
        assert any("25억" in s for s in statements), statements

    def test_안_빼면_붙는다(self, settings):
        """차단 케이스만 보면 규칙이 항상 막고 있어도 모른다."""
        ctx = self._run(settings, exclude=())
        assert TestDealAmountFromText._deal(ctx).props["amount_krw"] == pytest.approx(
            2_500_000_000
        )
