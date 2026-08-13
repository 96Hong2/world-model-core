"""SubgraphBuilder.ensure_focal 검증.

왜 필요한가: 제품 자체를 묻는 질문("통합 테스트가 부족한 곳이 있나")은 비즈니스 엔티티를
이름으로 대지 않는다. 그때 Q-E 는 focal 을 세우지 못해 §10 의 시각 위계가 무너지고
eval 의 subgraph 채점기가 '질문의 중심 엔티티가 표시되지 않음'으로 실패시킨다.
Q-S 는 같은 상황을 이미 처리하고 있었다. 그 규칙을 Q-E 로 맞춘 것이 ensure_focal 이다.
"""

from __future__ import annotations

from retrieval.subgraph import SubgraphBuilder


def _ranks(builder: SubgraphBuilder) -> dict[str, str]:
    """위계만 본다. build() 는 focal 을 씨앗으로 잡고 이어지지 않은 노드를 잘라내므로
    (subgraph.py 의 seeds 절단) 여기서 build() 를 쓰면 검사 대상이 사라진다."""
    return {key: node.rank for key, node in builder._nodes.items()}  # noqa: SLF001


def test_promotes_cited_when_no_focal() -> None:
    b = SubgraphBuilder()
    b.add_node("sup1", ("Evidence",), "발췌", "supporting")
    b.add_node("cit1", ("Account",), "바람화재", "cited")
    b.add_node("sup2", ("Claim",), "주장", "supporting")

    promoted = b.ensure_focal()

    assert promoted == "cit1", "cited 가 있으면 그것을 올려야 한다"
    ranks = _ranks(b)
    assert ranks["cit1"] == "focal"
    assert ranks["sup1"] == "supporting", "다른 노드를 건드리면 안 된다"
    assert ranks["sup2"] == "supporting"


def test_promotes_supporting_when_no_cited() -> None:
    """cited 도 없는 경우. 빈 위계보다 supporting 하나를 올리는 것이 낫다."""
    b = SubgraphBuilder()
    b.add_node("sup1", ("Evidence",), "발췌", "supporting")

    promoted = b.ensure_focal()

    assert promoted == "sup1"
    assert _ranks(b)["sup1"] == "focal"


def test_does_nothing_when_focal_exists() -> None:
    """이미 focal 이 있으면 아무것도 바꾸지 않는다. 엉뚱한 노드를 중심으로 올리면 안 된다."""
    b = SubgraphBuilder()
    b.add_node("f", ("Account",), "바람화재", "focal")
    b.add_node("c", ("Deal",), "딜", "cited")

    assert b.ensure_focal() is None
    ranks = _ranks(b)
    assert ranks["f"] == "focal"
    assert ranks["c"] == "cited", "기존 위계를 흔들지 않는다"


def test_empty_builder_is_safe() -> None:
    b = SubgraphBuilder()
    assert b.ensure_focal() is None
    assert _ranks(b) == {}


def test_idempotent() -> None:
    """두 번 불러도 focal 이 늘어나지 않는다."""
    b = SubgraphBuilder()
    b.add_node("c1", ("Account",), "A", "cited")
    b.add_node("c2", ("Account",), "B", "cited")

    b.ensure_focal()
    b.ensure_focal()

    focal = [r for r in _ranks(b).values() if r == "focal"]
    assert len(focal) == 1, f"focal 이 {len(focal)}개다"
