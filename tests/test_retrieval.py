"""A5 Retrieval 3종 + Router 검증.

기대값의 근거는 세 곳뿐이다.
  · `eval/golden.yaml` 의 `expected_route` — 구현보다 먼저 쓰인 라우팅 정본
  · REVISED-MASTER-PLAN §6 A5 · §8 · DECISIONS A-1 의 문장
  · `contracts/answer.schema.json` 의 상한·enum

시스템 출력에서 복사해 온 기대값은 없다. 그래서 이 파일에는 "실행해 보니 이 값이 나왔다"
류의 단언이 없고, 대신 **차단 케이스**(나오면 안 되는 것)와 **양방향 증명**(같은 엔진이
세 판정을 다 낼 수 있는가)이 중심이다. 통과 케이스만 보면 규칙이 죽어 있어도 초록불이다.

DB 를 건드리는 테스트는 `pytest::retrieval::` 네임스페이스 안에서만 노드를 만들고 지운다.
데모 그래프는 읽기만 한다.
"""

from __future__ import annotations

import pathlib
import sys

import os

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.golden_loader import contains, load_golden  # noqa: E402
from graph.connection import ReadOnlyGraph, read_only_graph, writable_graph  # noqa: E402

from retrieval import (  # noqa: E402
    AccessPolicy,
    CandidatePool,
    GapEngine,
    GapVerdict,
    RetrievalService,
    Router,
    verify_citations,
)
from retrieval import store  # noqa: E402
from retrieval.templates import CYPHER_TEMPLATES  # noqa: E402
from retrieval.text import content_terms, derive_gap_subject  # noqa: E402
from retrieval.types import EvidenceRef  # noqa: E402

TEST_PREFIX = "pytest::retrieval::"

# REVISED §5 A5 가 요구하는 최소 템플릿 5종. 이름을 여기 못 박아 둔다.
REQUIRED_TEMPLATES = {
    "common_needs_across_domains",
    "accounts_with_need",
    "industries_using_capability",
    "deals_by_domain",
    "feature_version_history",
}


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden():
    return load_golden()


@pytest.fixture(scope="module")
def router() -> Router:
    return Router()


@pytest.fixture(scope="module")
def graph() -> ReadOnlyGraph:
    g = read_only_graph()
    g.verify_connectivity()
    yield g
    g.close()


@pytest.fixture(scope="module")
def corpus():
    """실코퍼스(적재된 월드모델)를 전제하는 테스트의 게이트.

    빈 그래프(CI)나 다른 도메인이 적재된 그래프에서는 기대값이 성립하지 않으므로,
    데모 코퍼스를 적재한 환경에서 WM_CORPUS_TESTS=1 로 명시했을 때만 돌린다.
    """
    if os.environ.get("WM_CORPUS_TESTS") != "1":
        pytest.skip("WM_CORPUS_TESTS=1 이 아니면 코퍼스 의존 테스트를 건너뛴다")


@pytest.fixture(scope="module")
def service(graph: ReadOnlyGraph) -> RetrievalService:
    return RetrievalService(graph)


@pytest.fixture(scope="module")
def orphan_evidence():
    """그래프에 구조화되지 않은 Evidence 를 하나 심는다.

    Observation·Claim 어디에도 연결하지 않는다. 이것이 "추출이 놓친 1회 등장 정보"의
    최소 재현이다. fulltext 로만 되찾을 수 있어야 한다(REVISED §4 ⑦).
    """
    token = "짜맞춤파랑고래공정"  # 실제 자료에 없는 조어. 오탐이면 이 테스트가 먼저 깨진다.
    key = f"{TEST_PREFIX}orphan-evidence"
    ev_id = f"{TEST_PREFIX}ev-orphan"
    src_key = f"{TEST_PREFIX}orphan-source"
    src_id = f"{TEST_PREFIX}src-orphan"

    g = writable_graph()
    g.verify_connectivity()
    with g.write_session() as s:
        s.run(
            "MERGE (src:Source {natural_key: $src_key}) "
            "SET src.source_id = $src_id, src.source_type = 'internal_memo', "
            "    src.sensitivity = 'internal', src.visibility = 'internal', "
            "    src.source_status = 'active', src.doc_status = 'approved' "
            "MERGE (e:Evidence {natural_key: $key}) "
            "SET e.evidence_id = $ev_id, e.source_id = $src_id, "
            "    e.locator = 'pytest::orphan#1', e.snippet = $snippet "
            "MERGE (e)-[:FROM_SOURCE]->(src)",
            src_key=src_key,
            src_id=src_id,
            key=key,
            ev_id=ev_id,
            snippet=f"{token} 도입 시 전용 회선 이중화가 선행되어야 한다는 요구가 한 번 나왔다.",
        )
    yield {"token": token, "evidence_id": ev_id, "source_id": src_id}
    with g.write_session() as s:
        s.run(
            "MATCH (n) WHERE n.natural_key STARTS WITH $p DETACH DELETE n",
            p=TEST_PREFIX,
        )
    g.close()


# ---------------------------------------------------------------------------
# 1. Router — golden 20문항 라우팅
# ---------------------------------------------------------------------------


def test_router_reproduces_every_golden_expected_route(router, golden):
    """골든의 expected_route 는 구현보다 먼저 쓰인 정본이다. 20문항 전부 맞아야 한다."""
    wrong = []
    for q in golden.questions:
        decision = router.route(q["question"])
        if decision.retriever != q["expected_route"]:
            wrong.append(
                f"{q['id']}: 기대 {q['expected_route']} / 실제 {decision.retriever} "
                f"(단계 {decision.stage} · 규칙 {decision.matched_rule})"
            )
    assert not wrong, "라우팅 불일치\n" + "\n".join(wrong)


def test_router_covers_all_three_retrievers(router, golden):
    """전부 Q-E 로 보내도 다수결로 초록불이 되는 것을 막는다."""
    routed = {router.route(q["question"]).retriever for q in golden.questions}
    assert routed == {"Q-E", "Q-M", "Q-S"}


def test_router_leaves_a_reason_for_every_decision(router, golden):
    """라우팅 결과에 어느 단계에서 정해졌는지가 남아야 한다(A5 요구)."""
    for q in golden.questions:
        d = router.route(q["question"])
        assert d.stage in {"template", "signal", "llm_fallback", "default"}, q["id"]
        assert d.matched_rule, f"{q['id']} 에 규칙 이름이 비어 있다"
        assert d.retriever in {"Q-E", "Q-M", "Q-S"}


def test_router_falls_back_to_llm_when_no_rule_matches():
    """3단: 템플릿 → 신호 규칙 → S tier enum 폴백."""
    calls: list[str] = []

    def classifier(question: str) -> str:
        calls.append(question)
        return "Q-S"

    d = Router(classifier=classifier).route("음.")
    assert d.stage == "llm_fallback"
    assert d.llm_classifier_used is True
    assert d.retriever == "Q-S"
    assert calls == ["음."]


def test_router_ignores_llm_answer_outside_the_enum():
    """폴백 분류기가 enum 밖 값을 주면 그대로 쓰지 않는다."""
    d = Router(classifier=lambda q: "Q-X").route("음.")
    assert d.retriever in {"Q-E", "Q-M", "Q-S"}
    assert d.stage == "default"


def test_router_without_classifier_still_answers():
    d = Router(classifier=None).route("음.")
    assert d.retriever in {"Q-E", "Q-M", "Q-S"}
    assert d.llm_classifier_used is False


def test_router_template_stage_names_a_real_cypher_template(router, golden):
    """템플릿 단계로 정해진 라우팅은 실제 존재하는 템플릿을 가리켜야 한다."""
    named = set()
    for q in golden.questions:
        d = router.route(q["question"])
        if d.stage == "template":
            assert d.template in CYPHER_TEMPLATES, f"{q['id']}: 없는 템플릿 {d.template!r}"
            named.add(d.template)
    assert named, "템플릿 단계로 잡히는 문항이 하나도 없다"


def test_required_cypher_templates_exist():
    missing = REQUIRED_TEMPLATES - set(CYPHER_TEMPLATES)
    assert not missing, f"A5 최소 템플릿 누락: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 2. Gap 3값 — 차단 케이스가 중심
# ---------------------------------------------------------------------------


def _ev(eid: str, snippet: str, *, source_type="pain_registry", sor="") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=eid,
        source_id=f"src::{eid}",
        locator=f"loc::{eid}",
        snippet=snippet,
        source_type=source_type,
        sensitivity="internal",
        source_of_record_for=sor,
    )


def test_gap_missing_link_alone_is_never_confirmed():
    """차단 케이스: Need→Capability 연결이 없다는 것만으로 CONFIRMED 를 내면 안 된다.

    근거: REVISED §5 A5 · §8 상시 불변 원칙("absence of evidence 를 evidence of absence 로
    취급하지 않는다"). 이 단언이 무너지면 제품에 없는 기능을 있다고/없다고 단정하게 된다.
    """
    need = [_ev("n1", "퇴직연금 사업자는 본사가 외부 Guest 를 통제하지 못한다")]
    finding = GapEngine().assess(
        subject="퇴직연금 BD 에서 외부 Guest 통제에 대응할 Capability 근거",
        need_evidence=need,
        capability_evidence=[],  # 연결이 하나도 없다
        scan_evidence=need,
    )
    assert finding.verdict is not GapVerdict.CONFIRMED
    assert finding.verdict is GapVerdict.POSSIBLE
    assert finding.explicit_absence_evidence_ids == []


def test_gap_confirmed_requires_explicit_absence_evidence():
    """같은 엔진이 CONFIRMED 도 낼 수 있어야 한다(항상 POSSIBLE 이면 위 차단 케이스는 무의미)."""
    absence = _ev(
        "a1",
        "모바일 PDF 다운로드 | 보안 정책상 제한 | 고객 정보보호팀도 다운로드 불가 의견",
        sor="구축 고객의 명시적 기능 부재와 그 사유",
    )
    finding = GapEngine().assess(
        subject="요청한 모바일 PDF 다운로드",
        need_evidence=[_ev("n1", "모바일에서 PDF 다운로드가 필요하다")],
        capability_evidence=[],
        scan_evidence=[absence],
    )
    assert finding.verdict is GapVerdict.CONFIRMED
    assert finding.explicit_absence_evidence_ids == ["a1"]


def test_gap_absence_phrase_from_unauthorised_source_does_not_confirm():
    """부재를 말할 자격이 없는 자료(무엇의 정본인지 선언되지 않은 자료)는 CONFIRMED 를 못 만든다."""
    absence = _ev(
        "a1",
        "모바일 PDF 다운로드 미지원",
        source_type="slack_thread",
        sor="",  # 이 자료는 기능 부재의 정본이 아니다
    )
    finding = GapEngine().assess(
        subject="요청한 모바일 PDF 다운로드",
        need_evidence=[_ev("n1", "모바일에서 PDF 다운로드가 필요하다")],
        capability_evidence=[],
        scan_evidence=[absence],
    )
    assert finding.verdict is not GapVerdict.CONFIRMED


def test_gap_absence_phrase_about_another_subject_does_not_confirm():
    """다른 주제의 부재 문장이 이 질문의 CONFIRMED 로 새어 들어오면 안 된다."""
    other = _ev(
        "a1",
        "카카오 상담톡이 발신 메시지 수정을 미지원. 정정 재발송으로 대체 안내",
        sor="구축 고객의 명시적 기능 부재와 그 사유",
    )
    finding = GapEngine().assess(
        subject="B2B 유통 Sales 영역에서 부족한 Capability",
        need_evidence=[_ev("n1", "업무 대화가 개인 카톡에 산재해 회사가 관리·통제 못 함")],
        capability_evidence=[],
        scan_evidence=[other],
    )
    assert finding.verdict is not GapVerdict.CONFIRMED


def test_gap_unknown_when_there_is_nothing_to_judge():
    finding = GapEngine().assess(
        subject="사용해 본 적 없는 주제",
        need_evidence=[],
        capability_evidence=[],
        scan_evidence=[],
    )
    assert finding.verdict is GapVerdict.UNKNOWN


def test_gap_out_of_scope_by_design_is_not_a_confirmed_gap():
    """'기술적으로는 가능하지만 핵심 목적이 아님'은 기능 부재가 아니다(계약 basis 필드 주석)."""
    oos = _ev(
        "o1",
        "현재는 AG 의 영업 성과를 평가하고 순위를 매기는 시스템이 아닙니다. "
        "분석하고 평가하는 것이 기술적으로는 가능합니다.",
        source_type="bd_openbook",
        sor="자동차금융 BD 의 제품 적용 범위와 한계",
    )
    finding = GapEngine().assess(
        subject="AG(외부 모집사)의 영업 성과를 평가하고 순위를 매기는 기능",
        need_evidence=[_ev("n1", "AG 영업 성과 평가 자동화 요구")],
        capability_evidence=[],
        scan_evidence=[oos],
    )
    assert finding.verdict is not GapVerdict.CONFIRMED
    assert finding.out_of_scope_by_design is True


def test_gap_contract_shape_matches_answer_schema():
    finding = GapEngine().assess(
        subject="퇴직연금 Guest 통제",
        need_evidence=[_ev("n1", "퇴직연금 Guest 통제 불가")],
        capability_evidence=[],
        scan_evidence=[],
    )
    payload = finding.to_contract()
    assert payload["verdict"] in {"CONFIRMED", "POSSIBLE", "UNKNOWN"}
    assert set(payload) <= {"subject", "verdict", "basis"}
    assert payload["basis"]["reason"]


def test_gap_reason_has_no_digits():
    """basis.reason 은 답변 본문으로 채점된다. 근거 없는 숫자를 스스로 만들지 않는다."""
    finding = GapEngine().assess(
        subject="퇴직연금 Guest 통제",
        need_evidence=[_ev("n1", "x"), _ev("n2", "y")],
        capability_evidence=[],
        scan_evidence=[],
    )
    assert not any(ch.isdigit() for ch in finding.to_contract()["basis"]["reason"])


def test_gap_subject_comes_from_the_question():
    subject = derive_gap_subject("마바손해보험이 요청한 모바일 PDF 다운로드는 제공되나?")
    assert "PDF" in subject
    assert "제공되나" not in subject
    assert "?" not in subject


def test_content_terms_drop_generic_words():
    terms = content_terms("우리 제품이 해결할 수 있는 문제와 부족한 Capability는")
    assert "제품" not in terms and "문제" not in terms
    assert "capability" in terms


# ---------------------------------------------------------------------------
# 3. citation 검증 (deterministic)
# ---------------------------------------------------------------------------


def test_citation_verification_drops_citations_without_real_evidence():
    kept, dropped = verify_citations(
        [
            {"marker": 1, "evidence_id": "ev_real"},
            {"marker": 2, "evidence_id": "ev_made_up"},
        ],
        {"ev_real"},
    )
    assert [c["marker"] for c in kept] == [1]
    assert [c["evidence_id"] for c in dropped] == ["ev_made_up"]


def test_citation_verification_keeps_nothing_when_pool_is_empty():
    kept, dropped = verify_citations([{"marker": 1, "evidence_id": "ev_real"}], set())
    assert kept == []
    assert len(dropped) == 1


# ---------------------------------------------------------------------------
# 4. 실데이터 — 공통 후처리
# ---------------------------------------------------------------------------


def test_qe_keeps_unverified_critical_items(corpus, service):
    """미검증 CRITICAL 이 Q-E 결과에서 사라지지 않는 것이 목적이다(REVISED §0 25행)."""
    result = service.retrieve("하늘IT와 어떤 접촉이 있었나?")
    assert result.route.retriever == "Q-E"
    assert result.critical_items, "critical lane 항목이 하나도 합류되지 않았다"
    statuses = {item.status for item in result.critical_items}
    assert statuses - {"VERIFIED"}, "미검증(CRITICAL/UNVERIFIED) 항목이 전부 걸러졌다"
    assert result.notices["critical_unverified_included"] is True


def test_status_and_authority_are_not_hard_filters(corpus, service):
    """필터는 ACL·시간뿐이다(policy.schema.json consumption_points · x-rules)."""
    result = service.retrieve("하늘IT와 어떤 접촉이 있었나?")
    kinds = {item.status for item in result.critical_items}
    assert "UNVERIFIED" in kinds or "CRITICAL" in kinds
    # 근거 풀에도 미검증 항목이 남긴 Evidence 가 살아 있어야 한다.
    critical_evidence = {e for item in result.critical_items for e in item.evidence_ids}
    pool = {e.evidence_id for e in result.graph_evidence}
    assert critical_evidence & pool, "미검증 항목의 Evidence 가 근거 풀에서 사라졌다"


def test_sensitivity_is_the_gate_and_it_reports_incompleteness(corpus, service):
    """restricted 를 막는 정책에서는 그 파생물이 빠지고 notices 로만 알린다(개수는 밝히지 않는다)."""
    strict = AccessPolicy(allowed_sensitivity=frozenset({"public"}))
    open_result = service.retrieve("가나손해보험 딜은 지금 어느 단계인가?")
    closed_result = service.retrieve("가나손해보험 딜은 지금 어느 단계인가?", policy=strict)

    assert len(closed_result.graph_evidence) < len(open_result.graph_evidence)
    assert closed_result.notices["results_may_be_incomplete"] is True
    assert "denied_source_count" not in closed_result.notices


def test_event_and_account_connect_through_observation(corpus, service):
    """DECISIONS A-1: 직접 엣지가 없어도 Observation 경유로 도달한다."""
    result = service.retrieve("하늘IT와 어떤 접촉이 있었나?")
    labels = {lbl for node in result.subgraph.nodes for lbl in node.labels}
    assert "Account" in labels
    assert "Event" in labels, "Event 를 Account 에서 못 찾았다(경유 경로 미구현)"
    edge_types = {e.type for e in result.subgraph.edges}
    assert {"OBSERVED_IN", "MENTIONS"} <= edge_types


def test_absent_direct_edge_is_not_reported_as_no_relationship(corpus, service):
    """Feature↔Product 는 직접 엣지가 없다. Claim ABOUT 경유로 도달해야 한다."""
    rows = service.run_template("feature_version_history", {"name": "비회원 채팅 신청"})
    assert rows, "기능 버전 이력 템플릿이 빈 결과를 냈다"


# ---------------------------------------------------------------------------
# 5. raw signals — Graph 미반영 정보의 안전망
# ---------------------------------------------------------------------------


def test_raw_signals_recover_information_absent_from_the_graph(service, orphan_evidence):
    """차단 케이스: 구조화되지 않은 Evidence 가 답변 재료에서 사라지면 안 된다(§4 ⑦)."""
    token = orphan_evidence["token"]
    result = service.retrieve(f"{token} 도입 조건은 무엇인가?")

    found = [s for s in result.raw_signals if s.evidence_id == orphan_evidence["evidence_id"]]
    assert found, "fulltext 안전망이 구조화되지 않은 Evidence 를 못 찾았다"
    assert found[0].in_graph is False
    assert result.notices["raw_signal_count"] >= 1

    # 합성 인용과 섞이면 안 된다.
    assert orphan_evidence["evidence_id"] not in {e.evidence_id for e in result.graph_evidence}


def test_rare_critical_question_surfaces_its_single_occurrence_fact(corpus, service, golden):
    """Gate 1A 항목 4: 자료 한 곳에만 있는 사실이 Graph 미반영이어도 답변 재료에 실려야 한다.

    기대값은 golden.yaml 의 GQ-D6 `source_quote` 를 그대로 읽어 쓴다(구현보다 먼저 쓰인 값).
    """
    question = golden.by_id("GQ-D6")
    result = service.retrieve(question["question"])
    pool = [e.snippet for e in result.graph_evidence] + [s.snippet for s in result.raw_signals]

    missing = [
        item["source_quote"]
        for item in question["must_include"]
        if not any(contains(snippet, item["source_quote"]) for snippet in pool)
    ]
    assert not missing, f"1회 등장 정보가 재료에서 빠졌다: {missing}"


def test_raw_signals_are_empty_when_nothing_matches(service):
    """대조군. 항상 채워지는 것이라면 위 테스트는 아무것도 증명하지 못한다."""
    result = service.retrieve("뷁뷀뾃쭓쀓 삸삵삶")
    assert result.raw_signals == []
    assert result.notices["raw_signal_count"] == 0


def test_raw_signals_marked_in_graph_when_already_structured(corpus, service):
    result = service.retrieve("모바일 PDF 다운로드는 제공되나?")
    assert result.raw_signals, "실제 자료가 있는 질문인데 원문 검색이 비었다"
    assert any(s.in_graph for s in result.raw_signals)


# ---------------------------------------------------------------------------
# 6. Q-M / Q-S
# ---------------------------------------------------------------------------


def test_qm_returns_deterministic_aggregates(corpus, service):
    result = service.retrieve("여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?")
    assert result.route.retriever == "Q-M"
    assert result.route.template == "common_needs_across_domains"
    rows = result.aggregates["template_rows"]
    assert rows, "Cross-BD 공통 Need 집계가 비었다"
    for row in rows:
        assert row["domain_count"] >= 2, "'여러 BD 공통' 인데 도메인이 하나뿐인 행이 섞였다"


def test_qm_aggregation_is_stable_across_runs(service):
    a = service.retrieve("여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?")
    b = service.retrieve("여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?")
    assert a.aggregates["template_rows"] == b.aggregates["template_rows"]


def test_qs_collects_aggregates_before_synthesis(service):
    """§6 A5: BD/Industry → Need → Account/Deal → Capability → Gap → Competitor → Evidence 순."""
    result = service.retrieve("어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?")
    assert result.route.retriever == "Q-S"
    order = result.aggregates["collection_order"]
    assert order == [
        "business_domain",
        "industry",
        "need",
        "account_deal",
        "capability",
        "gap",
        "competitor",
        "evidence",
    ]
    for stage in order:
        assert stage in result.aggregates, f"집계 단계 누락: {stage}"
    assert result.synthesis_input["aggregates"], "집계가 합성 입력에 실리지 않았다"


def test_qs_synthesis_input_is_an_abstract_not_the_corpus(service):
    """'자료를 통째로 L 에 투입'으로 퇴화하지 않게 상한을 둔다(§11 Risk)."""
    result = service.retrieve("어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?")
    evidence = result.synthesis_input["evidence"]
    assert len(evidence) <= RetrievalService.SYNTHESIS_EVIDENCE_LIMIT
    for item in evidence:
        assert len(item["snippet"]) <= RetrievalService.SYNTHESIS_SNIPPET_CHARS
    # 합성 입력의 발췌는 근거 풀에서 고른 것이어야 한다. 새 원문을 끌어오지 않는다.
    pool = {e.evidence_id for e in result.graph_evidence}
    assert {item["evidence_id"] for item in evidence} <= pool
    # 집계도 통째가 아니라 단계별 상한을 지킨 축약본이다.
    # (문자열 항목은 데이터 행이 아니라 「몇 행을 뺐다」 표시라 상한에 세지 않는다)
    for stage, rows in result.synthesis_input["aggregates"].items():
        if isinstance(rows, list) and stage != "collection_order":
            data_rows = [r for r in rows if not isinstance(r, str)]
            assert len(data_rows) <= RetrievalService.SYNTHESIS_ROWS_PER_STAGE, stage
    assert "estimated_label_required" in result.synthesis_input


def test_qs_separates_estimation_from_fact(service):
    result = service.retrieve("어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?")
    assert result.synthesis_input["estimated_label_required"] is True


def test_compact_aggregates_는_evidence_id_목록을_모델에게_보내지_않는다():
    """id 원문이 프롬프트에 실리면 모델이 옮겨 적고, 게이트가 그 안의 숫자를 무근거로
    판정해 문장째 지운다(api/synthesis.py 모듈 주석의 실측 사고와 같은 유형).
    집계의 evidence 키는 게이트·화면용이고 합성 입력에서는 뺀다."""
    svc = object.__new__(RetrievalService)
    compacted = RetrievalService._compact_aggregates(
        svc,
        {
            "business_domain": [{"name": "BD"}],
            "evidence": ["ev_dfc58243098abc12", "ev_0503aa1122bb33cc"],
        },
    )
    assert "evidence" not in compacted
    assert "business_domain" in compacted


def test_compact_aggregates_는_행_절단을_같은_자리에서_밝힌다():
    """상류가 말없이 10행으로 자르면, 하류 프롬프트의 「없다는 뜻이 아니다」 표기가
    닿기 전에 이미 행이 사라져 있다. 자른 그 자리에서 몇 행을 뺐는지 적는다."""
    svc = object.__new__(RetrievalService)
    rows = [{"name": f"row{i}"} for i in range(25)]
    compacted = RetrievalService._compact_aggregates(svc, {"business_domain": rows})
    trimmed = compacted["business_domain"]
    limit = RetrievalService.SYNTHESIS_ROWS_PER_STAGE
    assert len([r for r in trimmed if isinstance(r, dict)]) == limit
    marker = [r for r in trimmed if isinstance(r, str)]
    assert marker and "전체 25행" in marker[0], trimmed[-1]


def test_각주_삭제가_소수점을_문장_경계로_쪼개지_않는다():
    """'2.85억 … [7].' 에서 [7] 을 지울 때 '3.' 뒤를 문장 끝으로 보면
    '격차는 3.' 이라는 훼손 조각이 남는다. 마침표는 뒤가 공백이거나 줄 끝일 때만
    경계다(guards.split_sentences 와 같은 규칙)."""
    from retrieval.citations import strip_uncited_sentences

    text = "격차는 2.85억 규모입니다[7]. 다른 사실은 남는다[2]."
    out = strip_uncited_sentences(text, [7])
    assert "3." not in out and "77억" not in out, out
    assert "다른 사실은 남는다[2]." in out


def test_compact_aggregates_는_상한_이하면_표시를_붙이지_않는다():
    svc = object.__new__(RetrievalService)
    rows = [{"name": f"row{i}"} for i in range(3)]
    compacted = RetrievalService._compact_aggregates(svc, {"need": rows})
    assert compacted["need"] == rows


def test_qs_emits_a_gap_with_a_three_value_verdict(service):
    result = service.retrieve(
        "퇴직연금 BD에서 본사가 외부 Guest를 통제하지 못하는 문제에 대응할 Capability 근거가 있나?"
    )
    assert result.gaps, "Q-S 인데 gap 판정이 없다"
    for gap in result.gaps:
        assert gap.verdict in tuple(GapVerdict)
        if gap.verdict is GapVerdict.CONFIRMED:
            assert gap.explicit_absence_evidence_ids


def test_live_gap_does_not_confirm_on_a_missing_link(service):
    """차단 케이스(실데이터판). 연결이 없다는 사실만으로 CONFIRMED 가 나오면 실패다."""
    result = service.retrieve(
        "퇴직연금 BD에서 본사가 외부 Guest를 통제하지 못하는 문제에 대응할 Capability 근거가 있나?"
    )
    verdicts = {gap.verdict for gap in result.gaps}
    assert GapVerdict.CONFIRMED not in verdicts


# ---------------------------------------------------------------------------
# 7. subgraph · 계약 상한
# ---------------------------------------------------------------------------


def test_subgraph_respects_contract_limits(service):
    for question in (
        "하늘IT와 어떤 접촉이 있었나?",
        "여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?",
        "어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?",
    ):
        result = service.retrieve(question)
        assert len(result.subgraph.nodes) <= 50, question
        assert len(result.subgraph.edges) <= 100, question
        node_ids = {n.id for n in result.subgraph.nodes}
        for edge in result.subgraph.edges:
            assert edge.source in node_ids and edge.target in node_ids


def test_every_route_produces_a_focal_node(corpus, service):
    """§10 의 시각 위계(focal > cited > supporting)는 focal 이 있어야 성립한다.

    질문이 이름을 하나도 대지 않는 전략 질문에서도 초점이 있어야 한다.
    eval 의 check_subgraph 도 focal 부재를 실패로 본다.
    """
    for question in (
        "하늘IT와 어떤 접촉이 있었나?",
        "여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?",
        "어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?",
    ):
        result = service.retrieve(question)
        assert any(n.rank == "focal" for n in result.subgraph.nodes), question


def test_guessed_entities_do_not_narrow_strategic_scope(corpus, service):
    """전사 단위 질문이 원문 검색으로 짐작한 엔티티 하나 때문에 좁아지면 안 된다."""
    result = service.retrieve("어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?")
    assert result.aggregates["capability"], "물어본 축(Capability)이 통째로 비었다"
    assert result.aggregates["business_domain"], "BusinessDomain 집계가 비었다"


def test_subgraph_ranks_focal_first(corpus, service):
    result = service.retrieve("하늘IT와 어떤 접촉이 있었나?")
    ranks = [n.rank for n in result.subgraph.nodes]
    assert "focal" in ranks
    assert set(ranks) <= {"focal", "cited", "supporting"}


def test_retrieval_result_carries_every_material_a6_needs(service):
    result = service.retrieve("하늘IT와 어떤 접촉이 있었나?")
    for field in (
        "route",
        "focal_entities",
        "graph_evidence",
        "raw_signals",
        "aggregates",
        "gaps",
        "critical_items",
        "subgraph",
        "synthesis_input",
    ):
        assert hasattr(result, field), f"A6 재료 누락: {field}"


def test_service_refuses_a_writable_graph():
    """질의 계층은 읽기 전용 세션만 쓴다."""
    with pytest.raises(TypeError):
        RetrievalService(writable_graph())


# ---------------------------------------------------------------------------
# 8. 후보 풀 — 근거가 그래프에 있는데 후보에조차 못 들어오는 일이 없어야 한다
# ---------------------------------------------------------------------------


def test_candidate_pool_gives_every_group_its_first_turn():
    """차단 케이스: 행이 많은 집계 하나가 상한을 통째로 먹으면 안 된다.

    같은 재료를 이어 붙여 앞에서 자르면 작은 그룹이 통째로 사라지는 것을 대조로 보인다.
    """
    pool = CandidatePool()
    pool.add("bulky", [f"ev{i}" for i in range(100)])
    pool.add("tiny", ["ev_target"])

    assert "ev_target" in pool.balanced(10)

    flat = [f"ev{i}" for i in range(100)] + ["ev_target"]
    assert "ev_target" not in flat[:10], "대조군이 성립하지 않는다"


def test_candidate_pool_respects_the_limit_and_drops_duplicates():
    pool = CandidatePool()
    pool.add("a", ["x", "y", "x"])
    pool.add("b", ["y", "z"])
    assert pool.balanced(None) == ["x", "y", "z"]
    assert len(pool.balanced(2)) == 2


def test_candidate_pool_rows_become_separate_groups():
    pool = CandidatePool()
    pool.add_rows("need", [{"evidence_ids": ["a1", "a2"]}, {"evidence_ids": ["b1"]}])
    assert pool.group_count == 2
    assert pool.balanced(2) == ["a1", "b1"], "행마다 한 건씩 먼저 들어가야 한다"


def test_candidate_pool_fills_the_rest_depth_first():
    """한 건씩 돌린 뒤에는 원래 순서대로 깊이 채운다(앞 그룹이 얇아지지 않게)."""
    pool = CandidatePool()
    pool.add("first", ["a1", "a2", "a3"])
    pool.add("second", ["b1", "b2"])
    assert pool.balanced(None) == ["a1", "b1", "a2", "a3", "b2"]


#: 이 문항들의 기대 발췌는 그래프에 실재하는 것을 확인했다(Evidence.snippet 직접 조회).
#: 그러므로 후보에 못 들어오면 그것은 자료 문제가 아니라 검색 결함이다.
#:
#: GQ-D4 는 아직 여기 없다. Pain Point 목록!E2 발췌가 후보에 안 들어오는데, 원인이 검색이
#: 아니라 common_needs_across_domains 템플릿이 Need 하나에 모은 발췌가 관측 8건 대비 6건뿐인
#: 데 있다(Q-M 집계 쪽). 고쳐지면 이 목록에 넣는다. 통과하는 것만 넣어 초록불을 만들지 말 것.
POOL_MUST_REACH = ("GQ-D1", "GQ-D3", "GQ-B1", "GQ-G1")


def _candidate_snippets(result) -> list[str]:
    """A6 가 근거 후보로 받는 것 전부. graph 순회분과 원문 검색분을 합친다."""
    return [e.snippet for e in result.graph_evidence] + [s.snippet for s in result.raw_signals]


@pytest.mark.parametrize("qid", POOL_MUST_REACH)
def test_expected_excerpt_reaches_the_candidate_pool(corpus, service, golden, qid):
    """기대값은 golden.yaml 의 source_quote 를 그대로 읽는다(구현보다 먼저 쓰인 값)."""
    question = golden.by_id(qid)
    pool = _candidate_snippets(service.retrieve(question["question"]))
    missing = [
        f"{item['fact']} @ {item['locator']}"
        for item in question["must_include"]
        if not any(contains(snippet, item["source_quote"]) for snippet in pool)
    ]
    assert not missing, f"{qid}: 그래프에 있는 근거가 후보 풀에 안 들어왔다 — {missing}"


def test_critical_lane_joins_even_when_attached_to_a_neighbour(corpus, service, golden):
    """차단 케이스: CRITICAL 이 focal 이 아니라 한 칸 옆(그 고객사의 딜)에 붙어 있을 때.

    focal 키로만 찾으면 이 항목들이 통째로 사라진다. 실측에서 가나손해보험 질문의
    CRITICAL 넷이 전부 Account 가 아니라 Deal 에 ABOUT 으로 달려 있었다.
    """
    result = service.retrieve(golden.by_id("GQ-D1")["question"])
    focal = {e.key for e in result.focal_entities}
    neighbour_criticals = [
        item for item in result.critical_items if item.about and not (set(item.about) & focal)
    ]
    assert neighbour_criticals, "focal 옆에 붙은 critical lane 항목이 하나도 합류하지 않았다"

    pool = {e.evidence_id for e in result.graph_evidence}
    assert any(
        set(item.evidence_ids) & pool for item in neighbour_criticals
    ), "합류는 했는데 그 근거가 후보 풀에 없다"


@pytest.mark.parametrize("qid", POOL_MUST_REACH)
def test_wider_candidate_pool_does_not_widen_the_synthesis_input(service, golden, qid):
    """후보는 넓히되 합성 입력은 상한을 지킨다(§11 Risk: 자료 통째 투입 방지)."""
    result = service.retrieve(golden.by_id(qid)["question"])
    evidence = result.synthesis_input["evidence"]
    assert len(evidence) <= RetrievalService.SYNTHESIS_EVIDENCE_LIMIT
    for item in evidence:
        assert len(item["snippet"]) <= RetrievalService.SYNTHESIS_SNIPPET_CHARS


# ---------------------------------------------------------------------------
# 9. 근거 출처 표시 — A6 가 24건을 고를 때 한 곳이 독식하지 않게 하는 재료
# ---------------------------------------------------------------------------


def test_candidate_pool_remembers_where_each_candidate_came_from():
    pool = CandidatePool()
    pool.add_rows("need", [{"evidence_ids": ["a1", "a2"]}, {"evidence_ids": ["b1", "a1"]}])
    origins = pool.origins()
    assert origins["a1"] == "need#0", "같은 근거를 두 행이 내면 처음 낸 행이 주인이다"
    assert origins["a2"] == "need#0"
    assert origins["b1"] == "need#1"


def test_every_candidate_carries_an_origin(service, golden):
    """차단 케이스: 출처가 안 붙은 후보가 있으면 A6 의 몫 나누기가 그만큼 헐거워진다."""
    result = service.retrieve(golden.by_id("GQ-D1")["question"])
    unknown = [
        e.evidence_id
        for e in result.graph_evidence
        if not result.evidence_groups.get(e.evidence_id)
    ]
    assert not unknown, f"출처가 안 붙은 후보 {len(unknown)}건 — {unknown[:5]}"


def test_raw_signals_get_their_own_origin_separate_from_graph_evidence(corpus, service, golden):
    result = service.retrieve(golden.by_id("GQ-D6")["question"])
    graph_ids = {e.evidence_id for e in result.graph_evidence}
    raw_only = [s for s in result.raw_signals if s.evidence_id not in graph_ids]
    assert raw_only, "이 문항은 원문 검색으로만 닿는 발췌가 있어야 성립한다"
    for signal in raw_only:
        origin = result.evidence_groups.get(signal.evidence_id) or ""
        assert origin.startswith(("raw#", "rare#")), (
            f"원문 검색분에 그래프 후보와 같은 출처가 붙었다: {origin!r}"
        )


def test_a_source_with_only_a_handful_of_excerpts_is_marked_rare(corpus, service, golden):
    """REVISED §5 의 안전망이 걸리는 자리. 발췌가 몇 줄뿐인 자료는 자르면 안 된다.

    하늘IT 검토 메모가 그 자료다. 이 표시가 없으면 A6 가 다른 자료와 같은 잣대로 잘라
    그 자료에만 적힌 기술 전제가 사라진다(Gate 1A 4번).
    """
    result = service.retrieve(golden.by_id("GQ-D6")["question"])
    rare = {
        origin for origin in result.evidence_groups.values() if origin.startswith("rare#")
    }
    assert rare, "발췌가 몇 줄뿐인 자료가 하나도 rare 로 표시되지 않았다"


def test_ontology_words_never_become_search_terms():
    """차단 케이스: 우리 온톨로지 이름을 검색어로 쓰면 스키마 문서가 상위를 채운다."""
    from retrieval.text import search_terms

    terms = content_terms("현재 회사가 개척하려는 Business Domain들은 무엇이고 각각 어느 단계인가?")
    assert "business" in terms, "겹침 점수에서는 그대로 쓴다"
    assert "business" not in search_terms(terms)
    assert "domain들" not in search_terms(terms) and "domain" not in search_terms(terms)
    assert "개척" in search_terms(terms), "업무 낱말까지 지우면 검색이 통째로 죽는다"


def test_verb_endings_are_trimmed_so_a_question_can_meet_the_source_wording():
    """질문의 "통제하지 못하는"이 자료의 "통제 불가"와 만나야 한다."""
    terms = content_terms("본사가 외부 Guest를 통제하지 못하는 문제에 대응할 근거가 있나")
    assert "통제" in terms, f"어미가 안 떨어졌다: {terms}"
    assert "대응" in terms, f"어미가 안 떨어졌다: {terms}"
    assert "통제하지" not in terms and "대응할" not in terms


# ---------------------------------------------------------------------------
# 새로 넣은 자료가 일반 질문에서 안 잡히던 두 원인
# ---------------------------------------------------------------------------
#
# 기대값의 근거는 실측이다(2026-08-13, 고치기 전 코드로 잰 것).
#   ① content_terms("…소개해야 하나?") → ['고객','소개','하나'] 이고 focal 에 가람저축은행이 왔다
#   ② 소미생명 제안서 발췌 32건 중 본문에 '푸본' 이 든 것 0건. 그 회사를 물으면
#      후보 풀 21건 중 그 자료 몫이 0건이라 점수를 줄 대상 자체가 없었다


def test_question_ending_is_not_a_search_term():
    """「~해야 하나?」의 `하나` 는 종결어미다. 낱말로 쓰면 가람금융 계열 자료가 걸린다."""
    terms = content_terms("우리 제품을 고객에게 어떻게 소개해야 하나?")
    assert "하나" not in terms, f"종결어미가 낱말로 남았다: {terms}"
    assert "소개" in terms, "업무 낱말까지 지우면 검색이 죽는다"


def test_company_name_that_begins_with_that_ending_survives():
    """차단 케이스: `하나` 를 빼면서 `마바손해보험` 까지 지우면 골든 GQ-B3 이 죽는다."""
    terms = content_terms("마바손해보험이 요청한 모바일 PDF 다운로드는 제공되나?")
    assert "마바손해보험" in terms, f"회사 이름이 사라졌다: {terms}"


def test_a_source_named_after_a_customer_is_found_by_that_name(corpus, graph):
    """자료 이름에만 고객사가 적힌 제안서를 그 이름으로 찾을 수 있어야 한다.

    본문 검색(`sources_mentioning`)으로는 못 찾는다 — 32건 어디에도 회사 이름이 없다.
    """
    with graph.session() as session:
        by_name = store.sources_named(session, ["소미생명"])
        by_body = store.sources_mentioning(session, ["소미생명"], limit=12)
    assert "src_doc_proposal_somi_life" in by_name
    assert "src_doc_proposal_somi_life" not in by_body, (
        "본문에 이름이 있으면 이 테스트가 재현하려는 상황이 아니다"
    )


def test_source_name_lookup_ignores_unrelated_names(graph):
    """차단 케이스: 아무 이름이나 자료를 물어오면 후보가 오염된다."""
    with graph.session() as session:
        assert store.sources_named(session, ["존재하지않는회사이름"]) == []
        assert store.sources_named(session, [""]) == []


def test_customer_named_only_in_the_file_name_still_reaches_the_answer(corpus, service):
    """제안서 본문에 회사 이름이 없어도 그 회사를 물으면 근거로 와야 한다."""
    result = service.retrieve(
        "소미생명에 제안한 내용은 무엇인가?", policy=AccessPolicy()
    )
    got = [
        e
        for e in list(result.graph_evidence) + list(result.raw_signals)
        if e.source_id == "src_doc_proposal_somi_life"
    ]
    assert got, "후보 풀에 그 제안서 발췌가 한 건도 없다"
