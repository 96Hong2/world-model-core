"""A6 Answer API 검증.

기대값의 근거는 세 곳뿐이다.
  · `contracts/answer.schema.json` — 필수 필드·금지 필드·상한
  · REVISED-MASTER-PLAN §6 A6 · §8 상시 하드 게이트 3종 · §10 Graph UX
  · `config/pii-patterns.yaml` 의 탐지기 정의

시스템 출력에서 복사한 기대값은 없다. 통과 케이스만 늘리면 게이트가 죽어 있어도 초록불이
되므로, 하드 게이트는 **일부러 위반한 합성 입력**을 넣어 걸리는지로 확인한다.

LLM 은 부르지 않는다. 합성기는 주입 가능한 부품이라 테스트가 자기 입력을 통제한다.
"""

from __future__ import annotations

import json
import pathlib
import sys

import os

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.connection import ReadOnlyGraph, read_only_graph  # noqa: E402
from retrieval.types import EvidenceRef  # noqa: E402

from api import contradiction, selection, strength  # noqa: E402
from api.accounts import ACCOUNTS, authenticate  # noqa: E402
from api.contracts import answer_validator  # noqa: E402
from api.guards import (  # noqa: E402
    HARD_GATE_DETECTORS,
    find_pii,
    payload_text,
    unsupported_tokens,
)
from api.service import AnswerService  # noqa: E402
from api.synthesis import Synthesis  # noqa: E402

# 대표 질문 3개. 세 경로(Q-E·Q-M·Q-S)를 모두 지나가도록 고른다.
REPRESENTATIVE = [
    "비회원 고객이 같은 기기로 재방문하면 상담이 이어지나? 언제부터 되나?",
    "여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?",
    "어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?",
]


class ScriptedSynthesizer:
    """테스트가 답변 문장을 직접 정하는 합성기.

    실제 LLM 자리에 끼워 넣어 "게이트가 무엇을 막는가"만 시험한다.
    """

    def __init__(self, factory):
        self._factory = factory
        self.calls: list[dict] = []

    def synthesize(self, *, result, evidence, **kwargs):
        self.calls.append({"route": result.route.retriever, "evidence": len(evidence)})
        return self._factory(result, evidence)


def echo_first_evidence(result, evidence):
    """근거 첫 줄을 그대로 옮기는 합성기. 근거 안의 말만 쓰므로 게이트에 걸릴 이유가 없다."""
    if not evidence:
        return Synthesis(text="확인된 자료가 없습니다.")
    first = evidence[0]
    quoted = " ".join(first.snippet.split())
    return Synthesis(
        text=f"{quoted}[1]",
        citations=[{"marker": 1, "evidence_id": first.evidence_id}],
    )


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def graph() -> ReadOnlyGraph:
    g = read_only_graph()
    g.verify_connectivity()
    yield g
    g.close()


@pytest.fixture(scope="module")
def validator():
    return answer_validator()


@pytest.fixture
def service(graph, tmp_path):
    return AnswerService(
        graph,
        synthesizer=ScriptedSynthesizer(echo_first_evidence),
        audit_path=tmp_path / "query.jsonl",
    )


@pytest.fixture(scope="module")
def demo_account():
    return ACCOUNTS["demo"]


def schema_errors(validator, answer: dict) -> list[str]:
    return [
        f"{list(e.path)} {e.message}"
        for e in sorted(validator.iter_errors(answer), key=lambda e: list(e.path))
    ]


# ---------------------------------------------------------------------------
# 1. 계약 준수
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", REPRESENTATIVE)
def test_answer_satisfies_the_answer_contract(service, validator, demo_account, question):
    answer = service.answer(question, account=demo_account)
    assert not schema_errors(validator, answer), schema_errors(validator, answer)


def test_answer_carries_every_required_section(service, demo_account):
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    for field in (
        "answer",
        "citations",
        "evidence",
        "evidence_strength",
        "unknowns",
        "gaps",
        "subgraph",
        "route",
        "notices",
    ):
        assert field in answer, f"계약 필수 필드 누락: {field}"


def test_no_numeric_confidence_anywhere(service, demo_account):
    """확률처럼 읽히는 숫자를 노출하지 않는다(계약 x-forbidden-fields)."""
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    assert "confidence" not in answer
    assert "confidence_band" not in answer
    strength = answer["evidence_strength"]
    assert strength["band"] in {"HIGH", "MEDIUM", "LOW"}
    assert "score" not in strength and "probability" not in strength
    for key, value in strength["basis"].items():
        if isinstance(value, float):
            pytest.fail(f"basis.{key} 가 실수다. 확률로 읽힌다: {value}")


def test_denied_source_count_is_never_reported(service, demo_account):
    """권한으로 걸러진 것은 개수·존재를 밝히지 않는다(계약 x-forbidden-fields)."""
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    assert "denied_source_count" not in answer
    dumped = json.dumps(answer, ensure_ascii=False)
    assert "denied_source_count" not in dumped
    assert isinstance(answer["notices"]["results_may_be_incomplete"], bool)


# ---------------------------------------------------------------------------
# 2. sensitivity — restricted 파생물 필터
# ---------------------------------------------------------------------------


def test_restricted_evidence_is_filtered_for_a_limited_account(corpus, service):
    """restricted Source 파생물이 계정 정책대로 걸러지는가."""
    limited = ACCOUNTS["viewer"]
    assert "restricted" not in limited.policy.allowed_sensitivity

    answer = service.answer("하늘IT와 어떤 접촉이 있었나?", account=limited)
    sources = {e["source_id"] for e in answer["evidence"]}
    sources |= {r["source_id"] for r in answer.get("raw_signals") or []}
    restricted = service.restricted_source_ids()
    assert restricted, "restricted 로 표시된 Source 가 하나도 없다. 이 테스트가 무의미해진다"
    leaked = sources & restricted
    assert not leaked, f"제한 자료가 새어 나왔다: {sorted(leaked)}"


def test_demo_account_sees_more_than_the_limited_account(corpus, service):
    """대조군. 둘 다 0건이면 필터가 아니라 검색이 죽은 것이다."""
    question = "하늘IT와 어떤 접촉이 있었나?"
    full = service.answer(question, account=ACCOUNTS["demo"])
    limited = service.answer(question, account=ACCOUNTS["viewer"])
    assert len(full["evidence"]) > 0
    assert len(full["evidence"]) >= len(limited["evidence"])


def test_incompleteness_notice_is_raised_when_something_is_filtered(corpus, service):
    answer = service.answer("하늘IT와 어떤 접촉이 있었나?", account=ACCOUNTS["viewer"])
    assert answer["notices"]["results_may_be_incomplete"] is True


# ---------------------------------------------------------------------------
# 3. 하드 게이트 — 일부러 위반한 입력을 넣어 걸리는지 본다
# ---------------------------------------------------------------------------


def test_pii_in_a_synthesised_sentence_is_removed(graph, tmp_path):
    """§8 하드 게이트 1: 답변·evidence·subgraph 어디에도 PII 정규식 매칭 0건."""

    def leaky(result, evidence):
        return Synthesis(
            text="담당자 연락처는 010-1234-5678 입니다. 나머지는 확인된 자료가 없습니다."
        )

    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(leaky), audit_path=tmp_path / "q.jsonl"
    )
    answer = svc.answer("가나손해보험 담당자 연락처를 알려줘.", account=ACCOUNTS["demo"])

    assert find_pii(payload_text(answer)) == []
    assert "010-1234-5678" not in json.dumps(answer, ensure_ascii=False)
    assert answer["notices"].get("guard_violation") is None  # 계약 밖 필드를 쓰지 않는다
    assert svc.last_guard_report.pii_hits, "PII 를 지웠으면 그 사실이 보고에 남아야 한다"


def test_number_that_no_evidence_supports_is_removed(graph, tmp_path):
    """§8 하드 게이트 3: 무근거 숫자 0건."""

    def hallucinating(result, evidence):
        return Synthesis(
            text="확인된 자료를 정리했습니다. 도입 고객사는 9,731곳입니다."
        )

    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(hallucinating), audit_path=tmp_path / "q.jsonl"
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    assert "9,731" not in answer["answer"]["text"]
    assert svc.last_guard_report.unsupported_numbers


def test_proper_noun_that_no_evidence_supports_is_removed(graph, tmp_path):
    """§8 하드 게이트 3: 무근거 고유명사 0건."""

    def hallucinating(result, evidence):
        return Synthesis(text="짜맞춤파랑고래보험 도입 사례가 있습니다.")

    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(hallucinating), audit_path=tmp_path / "q.jsonl"
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    assert "짜맞춤파랑고래보험" not in answer["answer"]["text"]
    assert svc.last_guard_report.unsupported_terms


def test_removal_report_says_which_token_caused_the_cut(graph, tmp_path):
    """개수만 세면 답변이 빈약해진 원인이 표기 차이인지 날조인지 가릴 수 없다."""

    def hallucinating(result, evidence):
        return Synthesis(text="확인된 자료를 정리했습니다. 도입 고객사는 9,731곳입니다.")

    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(hallucinating), audit_path=tmp_path / "q.jsonl"
    )
    svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])

    removals = svc.last_guard_report.removals
    assert removals, "지운 문장이 있으면 사유가 남아야 한다"
    cut = next(r for r in removals if "9,731" in r.sentence)
    assert cut.location == "answer.text"
    assert "9731" in cut.numbers or "9,731" in cut.numbers


def test_removal_log_in_the_audit_never_carries_the_pii_it_removed(graph, tmp_path):
    """지운 이유를 남기려다 지운 값을 로그로 되살리면 게이트를 우회한 것이 된다."""
    path = tmp_path / "q.jsonl"

    def leaky(result, evidence):
        return Synthesis(text="담당자 연락처는 010-1234-5678 입니다.")

    svc = AnswerService(graph, synthesizer=ScriptedSynthesizer(leaky), audit_path=path)
    svc.answer("가나손해보험 담당자 연락처를 알려줘.", account=ACCOUNTS["demo"])

    written = path.read_text(encoding="utf-8")
    assert "010-1234-5678" not in written
    row = json.loads(written.splitlines()[0])
    assert row["guard_removals"], "무엇을 왜 뺐는지가 로그에 남아야 한다"
    assert find_pii(json.dumps(row["guard_removals"], ensure_ascii=False)) == []


def test_raw_signal_section_never_repeats_an_excerpt_already_cited_as_evidence(
    service, demo_account
):
    """계약이 "합성 인용과 섞지 않는다"고 못 박은 자리다.

    원문 검색 발췌는 근거 후보 풀에 들어가므로 좋은 것은 evidence 로 승격된다. 승격된 것을
    "추가 원문 근거"에도 남기면 화면이 같은 발췌를 두 번 보여 주고, 그 섹션이 "답변의
    근거로 쓰지 않았습니다"라고 적어 둔 안내문이 거짓이 된다.
    """
    for question in REPRESENTATIVE:
        answer = service.answer(question, account=demo_account)
        evidence_ids = {e["evidence_id"] for e in answer["evidence"]}
        raw_ids = {r["evidence_id"] for r in answer.get("raw_signals") or []}
        assert not (evidence_ids & raw_ids), (
            f"{question}: 같은 발췌가 근거와 추가 원문 근거에 동시에 실렸다"
        )


def test_raw_signal_count_matches_what_the_screen_shows(service, demo_account):
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    assert answer["notices"]["raw_signal_count"] == len(answer.get("raw_signals") or [])


def test_gap_reason_does_not_ship_english_ontology_labels(service, demo_account):
    """분류어는 자료에 없는 우리 말이라 그대로 내보내면 무근거 고유명사가 된다."""
    answer = service.answer(REPRESENTATIVE[2], account=demo_account)
    for gap in answer.get("gaps") or []:
        reason = (gap.get("basis") or {}).get("reason") or ""
        for label in ("Capability", "Need", "BusinessDomain"):
            assert label not in reason, f"판정 사유에 영어 분류어가 남아 있다: {label}"


def test_a_sentence_backed_by_evidence_survives_the_guard(graph, tmp_path):
    """차단만 하고 다 지워 버리면 게이트가 아니라 고장이다. 대조군."""
    svc = AnswerService(
        graph,
        synthesizer=ScriptedSynthesizer(echo_first_evidence),
        audit_path=tmp_path / "q.jsonl",
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    assert answer["answer"]["text"].strip()
    assert not svc.last_guard_report.unsupported_numbers
    assert not svc.last_guard_report.unsupported_terms


def test_citation_pointing_at_unknown_evidence_is_dropped(graph, tmp_path):
    """§8 하드 게이트 2: evidence traceability 100%."""

    def bad_citation(result, evidence):
        return Synthesis(
            text="확인된 자료 기준으로 정리했습니다[9].",
            citations=[{"marker": 9, "evidence_id": "ev_does_not_exist"}],
        )

    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(bad_citation), audit_path=tmp_path / "q.jsonl"
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    ids = {e["evidence_id"] for e in answer["evidence"]}
    for citation in answer["citations"]:
        assert citation["evidence_id"] in ids
    assert svc.last_guard_report.dropped_citations


def test_every_citation_has_a_node_to_land_on(service, demo_account):
    """§10 양방향 연동: 각주를 누르면 강조할 노드가 있어야 한다."""
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    marked = {m for n in answer["subgraph"]["nodes"] for m in (n.get("citation_markers") or [])}
    for citation in answer["citations"]:
        assert citation["marker"] in marked, (
            f"각주 [{citation['marker']}] 에 대응하는 노드가 subgraph 에 없다"
        )


def test_pii_detector_set_matches_the_decided_hard_gate(graph):
    """A-3 결정: 전화·이메일·주민번호는 하드 게이트. 계좌·사업자번호는 오탐이 커서 제외."""
    assert "PII-MOBILE" in HARD_GATE_DETECTORS
    assert "PII-EMAIL" in HARD_GATE_DETECTORS
    assert "PII-RRN" in HARD_GATE_DETECTORS
    assert "PII-BIZ-NO" not in HARD_GATE_DETECTORS


def test_pii_detector_actually_fires():
    """탐지기가 죽어 있으면 위의 차단 테스트가 전부 무의미하다."""
    assert find_pii("연락처는 010-1234-5678 입니다")
    assert find_pii("메일은 someone@example.com 입니다")
    assert find_pii("사무실 번호 02-1234-5678")
    assert not find_pii("2.1.0 버전부터 제공됩니다")


def test_unsupported_token_detector_actually_fires():
    supported = "기능맵 v2.1 에 2.0.0 부터라고 적혀 있다"
    numbers, terms = unsupported_tokens("2.0.0 부터입니다", supported, "")
    assert not numbers and not terms
    numbers, terms = unsupported_tokens("3.4.5 부터입니다", supported, "")
    assert numbers


def test_반올림_절삭_숫자는_부분_문자열로_게이트를_통과하지_못한다():
    """'3.7' ⊂ '3.77', '400' ⊂ '4,000' 같은 부분 문자열 일치는 근거가 아니다.

    반올림·절삭 환각이 게이트를 지나면 값이 틀린 채로 답변에 남는다.
    숫자는 앞뒤가 숫자(또는 소수점 연속)가 아닐 때만 근거로 인정한다."""
    supported = "예상 매출 2.85억, 상담 월 4,000건 규모라고 적혀 있다"
    numbers, _ = unsupported_tokens("격차는 3.7억입니다", supported, "")
    assert numbers == ["3.7"], numbers
    numbers, _ = unsupported_tokens("상담은 월 400건입니다", supported, "")
    assert numbers == ["400"], numbers
    # 온전한 일치는 통과한다
    numbers, _ = unsupported_tokens("월 4,000건이고 2.85억입니다", supported, "")
    assert not numbers, numbers
    # 버전 표기의 접두 절삭도 근거가 아니다
    numbers, _ = unsupported_tokens("2.0 부터입니다", "기능맵에 2.0.0 부터라고 적혀 있다", "")
    assert numbers == ["2.0"], numbers


# ---------------------------------------------------------------------------
# 2-1. 합성 프롬프트가 게이트와 어긋나지 않는가
#
# 게이트가 근거로 인정하는 것은 **발췌와 질문뿐**이다. 프롬프트가 그 밖의 문자열(위치 표기 ·
# evidence_id)을 보여 주면 모델이 그것을 사실로 알고 옮겨 적고, 게이트는 그 문장을 통째로
# 지운다. 실측에서 그 때문에 답변 하나가 통째로 사라졌다(기능 버전 문항).
# ---------------------------------------------------------------------------


class CapturingLLM:
    """프롬프트만 받아 두고 정해진 JSON 을 돌려주는 가짜 LLMService."""

    def __init__(self, payload: dict | None = None):
        self.prompts: list[str] = []
        self._payload = payload if payload is not None else {"text": "확인했습니다."}

    def complete(self, prompt, *, schema=None, tier="S", purpose="", max_tokens=None, timeout=None):
        from llm.types import LLMResult

        self.prompts.append(prompt)
        return LLMResult(
            text=json.dumps(self._payload, ensure_ascii=False),
            parsed=self._payload,
            model="fake",
            cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
            cache_hit=False,
            attempts=1,
        )


class _StubRoute:
    retriever = "Q-E"


class _StubResult:
    """build_prompt 가 읽는 것만 갖춘 최소 결과."""

    question = "비회원 재방문 상담은 언제부터 이어지나?"
    route = _StubRoute()
    synthesis_input: dict = {}
    gaps: list = []
    raw_signals: list = []


#: 위치 표기가 답변에 새어 나갔을 때 무엇이 무너지는지 그대로 재현하는 발췌 2건.
#: `v2.1!F15` 는 시트 이름의 버전이지 그 기능의 도입 버전이 아니다.
PROMPT_EVIDENCE = [
    EvidenceRef(
        evidence_id="ev_dfc582e43098d503",
        source_id="src_feature_map",
        locator="v2.1!F15",
        snippet="비회원 상담 연속성 유지",
        source_type="feature_map",
    ),
    EvidenceRef(
        evidence_id="ev_aa11bb22cc33dd44",
        source_id="src_pain",
        locator="Pain Point 목록!E2",
        snippet="업무 대화가 개인 카톡에 산재해 통제 못 함",
        source_type="feature_map",
    ),
]


def _synthesis_prompt(payload: dict | None = None) -> str:
    from api.synthesis import LLMSynthesizer

    return LLMSynthesizer(CapturingLLM(payload)).build_prompt(_StubResult(), PROMPT_EVIDENCE)


def test_prompt_never_shows_the_model_a_locator_or_an_evidence_id():
    """둘 다 게이트가 근거로 안 쳐 준다. 보여 주면 모델이 옮겨 적고 그 문장이 삭제된다."""
    prompt = _synthesis_prompt()
    for ref in PROMPT_EVIDENCE:
        assert ref.evidence_id not in prompt, "evidence_id 가 프롬프트에 노출됐다"
        assert ref.locator not in prompt, "위치 표기가 프롬프트에 노출됐다"
    # 시트 이름에 붙은 버전 번호가 새어 나가지 않는지 직접 확인한다(GQ-P2 가 이걸로 무너졌다).
    assert "v2.1!" not in prompt
    assert "Pain Point 목록" not in prompt


def test_prompt_numbers_the_excerpts_so_the_model_can_cite_without_copying_ids():
    prompt = _synthesis_prompt()
    assert "[1] (feature_map) 비회원 상담 연속성 유지" in prompt
    assert "[2] (feature_map) 업무 대화가 개인 카톡에" in prompt


def test_marker_is_resolved_back_to_the_real_evidence_id():
    from api.synthesis import resolve_markers

    evidence = [
        _ref("ev_first", "src_a", "첫 발췌"),
        _ref("ev_second", "src_b", "둘째 발췌"),
    ]
    resolved = resolve_markers([{"marker": 2}], evidence)
    assert resolved == [{"marker": 2, "evidence_id": "ev_second"}]


def test_marker_outside_the_list_never_becomes_a_valid_citation():
    """번호를 지어내면 실존하지 않는 id 로 남아 인용 검증에서 걸린다(traceability 100%)."""
    from api.synthesis import resolve_markers

    evidence = [_ref("ev_first", "src_a", "첫 발췌")]
    resolved = resolve_markers([{"marker": 7}], evidence)
    assert resolved[0]["evidence_id"] not in {"ev_first"}


def test_model_supplied_evidence_id_is_kept_as_is():
    """스크립트 합성기·픽스처는 id 를 직접 적는다. 그 경로를 깨뜨리지 않는다."""
    from api.synthesis import resolve_markers

    evidence = [_ref("ev_first", "src_a", "첫 발췌")]
    resolved = resolve_markers([{"marker": 1, "evidence_id": "ev_given"}], evidence)
    assert resolved[0]["evidence_id"] == "ev_given"


#: 묻는 값이 **뒤쪽에 있는** 긴 발췌. 미팅 노트가 실제로 이런 모양이다: 앞머리에 장소·참석자가
#: 오고 정작 묻는 건수는 본문 3번 항목에 있다. 그래프는 발췌를 500자까지 담고 계약도 500자를
#: 허용하므로 이 길이는 실재하는 길이다(실측: 발췌 17,613건 중 6,026건이 300자를 넘는다).
LATE_VALUE_SNIPPET = """*2026.06.29 ○○유통 도입 초기 미팅*
장소: 종로 본사 회의실
참석자: 서비스운영팀 팀장, 운영기획파트 팀원, 고객지원팀 파트리더

*[주요내용]*
1. 현재 상담은 카카오채널을 사용해서 24시간 채팅상담과 전화 상담을 함께 진행하고 있으며, 주간에는 상담원 6명, 야간에는 3명이 교대로 응대하고 있다.
2. 야간 상담을 따로 운영하는 이유는 거래처가 모인 도매시장이 야간에 열려서 그 시간대에 문의가 몰리기 때문이다.
3. 핵심 니즈는 자주 묻는 질문을 봇으로 걸러 상담량이 줄면 남는 인력을 다른 업무에 쓰는 것이고, 카카오채널은 메인 월 4,000건과 도매 월 2,000건이 들어온다.
4. 최근 도입한 시나리오 봇은 한눈에 보기 어려워 불편하다는 의견이 있었다."""


def test_the_model_sees_every_excerpt_character_the_gate_accepts_as_evidence():
    """모델이 보는 발췌가 게이트가 인정하는 발췌보다 짧으면 안 된다.

    게이트(`service._assemble` 의 `support_text`)는 발췌 **전문**을 근거로 인정한다. 그런데
    프롬프트가 앞쪽만 보여 주면, 모델은 뒤쪽에 적힌 값을 본 적이 없어 "확인된 자료가
    없습니다"라고 답한다. 화면은 그 발췌를 전문으로 보여 주므로 사용자에게는 값이 보인다.
    같은 근거를 놓고 답변과 화면이 서로 다른 말을 하게 된다.

    실제로 그렇게 답한 질문이 있다("딜리셔스에서는 상담이 몇 건 정도 들어와?"). 그 발췌의
    `월4,000건` 은 정규화 후 312번째 글자였고 프롬프트는 300자에서 끊었다.
    """
    normalized = " ".join(LATE_VALUE_SNIPPET.split())
    # 픽스처가 절단 지점 뒤에 값을 두지 않으면 이 테스트는 아무것도 검증하지 않는다.
    assert normalized.index("월 4,000건") > 300
    assert len(normalized) <= 500, "계약이 허용하는 발췌 길이를 넘는 픽스처는 근거가 못 된다"

    ref = EvidenceRef(
        evidence_id="ev_late_value",
        source_id="src_slack_meeting",
        locator="slack:C0000000000/1772000000.000000",
        snippet=LATE_VALUE_SNIPPET,
        source_type="slack_thread",
    )
    from api.synthesis import LLMSynthesizer

    prompt = LLMSynthesizer(CapturingLLM()).build_prompt(_StubResult(), [ref])
    assert "월 4,000건" in prompt, "게이트가 인정하는 값이 프롬프트에서 잘려 나갔다"
    assert "월 2,000건" in prompt


def test_the_model_sees_every_reference_signal_the_gate_accepts():
    """참고 신호도 게이트는 전부 근거로 인정한다. 프롬프트가 앞 몇 건만 보여 주면 같은 결함이다.

    실측: 골든 20문항 중 **13문항**이 참고 신호 12건을 넘었다(최대 27건). 13번째부터는
    게이트가 근거로 치는데 모델은 본 적이 없는 상태였다.
    """
    from api.synthesis import LLMSynthesizer
    from retrieval.types import RawSignal

    signals = [
        RawSignal(
            evidence_id=f"ev_raw_{i:02d}",
            source_id="src_slack_meeting",
            locator=f"slack:C0000000000/17720000{i:02d}.000000",
            snippet=f"참고 신호 {i:02d} 번째 발췌입니다. 고유한 값 {700 + i}건이 적혀 있습니다.",
            match_terms=("상담",),
            in_graph=False,
        )
        for i in range(1, 21)
    ]

    class _WithSignals(_StubResult):
        raw_signals = signals

    prompt = LLMSynthesizer(CapturingLLM()).build_prompt(
        _WithSignals(), PROMPT_EVIDENCE, signals
    )
    missing = [s.evidence_id for s in signals if f"{700 + int(s.evidence_id[-2:])}건" not in prompt]
    assert not missing, f"게이트가 인정하는 참고 신호가 프롬프트에서 빠졌다: {missing}"


def test_aggregates_are_trimmed_by_whole_rows_not_cut_mid_string():
    """집계는 문자열 중간에서 자르면 안 된다. 닫히지 않은 조각은 모델이 읽을 수 없다.

    예전에는 완성된 JSON 을 4,000자에서 그냥 잘랐다. 실측(골든·추가 22문항)에서 그렇게 사라진
    글자가 36,298자, 한 문항 최대 7,965자였다. 뒤쪽 컬렉션은 보이지도 않는데 없다는 표시도
    없었다.
    """
    from api.synthesis import AGGREGATES_BUDGET_CHARS, _render_aggregates

    aggregates = {
        "collection_order": ["need", "business_domain"],
        "need": [
            {"name": f"요구 {i:03d}", "note": "가" * 180, "accounts": ["카파전선", "마바손해보험"]}
            for i in range(120)
        ],
        "business_domain": [{"name": "자동차금융", "note": "뒤쪽 컬렉션"}],
    }
    rendered = _render_aggregates(aggregates)
    body = rendered.split("\n(자리가 없어")[0]
    parsed = json.loads(body)  # 잘려서 깨지면 여기서 실패한다

    assert len(body) <= AGGREGATES_BUDGET_CHARS + 600, "예산을 크게 넘겼다"
    assert "(자리가 없어 넣지 못한 집계:" in rendered, "무엇을 뺐는지 밝히지 않았다"
    assert len(parsed["need"]) < 120, "이 픽스처는 예산을 넘겨야 검증에 뜻이 있다"
    assert parsed["need"][0]["name"] == "요구 000", "행 순서를 다시 세우면 A5 의 순위를 덮어쓴다"


def test_gap_verdicts_reach_the_model_in_korean_not_as_english_enums():
    """영어 enum 을 보여 주면 모델이 그대로 옮기고, 그 문장이 무근거 고유명사로 삭제된다."""
    from api.synthesis import _render_gaps
    from retrieval.types import GapFinding, GapVerdict

    rendered = _render_gaps(
        [
            GapFinding(
                subject="퇴직연금 Guest 통제",
                verdict=GapVerdict.POSSIBLE,
                reason="필요를 말하는 근거는 있으나 대응하는 Capability 근거를 찾지 못했다.",
            )
        ]
    )
    assert "POSSIBLE" not in rendered
    assert "Capability" not in rendered
    assert "가능성 있는 공백" in rendered
    assert "역량" in rendered


def test_list_questions_get_more_room_than_single_fact_questions():
    """항목을 빠짐없이 나열해야 하는 경로에 한 문장 상한을 똑같이 씌우면 항목이 잘린다."""
    from api.synthesis import LLMSynthesizer

    synth = LLMSynthesizer(CapturingLLM())
    assert synth._sentence_budget("Q-S") > synth._sentence_budget("Q-E")
    assert synth._sentence_budget("Q-M") > synth._sentence_budget("Q-E")


# ---------------------------------------------------------------------------
# 2-2. 각주가 조용히 사라지지 않는다 (§8 하드 게이트 2 · PRD 1A AC-4)
#
# 모델이 citations 에 번호를 적고도 본문에 [n] 을 안 다는 일이 있다. 그때 예전 코드는
# "본문에 없는 각주는 버린다" 규칙에 걸려 근거를 전부 버렸고, 아무 기록도 남지 않았다.
# 실측(직전 라운드 07:57 저장본 `data/cache/gate_1a_answers.json`): GQ-P2·GQ-A1 이
# citations 0, guard 로그도 전부 0. 캐시된 모델 출력에는 citations 가 있었다.
#
# 게이트가 지운 문장의 각주는 되살리지 않는다. 되살리면 게이트를 무력화한다.
# ---------------------------------------------------------------------------


def _first_sentence_no_marker(result, evidence):
    """근거를 그대로 옮기지만 **본문에 각주 번호를 달지 않는** 합성기.

    모델이 실제로 이렇게 답한 적이 있다(위 주석의 저장본). citations 에는 번호가 있다.
    """
    first = evidence[0]
    quoted = " ".join(first.snippet.split())[:200]
    return Synthesis(
        text=f"{quoted}.",
        citations=[{"marker": 1, "evidence_id": first.evidence_id}],
    )


def test_citation_survives_when_the_model_writes_no_inline_marker(corpus, graph, tmp_path):
    """근거를 옮겨 적은 문장이 있으면 각주는 그 문장에 붙어야 한다. 버리면 3클릭이 끊긴다."""
    svc = AnswerService(
        graph,
        synthesizer=ScriptedSynthesizer(_first_sentence_no_marker),
        audit_path=tmp_path / "q.jsonl",
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    assert answer["citations"], "모델이 각주 번호를 본문에 안 달면 근거가 통째로 사라진다"
    ids = {e["evidence_id"] for e in answer["evidence"]}
    for citation in answer["citations"]:
        assert citation["evidence_id"] in ids
        assert f"[{citation['marker']}]" in answer["answer"]["text"], (
            "각주가 본문 어디에도 없으면 화면에서 누를 수가 없다"
        )


def test_citation_that_cannot_be_anchored_is_reported_instead_of_vanishing(corpus, graph, tmp_path):
    """붙일 문장이 없으면 버린다. 다만 조용히 버리지 않는다 — 기록이 남아야 원인을 찾는다."""

    def bare_answer(result, evidence):
        return Synthesis(
            text="그렇습니다.",
            citations=[{"marker": 1, "evidence_id": evidence[0].evidence_id}],
        )

    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(bare_answer), audit_path=tmp_path / "q.jsonl"
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    assert answer["citations"] == []
    assert svc.last_guard_report.unanchored_citations, "버린 각주가 로그에 남지 않았다"


def test_citation_whose_sentence_the_guard_removed_stays_dropped(corpus, graph, tmp_path):
    """게이트가 지운 문장의 각주는 되살리지 않는다(§8 하드 게이트 3 약화 금지)."""

    def half_fabricated(result, evidence):
        first = evidence[0]
        quoted = " ".join(first.snippet.split())[:200]
        return Synthesis(
            text=f"{quoted}.[1]\n지난해 매출은 987654321원이었습니다.[2]",
            citations=[
                {"marker": 1, "evidence_id": first.evidence_id},
                {"marker": 2, "evidence_id": evidence[min(1, len(evidence) - 1)].evidence_id},
            ],
        )

    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(half_fabricated), audit_path=tmp_path / "q.jsonl"
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    assert "987654321" not in answer["answer"]["text"]
    assert 2 not in {c["marker"] for c in answer["citations"]}, (
        "게이트가 지운 문장의 각주가 다시 붙었다 — 게이트가 무력화된다"
    )
    assert svc.last_guard_report.unsupported_numbers


def test_anchoring_never_touches_a_marker_the_model_had_already_written():
    """게이트가 지운 문장의 각주는 되붙이기 대상이 아니다. 그래프 없이 직접 확인한다."""
    from api.anchoring import anchor_citations

    text, anchored, unanchored = anchor_citations(
        "비회원 상담 연속성 유지를 지원합니다.",
        [
            {"marker": 1, "evidence_id": "ev_a"},
            {"marker": 2, "evidence_id": "ev_b"},
        ],
        {"ev_a": "비회원 상담 연속성 유지", "ev_b": "비회원 상담 연속성 유지"},
        already={2},  # 모델이 본문에 달았다가 게이트가 그 문장을 지운 각주
    )
    assert "[1]" in text
    assert "[2]" not in text, "게이트가 지운 문장의 각주가 되살아났다"
    assert [c["marker"] for c in anchored] == [1]
    assert unanchored == []


# ---------------------------------------------------------------------------
# 2-3. 값은 그 값이 적힌 행의 대상에만 붙인다 (PRD 1A AC-5)
#
# 발췌 전체를 한 덩어리로 보면 "근거 있는 값을 틀린 대상에 붙이는" 오류가 통과한다.
# 아래 두 발췌는 기능맵 원본 `오로라웍스 기능맵 v2.1.xlsx` 시트 v2.1 의 172행·174행이다.
#   · 172행 E=대화하기 팝업창 지원 · I~L(1.0.0~2.1.0) 전부 'O' → 1.0.0부터
#   · 174행 E=대화형 브라우저 팝업 · I·J·K='.' · L='O'        → 2.1.0부터
# 두 기능은 같은 그룹(D=대화하기 팝업 제공)에 있어 발췌 문장이 서로 닮았다.
# ---------------------------------------------------------------------------

FEATURE_ROW_1_0_0 = (
    "PCWorkspace (사용자 화면) > 대화 > 대화하기 팝업 제공 | 대화하기 팝업창 지원: "
    "현재 대화중인 상담 대화 화면을 별도의 팝업창으로 지원하며 해당 팝업창을 통해 대화를 "
    "진행할 수 있습니다. | 제공 버전: 1.0.0, 1.1.0, 2.0.0, 2.1.0 (1.0.0부터)"
)
FEATURE_ROW_2_1_0 = (
    "PCWorkspace (사용자 화면) > 대화 > 대화하기 팝업 제공 | 대화형 브라우저 팝업: "
    "진행 중인 대화를 브라우저 팝업 형태의 미니창으로 띄워 별도 창에서 이어서 대화할 수 "
    "있습니다. | 제공 버전: 2.1.0 (2.1.0부터)"
)
FEATURE_ROWS = [FEATURE_ROW_1_0_0, FEATURE_ROW_2_1_0]
VERSION_QUESTION = "진행 중인 대화를 별도 팝업 창으로 띄우는 기능은 어느 버전부터 제공되나?"


def test_version_of_another_row_attributed_to_the_asked_feature_is_removed():
    """`대화형 브라우저 팝업`은 2.1.0 행의 이름이다. 1.0.0 을 붙이면 그 문장은 나갈 수 없다."""
    from api.guards import GuardReport, scrub_prose

    report = GuardReport()
    kept = scrub_prose(
        "대화형 브라우저 팝업은 1.0.0부터 제공됩니다.",
        "\n".join(FEATURE_ROWS),
        VERSION_QUESTION,
        report,
        location="answer.text",
        snippets=FEATURE_ROWS,
    )
    assert kept == "", "다른 행의 버전을 그 기능에 붙인 문장이 그대로 나갔다"
    assert report.misattributed_values, "무엇이 왜 지워졌는지 기록이 없다"


def test_version_paired_with_its_own_row_survives():
    """차단만 하면 게이트가 아니라 고장이다. 같은 행 안의 짝은 그대로 나가야 한다."""
    from api.guards import GuardReport, scrub_prose

    report = GuardReport()
    for sentence in (
        "대화형 브라우저 팝업은 2.1.0부터 제공됩니다.",
        "대화하기 팝업창 지원은 1.0.0부터 제공됩니다.",
    ):
        kept = scrub_prose(
            sentence,
            "\n".join(FEATURE_ROWS),
            VERSION_QUESTION,
            report,
            location="answer.text",
            snippets=FEATURE_ROWS,
        )
        assert kept.strip() == sentence, f"같은 행 안의 짝인데 지워졌다: {sentence}"
    assert not report.misattributed_values


def test_prompt_makes_the_model_pair_a_name_with_the_version_in_the_same_excerpt():
    """짝을 어디서 가져와야 하는지 프롬프트가 말해 주지 않으면 모델이 행을 섞는다."""
    prompt = _synthesis_prompt()
    assert "같은 번호의 발췌 안에서만" in prompt, "한 발췌 안에서만 짝지으라는 규칙이 없다"
    assert "다른 기능의 버전" in prompt, "다른 기능의 버전을 덧붙이지 말라는 규칙이 없다"


# ---------------------------------------------------------------------------
# 3-1. 모순 측정 (1A 는 측정만)
# ---------------------------------------------------------------------------


def _ref(eid: str, source_id: str, snippet: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=eid,
        source_id=source_id,
        locator=f"{source_id}#1",
        snippet=snippet,
        source_type="proposal",
    )


CONFLICT_QUESTION = "개인 메신저를 업무에 쓴 미국 금융권에 부과된 벌금 규모는 얼마인가?"
CONFLICT_TERMS = ["미국", "금융권", "벌금"]


def test_two_sources_with_ten_fold_different_amounts_are_reported_side_by_side():
    """한쪽으로 뭉개지 않는다. 자릿수가 다른 값이 두 자료에 있으면 둘 다 내놓는다."""
    disputes = contradiction.detect(
        CONFLICT_QUESTION,
        [
            _ref("e1", "src_a", "미국 금융권 부과된 벌금 180M 달러 - 한화 약 2,564억 원"),
            _ref("e2", "src_b", "미국 금융권에 18억 달러 벌금 부과"),
        ],
        CONFLICT_TERMS,
    )
    assert len(disputes) == 1
    statements = " ".join(s["statement"] for s in disputes[0]["sides"])
    assert "180M 달러" in statements and "18억 달러" in statements
    for side in disputes[0]["sides"]:
        assert side["evidence_ids"], "근거 없는 dispute 항목을 만들면 안 된다"


def test_same_source_repeating_itself_is_not_a_contradiction():
    """대조군. 같은 자료가 두 번 나온 것은 모순이 아니다."""
    disputes = contradiction.detect(
        CONFLICT_QUESTION,
        [
            _ref("e1", "src_a", "미국 금융권 부과된 벌금 180M 달러"),
            _ref("e2", "src_a", "미국 금융권 벌금 18억 달러"),
        ],
        CONFLICT_TERMS,
    )
    assert disputes == []


def test_similar_amounts_are_not_a_contradiction():
    """대조군. 값이 비슷하면 모순이라고 외치지 않는다."""
    disputes = contradiction.detect(
        CONFLICT_QUESTION,
        [
            _ref("e1", "src_a", "미국 금융권 벌금 180M 달러"),
            _ref("e2", "src_b", "미국 금융권 벌금 190M 달러"),
        ],
        CONFLICT_TERMS,
    )
    assert disputes == []


def test_a_question_that_does_not_ask_for_a_magnitude_never_reports_amounts():
    disputes = contradiction.detect(
        "미국 금융권 규제 동향은 어떤가?",
        [
            _ref("e1", "src_a", "미국 금융권 벌금 180M 달러"),
            _ref("e2", "src_b", "미국 금융권 벌금 18억 달러"),
        ],
        CONFLICT_TERMS,
    )
    assert disputes == []


def test_contradiction_lowers_the_evidence_strength_band(graph, tmp_path):
    """모순이 감지되면 밴드가 내려가야 한다. 그러지 않으면 측정이 표시에 반영되지 않는다."""
    facts = [
        strength.EvidenceFact("e1", "src_a", "release_spec"),
        strength.EvidenceFact("e2", "src_b", "release_spec"),
    ]
    clean = strength.assess(facts, claim_domain="product_behavior", contradiction="none")
    conflicted = strength.assess(
        facts, claim_domain="product_behavior", contradiction="detected"
    )
    assert clean["band"] == "HIGH"
    assert conflicted["band"] == "LOW"


# ---------------------------------------------------------------------------
# 4. Evidence Strength
# ---------------------------------------------------------------------------


def test_evidence_strength_basis_is_complete(service, demo_account):
    basis = service.answer(REPRESENTATIVE[0], account=demo_account)["evidence_strength"]["basis"]
    for key in ("independent_evidence", "highest_authority", "contradiction", "recency"):
        assert key in basis
    assert basis["contradiction"] in {"none", "detected"}
    assert basis["recency"] in {"current", "aging", "stale", "unknown"}


def test_independent_evidence_counts_sources_not_excerpts(service, demo_account):
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    sources = {e["source_id"] for e in answer["evidence"]}
    assert answer["evidence_strength"]["basis"]["independent_evidence"] == len(sources)


def test_band_drops_when_there_is_nothing_to_stand_on(graph, tmp_path):
    """근거가 하나도 없는데 HIGH 가 나오면 밴드가 장식이다."""
    svc = AnswerService(
        graph,
        synthesizer=ScriptedSynthesizer(lambda r, e: Synthesis(text="확인된 자료가 없습니다.")),
        audit_path=tmp_path / "q.jsonl",
    )
    answer = svc.answer("짜맞춤파랑고래공정 도입 고객사는 어디인가?", account=ACCOUNTS["demo"])
    if not answer["evidence"]:
        assert answer["evidence_strength"]["band"] == "LOW"


# ---------------------------------------------------------------------------
# 5. gaps · unknowns · 추가 원문 근거
# ---------------------------------------------------------------------------


def test_gap_verdicts_pass_through_unchanged(service, demo_account):
    """A5 가 판정한 3값을 A6 가 바꾸지 않는다."""
    question = "마바손해보험이 요청한 모바일 PDF 다운로드는 제공되나?"
    answer = service.answer(question, account=demo_account)
    verdicts = {g["verdict"] for g in answer["gaps"]}
    assert verdicts <= {"CONFIRMED", "POSSIBLE", "UNKNOWN"}
    for gap in answer["gaps"]:
        if gap["verdict"] == "CONFIRMED":
            assert gap["basis"].get("explicit_absence_evidence_ids"), (
                "CONFIRMED 인데 명시적 부재 근거가 비어 있다"
            )


def test_unknowns_are_stated_when_the_answer_rests_on_little(service, demo_account):
    answer = service.answer("짜맞춤파랑고래공정 도입 고객사는 어디인가?", account=demo_account)
    assert answer["unknowns"], "근거가 없으면 '확인된 자료 없음'을 말해야 한다"


def test_raw_signals_are_a_separate_section(service, demo_account):
    answer = service.answer(
        "하늘IT와 구독 사업을 추진하려면 반드시 해결해야 할 기술 전제는 무엇인가?",
        account=demo_account,
    )
    assert "raw_signals" in answer
    assert answer["notices"]["raw_signal_count"] == len(answer["raw_signals"])


# ---------------------------------------------------------------------------
# 6. subgraph 절단
# ---------------------------------------------------------------------------


def test_subgraph_respects_contract_limits(service, demo_account):
    for question in REPRESENTATIVE:
        sub = service.answer(question, account=demo_account)["subgraph"]
        assert len(sub["nodes"]) <= 50
        assert len(sub["edges"]) <= 100
        ids = {n["id"] for n in sub["nodes"]}
        for edge in sub["edges"]:
            assert edge["from"] in ids and edge["to"] in ids


def test_subgraph_keeps_focal_before_supporting(service, demo_account):
    sub = service.answer(REPRESENTATIVE[0], account=demo_account)["subgraph"]
    ranks = [n["rank"] for n in sub["nodes"]]
    order = {"focal": 0, "cited": 1, "supporting": 2}
    assert ranks == sorted(ranks, key=lambda r: order[r])


# ---------------------------------------------------------------------------
# 7. expand
# ---------------------------------------------------------------------------


def test_expand_returns_neighbours_within_the_cap(corpus, service, demo_account):
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    node_id = answer["subgraph"]["nodes"][0]["id"]
    payload = service.expand(node_id, account=demo_account)
    assert len(payload["nodes"]) <= service.EXPAND_NODE_CAP
    ids = {n["id"] for n in payload["nodes"]} | {node_id}
    for edge in payload["edges"]:
        assert edge["from"] in ids and edge["to"] in ids


def test_expand_honours_the_cumulative_budget(corpus, service, demo_account):
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    node_id = answer["subgraph"]["nodes"][0]["id"]
    payload = service.expand(node_id, account=demo_account, already=148)
    assert len(payload["nodes"]) <= 2


def test_expand_marks_added_nodes_as_expanded(corpus, service, demo_account):
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    node_id = answer["subgraph"]["nodes"][0]["id"]
    payload = service.expand(node_id, account=demo_account)
    for node in payload["nodes"]:
        assert node.get("expanded") is True


def test_expand_rejects_more_than_one_hop(service, demo_account):
    with pytest.raises(ValueError):
        service.expand("anything", hops=3, account=demo_account)


# ---------------------------------------------------------------------------
# 8. 로그인 · audit
# ---------------------------------------------------------------------------


def test_login_accepts_a_known_account():
    account = authenticate("demo", ACCOUNTS["demo"].demo_password)
    assert account is not None and account.username == "demo"


def test_login_rejects_a_wrong_password():
    assert authenticate("demo", "틀린비밀번호") is None
    assert authenticate("없는사용자", "아무거나") is None


def test_query_audit_records_who_asked_what(graph, tmp_path):
    path = tmp_path / "query.jsonl"
    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(echo_first_evidence), audit_path=path
    )
    answer = svc.answer(REPRESENTATIVE[0], account=ACCOUNTS["demo"])
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "query"
    assert row["username"] == "demo"
    assert row["question"] == REPRESENTATIVE[0]
    assert row["route"] == answer["route"]["retriever"]
    assert row["citation_count"] == len(answer["citations"])
    assert row["query_id"] == answer["query_id"]
    assert row["ts"]


def test_audit_log_does_not_store_pii(graph, tmp_path):
    """로그는 오래 남는다. 질문에 전화번호가 섞여 들어와도 그대로 적지 않는다.

    검사 대상은 사람이 적은 텍스트 필드다. 내부 식별자(query_id)는 난수 hex 라 우연히
    주민번호 정규식에 걸릴 수 있고 개인을 식별하지도 않으므로 대상에서 뺀다.
    """
    path = tmp_path / "query.jsonl"
    svc = AnswerService(
        graph, synthesizer=ScriptedSynthesizer(echo_first_evidence), audit_path=path
    )
    svc.answer("담당자 연락처 010-1234-5678 로 연락했나?", account=ACCOUNTS["demo"])

    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    text_fields = "\n".join(
        str(value)
        for key, value in row.items()
        if key not in {"query_id"} and isinstance(value, str)
    )
    assert find_pii(text_fields) == []
    assert "010-1234-5678" not in text_fields
    # 번호만 덮고 질문은 남긴다. 질문을 통째로 지우면 무엇을 물었는지 감사할 수 없다.
    assert "담당자 연락처" in row["question"] and "연락했나" in row["question"]


# ---------------------------------------------------------------------------
# 9. 라우팅 정보 보존
# ---------------------------------------------------------------------------


def test_route_is_reported_as_the_retrieval_layer_decided(service, demo_account):
    answer = service.answer("여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?", account=demo_account)
    assert answer["route"]["retriever"] == "Q-M"
    assert answer["route"]["matched_rule"]


def test_answer_stamps_policy_version_and_time(service, demo_account):
    answer = service.answer(REPRESENTATIVE[0], account=demo_account)
    assert answer["policy_version"]
    assert answer["answered_at"].endswith("Z")
    assert answer["query_id"]


# ---------------------------------------------------------------------------
# 10. 근거 도달 회귀 — 그래프에 있는 발췌가 답변의 evidence 까지 오는가
# ---------------------------------------------------------------------------
#
# 실패 21건 중 19건이 "근거가 그래프에 실재하는데 answer.evidence 까지 못 온다"였다.
# 후보 풀까지 오는지는 tests/test_retrieval.py 가 보고, 여기서는 **마지막 24건 선별을
# 통과하는지**를 본다. 두 자리 중 어디서 떨어졌는지 구분되어야 원인을 짚을 수 있다.
#
# 기대 문구는 eval/golden.yaml 의 `source_quote` 를 그대로 읽는다. 구현보다 먼저 쓰였고
# 원본 대조기(test_golden_quotes_exist_in_sources)가 원문에 실재함을 이미 검사한 값이다.

EVIDENCE_MUST_CARRY = ("GQ-D1", "GQ-D3", "GQ-D6", "GQ-B1", "GQ-G1")

#: 아직 답변 근거까지 오지 못하는 발췌와, 실측으로 확인한 원인.
#: **통과하는 것만 남겨 초록불을 만들지 않으려고** 여기에 남긴다. 고쳐지면 줄을 지운다.
NOT_YET_REACHING: dict[tuple[str, str], str] = {}


def _expected_excerpts(question: dict) -> list[tuple[str, str]]:
    return [
        (item["locator"], item["source_quote"])
        for item in question.get("must_include") or []
        if item.get("source_quote")
    ]


@pytest.fixture(scope="module")
def golden():
    from eval.golden_loader import load_golden

    return load_golden()


@pytest.fixture(scope="module")
def corpus():
    """실코퍼스 적재를 전제하는 테스트의 게이트(tests/test_retrieval.py 와 동일)."""
    if os.environ.get("WM_CORPUS_TESTS") != "1":
        pytest.skip("WM_CORPUS_TESTS=1 이 아니면 코퍼스 의존 테스트를 건너뛴다")


@pytest.mark.parametrize("qid", EVIDENCE_MUST_CARRY)
def test_expected_excerpt_is_carried_into_the_answer_evidence(corpus, 
    service, demo_account, golden, qid
):
    from eval.golden_loader import contains

    question = golden.by_id(qid)
    answer = service.answer(question["question"], account=demo_account)
    carried = "\n".join(e.get("snippet") or "" for e in answer["evidence"])

    missing = [
        f"{locator}: {quote[:40]!r}"
        for locator, quote in _expected_excerpts(question)
        if (qid, locator) not in NOT_YET_REACHING and not contains(carried, quote)
    ]
    assert not missing, f"{qid}: 그래프에 있는 발췌가 답변 근거에 안 실렸다 — {missing}"


def test_the_not_yet_list_is_not_hiding_something_that_already_works(
    service, demo_account, golden
):
    """차단 케이스. 위 목록이 실제로는 통과하는 것을 숨기고 있으면 이 테스트가 깨진다.

    면제 목록은 시간이 지나면 잊힌다. 고쳐졌는데도 남아 있으면 다음 사람이 "아직 안 된다"고
    믿게 되므로, 고쳐진 줄은 여기서 잡아 지우게 만든다.
    """
    from eval.golden_loader import contains

    stale: list[str] = []
    for qid in EVIDENCE_MUST_CARRY:
        question = golden.by_id(qid)
        exempt = {loc for (q, loc) in NOT_YET_REACHING if q == qid}
        if not exempt:
            continue
        answer = service.answer(question["question"], account=demo_account)
        carried = "\n".join(e.get("snippet") or "" for e in answer["evidence"])
        stale += [
            f"{qid} {locator}"
            for locator, quote in _expected_excerpts(question)
            if locator in exempt and contains(carried, quote)
        ]
    assert not stale, f"이제 도달하는데 면제 목록에 남아 있다. NOT_YET_REACHING 에서 지울 것 — {stale}"


def test_a_single_source_never_takes_the_whole_evidence_list(service, demo_account, golden):
    """차단 케이스: 자료 하나가 24칸을 독식하면 안 된다.

    전략 질문은 점수가 거의 평평해서, 곳을 나누지 않으면 한 자료·한 집계 행이 목록을 통째로
    가져간다. 실측에서 그렇게 잘려 BD 20행 중 두 행만 남았다.
    """
    for qid in ("GQ-D2", "GQ-D5"):
        answer = service.answer(golden.by_id(qid)["question"], account=demo_account)
        evidence = answer["evidence"]
        if len(evidence) < 10:
            continue
        counts: dict[str, int] = {}
        for item in evidence:
            counts[item["source_id"]] = counts.get(item["source_id"], 0) + 1
        worst = max(counts.values())
        assert worst <= len(evidence) * 0.75, (
            f"{qid}: 자료 하나가 근거 {worst}/{len(evidence)} 건을 가져갔다 — {counts}"
        )


# ---------------------------------------------------------------------------
# 11. 근거 24칸 배분 — 점수 · 그룹 · 자료 세 힘의 우선순위
# ---------------------------------------------------------------------------
#
# 기대값의 근거는 §6 A6 의 요구 셋이다. 구현 출력에서 복사한 값은 없다.
#   ① 질문에 더 맞는 근거(점수 높은 것)가 덜 맞는 근거에 밀리지 않는다
#   ② 한 자료·한 집계 행이 목록을 독식하지 않는다 (answer.schema 상한 24 안에서)
#   ③ 같은 후보를 주면 같은 24건이 나온다 — 조회 순서가 결과를 바꾸지 않는다
#
# ③ 이 계약인 이유: 후보를 담아 오는 dict 의 순서는 Neo4j 반환 순서라 실행마다 다를 수
# 있다. 그것이 선별에 흘러가면 답변 근거가 실행마다 달라지고, 회귀를 판정할 수 없다.


def _seat(eid: str, source: str, locator: str = "s!A1", snippet: str = "본문") -> EvidenceRef:
    return EvidenceRef(evidence_id=eid, source_id=source, locator=locator, snippet=snippet)


def _pick(refs, groups, limit=6, **kw):
    """점수를 직접 주고 배분만 본다(낱말 겹침 계산을 끼우지 않는다)."""
    from api import selection

    scores = kw.pop("scores")

    class FixedScorer:
        def score(self, ref):
            return scores[ref.evidence_id]

    original = selection.build_scorer
    selection.build_scorer = lambda *a, **k: FixedScorer()
    try:
        return selection.select_evidence(
            refs, terms=[], focal_names=[], aggregate_ids=set(), groups=groups, limit=limit, **kw
        )
    finally:
        selection.build_scorer = original


def test_a_clearly_better_excerpt_is_not_pushed_out_by_group_decay():
    """차단 케이스. 같은 집계 행의 세 번째라는 이유로 점수 10 이 점수 6 뒤로 가면 안 된다.

    실측(2026-08-13 GQ-D1): 기대 발췌 `2.85억` 은 원점수 9.99 로 후보 144건 중 5위인데
    `account_deal#0` 의 3번째라 감쇠로 5.62 가 되어, 5.99 짜리 14건에 밀려 잘렸다.
    감쇠는 비슷한 점수끼리의 순서를 정하는 장치이지 점수를 뒤집는 장치가 아니다.
    """
    refs = [_seat("hi1", "src_a"), _seat("hi2", "src_a"), _seat("hi3", "src_a")]
    refs += [_seat(f"lo{i}", f"src_{i}") for i in range(6)]
    scores = {"hi1": 10.0, "hi2": 10.0, "hi3": 10.0}
    scores.update({f"lo{i}": 6.0 for i in range(6)})
    groups = {"hi1": "deal#0", "hi2": "deal#0", "hi3": "deal#0"}
    groups.update({f"lo{i}": f"obs#{i}" for i in range(6)})

    chosen = {r.evidence_id for r in _pick(refs, groups, limit=6, scores=scores)}
    assert {"hi1", "hi2", "hi3"} <= chosen, (
        f"점수 10 짜리 셋이 점수 6 짜리에 밀렸다 — {sorted(chosen)}"
    )


def test_one_source_does_not_take_consecutive_seats_when_scores_tie():
    """동률 무리에서는 아직 자리를 못 받은 자료가 먼저다.

    지금은 이 자리가 후보 dict 의 삽입 순서(= Neo4j 반환 순서)로 갈린다. 그래서 자료
    다양성이 우연에 걸려 있다. 다양성은 우연이 아니라 배분 규칙이어야 한다.
    """
    refs = [_seat(f"a{i}", "src_big") for i in range(5)] + [_seat("b0", "src_small")]
    scores = {r.evidence_id: 5.0 for r in refs}
    groups = {r.evidence_id: f"obs#{i}" for i, r in enumerate(refs)}

    chosen = [r.source_id for r in _pick(refs, groups, limit=3, scores=scores)]
    assert "src_small" in chosen, f"동률인데 큰 자료가 세 칸을 다 먹었다 — {chosen}"


def test_selection_does_not_depend_on_the_candidate_order():
    """③ 계약. 후보 순서를 뒤집어도 같은 24건이 나와야 한다."""
    refs = [_seat(f"e{i}", f"src_{i % 4}", locator=f"s!A{i}") for i in range(40)]
    # 점수를 일부러 계단으로 만들어 동률 구간이 넓게 생기게 한다
    scores = {f"e{i}": float(5 - i // 12) for i in range(40)}
    groups = {f"e{i}": f"obs#{i % 7}" for i in range(40)}

    forward = [r.evidence_id for r in _pick(list(refs), groups, limit=24, scores=scores)]
    backward = [r.evidence_id for r in _pick(list(reversed(refs)), groups, limit=24, scores=scores)]
    assert forward == backward, "후보 순서가 바뀌자 선별 결과가 달라졌다"


def test_evidence_fetch_returns_in_the_requested_order(corpus, graph):
    """조회 계층도 결정적이어야 한다 — 요청한 id 순으로 돌려준다.

    선별이 순서에 좌우되지 않게 고쳤으므로 여기를 고정해도 배분이 흔들리지 않는다.
    되돌린 이력은 `retrieval/store.py` 주석에 있다.
    """
    from retrieval.store import fetch_evidence

    with graph.session() as session:
        ids = [
            row["evidence_id"]
            for row in session.run("MATCH (e:Evidence) RETURN e.evidence_id AS evidence_id LIMIT 12")
        ]
        assert len(ids) == 12
        shuffled = ids[::-1]
        got = list(fetch_evidence(session, shuffled).keys())
    assert got == shuffled, "요청 순서와 다른 순서로 돌아왔다"


# ---------------------------------------------------------------------------
# 12. 자료의 표기가 곧 그 자료 발췌의 신호다
# ---------------------------------------------------------------------------
#
# 실측(2026-08-13): 소미생명 제안서는 발췌 32건 어디에도 회사 이름이 없다. 누구에게 낸
# 제안인지가 파일 이름에만 적혀 있어서, 그 회사를 물으면 focal 겹침이 0 이었다.
# 기대값의 근거는 §6 A6 의 "질문의 중심 엔티티 이름이 발췌 안에 있는가" 를 자료 표기까지
# 넓힌 것이다. 구현 출력에서 복사한 값은 없다.


def _labelled(eid: str, label: str, snippet: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=eid,
        source_id=f"src_{eid}",
        locator="slide 1",
        snippet=snippet,
        source_label=label,
    )


def test_a_source_named_after_the_entity_scores_above_an_unrelated_one():
    """본문이 똑같아도 자료 표기가 그 회사 것이면 점수가 앞선다."""
    mine = _labelled("a", "[소미생명] 보험설계지원 솔루션 소개서.pptx", "설계매니저 KPI 관리")
    other = _labelled("b", "제품 브로슈어.pdf", "설계매니저 KPI 관리")
    scorer = selection.build_scorer(
        [mine, other], terms=[], focal_names=["소미생명"], aggregate_ids=set()
    )
    assert scorer.score(mine) > scorer.score(other)


def test_a_source_name_match_does_not_outrank_an_excerpt_that_names_the_entity():
    """차단 케이스: 자료 표기 겹침이 본문 겹침을 이기면 정작 그 회사를 말한 발췌가 밀린다."""
    by_label = _labelled("a", "[소미생명] 소개서.pptx", "설계매니저 KPI 관리")
    by_body = _labelled("b", "영업활동일지.xlsx", "소미생명 계약 조건을 협의했다")
    scorer = selection.build_scorer(
        [by_label, by_body], terms=[], focal_names=["소미생명"], aggregate_ids=set()
    )
    assert scorer.score(by_body) > scorer.score(by_label)


def test_source_name_match_requires_a_word_boundary_for_ascii_names():
    """차단 케이스: 파일 이름은 구분자가 적어 짧은 영문 이름이 우연히 박히기 쉽다."""
    ref = _labelled("a", "AGENT-사용법.md", "본문")
    scorer = selection.build_scorer([ref], terms=[], focal_names=["AG"], aggregate_ids=set())
    assert scorer.score(ref) == 0.0
