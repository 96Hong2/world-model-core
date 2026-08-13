"""R8 회귀 방지 — 각주 추적(AC-4)과 중립 도메인 폴백.

Lead 실검증 D-3·D-4 가 잡은 두 결함을 다시 못 생기게 막는다. 큰 스위트를 새로 짜지 않고
그 결함 하나씩만 겨눈다.

    D-3  값이 참고 신호(raw_signals)에만 있는 문장은 붙일 각주 번호가 없어 추적이 끊겼다.
    D-4  질문 유형 분류가 실패하면 product_behavior 로 떨어져 그 도메인 정본이 T1 을 받았다.
"""

from __future__ import annotations

from api.anchoring import MarkerAllocator, anchor_orphan_tokens, orphan_token_sentences
from api.authority import classify_claim_domain, tier_for
from api.guards import unsupported_tokens
from retrieval.types import EvidenceRef

QUESTION = "가나손해보험에게 어떤 Sales Point를 잡는 것이 좋은가?"


def _ref(index: int, snippet: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev_{index}",
        source_id=f"src_{index}",
        locator=f"sheet!{index}",
        snippet=snippet,
        source_type="internal_memo",
    )


def test_value_only_in_raw_signal_gets_a_marker_by_promotion():
    """D-3. 6.66 이 근거 발췌에 없고 참고 신호에만 있어도 각주가 붙는다."""
    evidence = [_ref(1, "GA 설계매니저 소통 채널 구축이 필요하다")]
    raw = [_ref(9, "보험설계지원 제안가 검토 가나손해보험 500억 900명 기여가치 5.5억원")]
    text = "가격 논리에는 가나손해보험 기여가치 5.5억원을 함께 제시할 수 있습니다."

    allocator = MarkerAllocator(list(evidence), [])
    kept = anchor_orphan_tokens(text, evidence, QUESTION, marker_for=allocator.existing)
    assert orphan_token_sentences(kept, QUESTION), "근거 발췌만으로는 붙일 곳이 없어야 한다"

    anchored = anchor_orphan_tokens(
        kept, raw, QUESTION, marker_for=lambda index: allocator.promote(raw[index])
    )
    assert "[2]" in anchored
    assert orphan_token_sentences(anchored, QUESTION) == []
    # 승격된 발췌가 근거 목록에 들어와야 사용자가 원본으로 갈 수 있다.
    assert [r.evidence_id for r in allocator.evidence] == ["ev_1", "ev_9"]
    assert allocator.added == [{"marker": 2, "evidence_id": "ev_9"}]


def test_question_words_alone_do_not_get_a_marker():
    """질문의 말을 되쓴 문장은 새 사실이 아니다. 아무 발췌나 붙이지 않는다."""
    evidence = [_ref(1, "전혀 다른 이야기가 적힌 발췌")]
    text = "가나손해보험 Sales Point는 세 가지로 정리됩니다."

    allocator = MarkerAllocator(list(evidence), [])
    out = anchor_orphan_tokens(text, evidence, QUESTION, marker_for=allocator.existing)
    assert out == text
    assert allocator.added == []


def test_unclassified_question_grants_no_source_of_record():
    """D-4. 분류 실패는 중립이다. 어느 자료도 T1 을 받지 못한다."""
    domain, failed = classify_claim_domain("오늘 날씨 어때")
    assert failed is True
    assert tier_for("release_spec", domain) == "T1"  # 분류가 됐다면 정본
    assert tier_for("release_spec", domain, neutral=True) != "T1"


def test_sales_point_question_is_a_deal_fact():
    """D-4. 영업 어휘가 딜 질문으로 분류되고 활동일지가 정본이 된다."""
    for question in (
        "가나손해보험에게 어떤 Sales Point를 잡는 것이 좋은가?",
        "이 고객사는 어떻게 공략해야 하나?",
        "세일즈 포인트를 정리해줘",
    ):
        domain, failed = classify_claim_domain(question)
        assert (domain, failed) == ("deal_fact", False), question
    assert tier_for("sales_activity_log", "deal_fact") == "T1"


def test_fabricated_company_name_is_still_flagged_across_parentheses():
    """괄호를 이름에서 뺀 뒤에도 지어낸 회사 이름은 그대로 걸린다(게이트 미약화 확인)."""
    support = "퇴직연금 대상은 은행(13개)·증권사(14개)·생명보험사(12개)입니다."
    question = "어떤 역량이 여러 산업으로 확장될 가능성이 높은가?"

    grounded = "퇴직연금(은행·증권사·생명보험사)을 우선순위로 두시길 권합니다."
    assert unsupported_tokens(grounded, support, question) == ([], [])

    invented = "(가상)없는손해보험이 이미 도입했습니다."
    _, terms = unsupported_tokens(invented, support, question)
    assert "없는손해보험" in terms
