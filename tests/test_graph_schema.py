"""그래프 스키마(제약·인덱스·fulltext)와 드라이버 계층 검증.

기대값은 계약 파일(contracts/ontology.schema.json)과 요구사항에서 가져온다.
실행 중인 DB 의 출력에서 복사해 오지 않는다.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo4j.exceptions import Neo4jError  # noqa: E402

from graph import ddl  # noqa: E402
from graph.connection import (  # noqa: E402
    ReadOnlyGraph,
    WritableGraph,
    read_only_graph,
    writable_graph,
)
from graph.queries import (  # noqa: E402
    RELATIONSHIP_TYPES,
    merge_edge,
    merge_node,
    search_evidence_fulltext,
    search_observation_fulltext,
)

ONTOLOGY = json.loads((ROOT / "contracts" / "ontology.schema.json").read_text(encoding="utf-8"))
ONTOLOGY_LABELS = ONTOLOGY["x-labels"]["business"] + ONTOLOGY["x-labels"]["knowledge"]
ONTOLOGY_RELATIONS = (
    ONTOLOGY["x-relations"]["knowledge_backbone"] + ONTOLOGY["x-relations"]["business"]
)

# 테스트가 만든 노드만 지우기 위한 natural_key 접두사.
TEST_PREFIX = "pytest::graph-schema::"


def _purge(graph: WritableGraph) -> int:
    with graph.write_session() as session:
        return session.run(
            "MATCH (n) WHERE n.natural_key STARTS WITH $p DETACH DELETE n RETURN count(*) AS c",
            p=TEST_PREFIX,
        ).single()["c"]


@pytest.fixture(scope="module")
def graph() -> WritableGraph:
    g = writable_graph()
    g.verify_connectivity()
    yield g
    _purge(g)
    g.close()


@pytest.fixture(scope="module")
def applied(graph: WritableGraph) -> dict:
    """DDL 을 1회 적용한 상태를 만든다."""
    return ddl.apply(graph)


@pytest.fixture(autouse=True)
def clean_test_nodes(graph: WritableGraph):
    _purge(graph)
    yield
    left = _purge(graph)
    # 정리가 실제로 도는지 확인한다. 남으면 테스트가 깨지게 둔다.
    with graph.write_session() as session:
        remaining = session.run(
            "MATCH (n) WHERE n.natural_key STARTS WITH $p RETURN count(n) AS c",
            p=TEST_PREFIX,
        ).single()["c"]
    assert remaining == 0, f"테스트 노드 정리 실패: {remaining}개 남음 (직전 삭제 {left})"


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------


def test_ddl_apply_is_idempotent(graph: WritableGraph, applied: dict):
    before = ddl.status(graph)
    ddl.apply(graph)
    after = ddl.status(graph)

    assert after["constraint_count"] == before["constraint_count"]
    assert after["index_count"] == before["index_count"]
    assert {c["name"] for c in after["constraints"]} == {c["name"] for c in before["constraints"]}
    assert {i["name"] for i in after["indexes"]} == {i["name"] for i in before["indexes"]}


def test_every_ontology_label_has_uniqueness_constraint(graph: WritableGraph, applied: dict):
    assert len(ONTOLOGY_LABELS) == ONTOLOGY["x-label-count"]

    constrained = {c["label"] for c in ddl.status(graph)["constraints"]}
    missing = [label for label in ONTOLOGY_LABELS if label not in constrained]
    assert missing == [], f"UNIQUE 제약 없는 라벨: {missing}"


def test_no_constraint_on_label_outside_ontology(graph: WritableGraph, applied: dict):
    constrained = {c["label"] for c in ddl.status(graph)["constraints"]}
    extra = sorted(constrained - set(ONTOLOGY_LABELS))
    assert extra == [], f"온톨로지 15라벨 밖의 제약: {extra}"


def test_uniqueness_constraint_actually_blocks_duplicate(graph: WritableGraph, applied: dict):
    key = TEST_PREFIX + "dup-account"
    with graph.write_session() as session:
        session.run(
            "CREATE (n:Account {natural_key: $k, canonical_name: $k})", k=key
        ).consume()
        with pytest.raises(Neo4jError) as err:
            session.run(
                "CREATE (n:Account {natural_key: $k, canonical_name: $k2})", k=key, k2=key + "-2"
            ).consume()
    assert "ConstraintValidationFailed" in err.value.code


def test_fulltext_indexes_exist_with_expected_names(graph: WritableGraph, applied: dict):
    fulltext = {i["name"]: i for i in ddl.status(graph)["indexes"] if i["type"] == "FULLTEXT"}
    assert set(fulltext) == {"evidence_fulltext", "observation_fulltext"}
    assert fulltext["evidence_fulltext"]["labels"] == ["Evidence"]
    assert fulltext["observation_fulltext"]["labels"] == ["Observation"]


def test_no_vector_index_is_created(graph: WritableGraph, applied: dict):
    types = {i["type"] for i in ddl.status(graph)["indexes"]}
    assert "VECTOR" not in types


def test_status_reports_counts_matching_lists(graph: WritableGraph, applied: dict):
    status = ddl.status(graph)
    assert status["constraint_count"] == len(status["constraints"])
    assert status["index_count"] == len(status["indexes"])
    assert status["constraint_count"] > 0
    assert status["index_count"] > 0


# --------------------------------------------------------------------------
# fulltext 검색
# --------------------------------------------------------------------------


KOREAN_SNIPPETS = {
    "multitenant": "구독 사업을 추진하려면 멀티테넌트 구조 개발이 선행되어야 한다는 검토 의견",
    "kakao": "고객사 담당자가 카카오톡으로 업무하고 있어 대화 이력 통제가 어렵다는 문제 제기",
    "market": "퇴직연금 시장 규모는 80억에서 100억 사이로 추정한다는 내부 평가",
}


def _seed_evidence(graph: WritableGraph) -> dict[str, str]:
    ids = {}
    with graph.write_session() as session:
        for slug, snippet in KOREAN_SNIPPETS.items():
            key = TEST_PREFIX + "evidence::" + slug
            merge_node(
                session,
                "Evidence",
                key,
                {
                    "evidence_id": key,
                    "source_id": TEST_PREFIX + "source",
                    "locator": f"pytest!{slug}",
                    "snippet": snippet,
                    "masked": True,
                },
            )
            ids[slug] = key
    return ids


def test_evidence_fulltext_finds_korean_sentence(graph: WritableGraph, applied: dict):
    ids = _seed_evidence(graph)
    with graph.read_session() as session:
        hits = search_evidence_fulltext(session, "멀티테넌트", k=5)

    hit_ids = [h["evidence_id"] for h in hits]
    assert ids["multitenant"] in hit_ids
    assert ids["market"] not in hit_ids
    assert hits[0]["snippet"] == KOREAN_SNIPPETS["multitenant"]
    assert hits[0]["score"] > 0


def test_evidence_fulltext_respects_k(graph: WritableGraph, applied: dict):
    _seed_evidence(graph)
    with graph.read_session() as session:
        hits = search_evidence_fulltext(session, "업무 OR 구조 OR 시장", k=2)
    assert len(hits) <= 2


def test_observation_fulltext_finds_korean_statement(graph: WritableGraph, applied: dict):
    key = TEST_PREFIX + "observation::bd"
    with graph.write_session() as session:
        merge_node(
            session,
            "Observation",
            key,
            {
                "observation_id": key,
                "statement": "BD Overview 기대효과 시트가 식자재유통을 관리 BM 으로 분류했다",
                "evidence_ids": [TEST_PREFIX + "evidence::market"],
            },
        )
    with graph.read_session() as session:
        hits = search_observation_fulltext(session, "식자재유통", k=5)
    assert key in [h["observation_id"] for h in hits]


def test_fulltext_analyzer_is_recorded(graph: WritableGraph, applied: dict):
    """cjk 를 먼저 쓰고, 못 쓰면 standard 로 두되 어느 쪽인지 기록되어야 한다."""
    analyzer = ddl.fulltext_analyzer(graph, "evidence_fulltext")
    assert analyzer in {"cjk", "standard"}
    assert applied["fulltext_analyzer"] == analyzer
    assert ddl.fulltext_analyzer(graph, "observation_fulltext") == analyzer

    if analyzer == "cjk":
        # cjk 는 bigram 이라 어절 일부로도 걸린다. standard 는 어절 단위라 안 걸린다.
        ids = _seed_evidence(graph)
        with graph.read_session() as session:
            hits = search_evidence_fulltext(session, "테넌트", k=5)
        assert ids["multitenant"] in [h["evidence_id"] for h in hits]


def test_fulltext_query_with_lucene_special_chars_does_not_raise(
    graph: WritableGraph, applied: dict
):
    _seed_evidence(graph)
    with graph.read_session() as session:
        assert search_evidence_fulltext(session, "멀티테넌트 (구조) [검토]!", k=3) is not None


# --------------------------------------------------------------------------
# 읽기/쓰기 세션 분리
# --------------------------------------------------------------------------


def test_read_only_graph_has_no_write_api():
    with read_only_graph() as g:
        assert isinstance(g, ReadOnlyGraph)
        assert not hasattr(g, "write_session")
        assert not hasattr(g, "execute_write")


def test_read_session_cannot_write():
    with read_only_graph() as g, g.session() as session:
        with pytest.raises(Neo4jError) as err:
            session.run("CREATE (n:Account {natural_key: $k})", k=TEST_PREFIX + "illegal").consume()
    assert err.value.code == "Neo.ClientError.Statement.AccessMode"


def test_writable_graph_read_session_also_cannot_write(graph: WritableGraph):
    with graph.read_session() as session:
        with pytest.raises(Neo4jError) as err:
            session.run("CREATE (n:Account {natural_key: $k})", k=TEST_PREFIX + "illegal2").consume()
    assert err.value.code == "Neo.ClientError.Statement.AccessMode"


def test_require_read_only_rejects_writable_graph(graph: WritableGraph):
    from graph.connection import require_read_only

    with pytest.raises(TypeError):
        require_read_only(graph)
    with read_only_graph() as g:
        assert require_read_only(g) is g


# --------------------------------------------------------------------------
# MERGE 헬퍼
# --------------------------------------------------------------------------


def test_merge_node_twice_creates_one_node(graph: WritableGraph, applied: dict):
    key = TEST_PREFIX + "account::kb"
    with graph.write_session() as session:
        merge_node(session, "Account", key, {"canonical_name": key, "account_kind": "prospect"})
        merge_node(session, "Account", key, {"canonical_name": key, "account_kind": "customer"})
        rows = session.run(
            "MATCH (n:Account {natural_key: $k}) RETURN n.account_kind AS kind", k=key
        ).data()

    assert len(rows) == 1
    assert rows[0]["kind"] == "customer"


def test_merge_node_rejects_label_outside_ontology(graph: WritableGraph):
    with graph.write_session() as session:
        with pytest.raises(ValueError):
            merge_node(session, "Person", TEST_PREFIX + "person", {})


def test_merge_edge_twice_creates_one_relationship(graph: WritableGraph, applied: dict):
    deal = TEST_PREFIX + "deal::kb"
    account = TEST_PREFIX + "account::kb2"
    with graph.write_session() as session:
        merge_node(session, "Deal", deal, {"account_canonical": account})
        merge_node(session, "Account", account, {"canonical_name": account})
        for _ in range(2):
            merge_edge(
                session,
                "WITH_ACCOUNT",
                ("Deal", deal),
                ("Account", account),
                {"claim_ids": [TEST_PREFIX + "claim"]},
            )
        count = session.run(
            "MATCH (:Deal {natural_key: $d})-[r:WITH_ACCOUNT]->(:Account {natural_key: $a}) "
            "RETURN count(r) AS c",
            d=deal,
            a=account,
        ).single()["c"]
    assert count == 1


def test_merge_edge_rejects_type_outside_ontology(graph: WritableGraph):
    assert set(RELATIONSHIP_TYPES) == set(ONTOLOGY_RELATIONS)
    assert len(RELATIONSHIP_TYPES) == ONTOLOGY["x-relation-count"]

    with graph.write_session() as session:
        with pytest.raises(ValueError):
            merge_edge(
                session,
                "WORKS_FOR",
                ("Account", TEST_PREFIX + "a"),
                ("Account", TEST_PREFIX + "b"),
            )
