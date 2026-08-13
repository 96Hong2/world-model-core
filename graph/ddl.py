"""schema.cypher 적용 CLI.

    python -m graph.ddl apply     제약·인덱스 생성(여러 번 실행해도 결과 동일)
    python -m graph.ddl status    제약·인덱스 목록과 개수 출력
    python -m graph.ddl drop      이 파일이 만든 제약·인덱스만 제거(데이터는 건드리지 않음)

fulltext analyzer 는 한국어 때문에 cjk 를 먼저 시도하고, 서버가 지원하지 않거나 생성이 실패하면
standard 로 내려 잡은 뒤 어느 것이 실제로 걸렸는지 출력한다.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Any

from neo4j.exceptions import Neo4jError

from graph.connection import ReadOnlyGraph, WritableGraph, writable_graph

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent / "schema.cypher"

PREFERRED_ANALYZER = "cjk"
FALLBACK_ANALYZER = "standard"
FULLTEXT_INDEX_NAMES = ("evidence_fulltext", "observation_fulltext")

_NAME_RE = re.compile(
    r"^\s*CREATE\s+(?:CONSTRAINT|(?:FULLTEXT\s+|TEXT\s+|POINT\s+|RANGE\s+)?INDEX)\s+([A-Za-z0-9_]+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# schema.cypher 파싱
# ---------------------------------------------------------------------------


def load_statements(schema_path: pathlib.Path | None = None) -> list[str]:
    """주석을 제거하고 세미콜론 단위로 나눈다."""
    text = (schema_path or SCHEMA_PATH).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("//")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def statement_name(statement: str) -> str | None:
    match = _NAME_RE.match(statement)
    return match.group(1) if match else None


def _is_fulltext(statement: str) -> bool:
    return "FULLTEXT INDEX" in statement.upper()


def _with_analyzer(statement: str, analyzer: str) -> str:
    return statement.replace(f"'{PREFERRED_ANALYZER}'", f"'{analyzer}'")


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------


def available_analyzers(graph: ReadOnlyGraph | WritableGraph) -> set[str]:
    with _read_session(graph) as session:
        rows = session.run("CALL db.index.fulltext.listAvailableAnalyzers()").data()
    return {row["analyzer"] for row in rows}


def fulltext_analyzer(
    graph: ReadOnlyGraph | WritableGraph, index_name: str = FULLTEXT_INDEX_NAMES[0]
) -> str | None:
    """실제로 인덱스에 걸린 analyzer 이름. 인덱스가 없으면 None."""
    with _read_session(graph) as session:
        row = session.run(
            "SHOW INDEXES YIELD name, options WHERE name = $name RETURN options",
            name=index_name,
        ).single()
    if row is None:
        return None
    return row["options"]["indexConfig"].get("fulltext.analyzer")


def status(graph: ReadOnlyGraph | WritableGraph) -> dict[str, Any]:
    with _read_session(graph) as session:
        constraints = [
            {
                "name": row["name"],
                "type": row["type"],
                "label": (row["labelsOrTypes"] or [None])[0],
                "properties": row["properties"],
            }
            for row in session.run(
                "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
                "RETURN name, type, labelsOrTypes, properties ORDER BY name"
            ).data()
        ]
        indexes = [
            {
                "name": row["name"],
                "type": row["type"],
                "state": row["state"],
                "labels": row["labelsOrTypes"],
                "properties": row["properties"],
                "owning_constraint": row["owningConstraint"],
            }
            for row in session.run(
                "SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties, owningConstraint "
                "RETURN name, type, state, labelsOrTypes, properties, owningConstraint ORDER BY name"
            ).data()
        ]
    return {
        "constraints": constraints,
        "indexes": indexes,
        "constraint_count": len(constraints),
        "index_count": len(indexes),
        "fulltext_analyzer": fulltext_analyzer(graph),
    }


def _read_session(graph: ReadOnlyGraph | WritableGraph):
    if isinstance(graph, WritableGraph):
        return graph.read_session()
    return graph.session()


# ---------------------------------------------------------------------------
# 적용 / 제거
# ---------------------------------------------------------------------------


def apply(
    graph: WritableGraph, schema_path: pathlib.Path | None = None, *, verbose: bool = False
) -> dict[str, Any]:
    statements = load_statements(schema_path)
    analyzers = available_analyzers(graph)
    chosen = PREFERRED_ANALYZER if PREFERRED_ANALYZER in analyzers else FALLBACK_ANALYZER
    notes: list[str] = []
    if chosen != PREFERRED_ANALYZER:
        notes.append(
            f"서버가 '{PREFERRED_ANALYZER}' analyzer 를 제공하지 않아 '{chosen}' 로 생성한다. "
            "한국어 어절 일부 검색(부분 일치)이 약해진다."
        )

    applied = 0
    with graph.write_session() as session:
        for statement in statements:
            target = _with_analyzer(statement, chosen) if _is_fulltext(statement) else statement
            try:
                session.run(target).consume()
            except Neo4jError as err:
                if _is_fulltext(statement) and chosen == PREFERRED_ANALYZER:
                    notes.append(
                        f"{statement_name(statement)}: '{PREFERRED_ANALYZER}' 생성 실패({err.code}) "
                        f"→ '{FALLBACK_ANALYZER}' 로 재시도한다."
                    )
                    chosen = FALLBACK_ANALYZER
                    session.run(_with_analyzer(statement, FALLBACK_ANALYZER)).consume()
                else:
                    raise
            applied += 1
            if verbose:
                print(f"  ok  {statement_name(statement) or statement.splitlines()[0]}")

    current = status(graph)
    actual_analyzer = current["fulltext_analyzer"]
    if actual_analyzer != chosen:
        # IF NOT EXISTS 라 이미 있던 인덱스의 analyzer 가 그대로 유지된 경우.
        notes.append(
            f"이미 존재하던 fulltext 인덱스의 analyzer 를 유지한다: '{actual_analyzer}' "
            f"(이번 적용이 요청한 값은 '{chosen}'). 바꾸려면 drop 후 apply 한다."
        )
    for note in notes:
        print(f"[analyzer] {note}")

    return {
        "statements": applied,
        "constraint_count": current["constraint_count"],
        "index_count": current["index_count"],
        "fulltext_analyzer": actual_analyzer,
        "notes": notes,
    }


def drop(graph: WritableGraph, schema_path: pathlib.Path | None = None) -> dict[str, Any]:
    """이 스키마 파일이 선언한 이름만 제거한다. 노드·관계 데이터는 손대지 않는다."""
    dropped_constraints: list[str] = []
    dropped_indexes: list[str] = []
    with graph.write_session() as session:
        for statement in load_statements(schema_path):
            name = statement_name(statement)
            if not name:
                continue
            if statement.upper().lstrip().startswith("CREATE CONSTRAINT"):
                session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()
                dropped_constraints.append(name)
            else:
                session.run(f"DROP INDEX {name} IF EXISTS").consume()
                dropped_indexes.append(name)
    return {"constraints": dropped_constraints, "indexes": dropped_indexes}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_status(graph: WritableGraph) -> None:
    current = status(graph)
    print(f"제약 {current['constraint_count']}개")
    for constraint in current["constraints"]:
        props = ", ".join(constraint["properties"] or [])
        print(f"  {constraint['name']:<34} {constraint['label'] or '-':<16} ({props}) {constraint['type']}")
    print(f"인덱스 {current['index_count']}개")
    for index in current["indexes"]:
        labels = ",".join(index["labels"] or [])
        props = ", ".join(index["properties"] or [])
        owner = f" ← {index['owning_constraint']}" if index["owning_constraint"] else ""
        print(f"  {index['name']:<34} {index['type']:<10} {labels:<16} ({props}) {index['state']}{owner}")
    print(f"fulltext analyzer: {current['fulltext_analyzer']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m graph.ddl", description=__doc__)
    parser.add_argument("command", choices=["apply", "drop", "status"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    with writable_graph() as graph:
        graph.verify_connectivity()
        if args.command == "apply":
            result = apply(graph, verbose=args.verbose)
            print(
                f"적용 {result['statements']}건 · 제약 {result['constraint_count']}개 · "
                f"인덱스 {result['index_count']}개 · analyzer {result['fulltext_analyzer']}"
            )
        elif args.command == "drop":
            result = drop(graph)
            print(
                f"제거: 제약 {len(result['constraints'])}개 · 인덱스 {len(result['indexes'])}개"
            )
        else:
            _print_status(graph)
    return 0


if __name__ == "__main__":
    sys.exit(main())
