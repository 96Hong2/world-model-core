"""⑨ Neo4j 적재. natural key MERGE 만 쓴다 — 같은 입력을 두 번 넣어도 수가 같다.

Neo4j 속성은 스칼라와 스칼라 리스트만 받는다. BusinessDomain.maturity 처럼 계약이 객체로
정의한 값은 JSON 문자열로 직렬화해서 넣는다(읽는 쪽이 json.loads 한다).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from graph.connection import WritableGraph

from .model import BUSINESS_EDGE_TYPES, EDGE_TYPES, NODE_LABELS, GraphBatch

CHUNK = 500
JSON_PROPS = ("maturity",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _storable(props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in props.items():
        if value is None:
            continue
        if isinstance(value, dict):
            out[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, (list, tuple)):
            flat = [v for v in value if v is not None]
            if not flat:
                continue
            out[key] = [
                json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v
                for v in flat
            ]
        else:
            out[key] = value
    return out


class GraphWriter:
    def __init__(self, graph: WritableGraph, *, run_id: str = "ingest"):
        self.graph = graph
        self.run_id = run_id

    # -- 쓰기 ---------------------------------------------------------------
    def write(
        self,
        batch: GraphBatch,
        *,
        patch: bool = False,
        force_props: frozenset[str] | set[str] = frozenset(),
    ) -> dict[str, int]:
        """batch 를 적재한다.

        replace(기본): 전체 빌드용. 배치가 정본이므로 마지막 쓴 값이 이긴다.
        patch: 부분 빌드(--only·백필)용. 배치가 그래프의 일부만 아는 상태라,
        인메모리 병합(_merge_props)과 같은 규칙으로 기존 노드를 지킨다 —
        리스트는 합집합, 스칼라는 빈 칸만 채운다. 부분 배치의 1건짜리
        source_ids 가 전체 적재가 쌓아 둔 리스트를 덮던 결함의 방어다.
        force_props 에 적은 속성만 예외로 덮는다(금액 짝처럼 함께 바꿔야
        어긋나지 않는 값).
        """
        now = _now()
        by_label: dict[str, list[dict[str, Any]]] = {}
        for (label, natural_key), node in batch.nodes.items():
            props = _storable(node.props)
            props.pop("natural_key", None)
            props.pop("label", None)
            props["pipeline_run_id"] = self.run_id
            by_label.setdefault(label, []).append({"key": natural_key, "props": props})

        written_nodes = 0
        with self.graph.write_session() as session:
            for label, rows in by_label.items():
                if label not in NODE_LABELS:
                    raise ValueError(f"온톨로지에 없는 라벨: {label!r}")
                if patch:
                    self._merge_existing_node_props(session, label, rows, force_props)
                for chunk in _chunks(rows, CHUNK):
                    session.run(
                        "UNWIND $rows AS row "
                        f"MERGE (n:{label} {{natural_key: row.key}}) "
                        "ON CREATE SET n.created_at = $now "
                        "SET n += row.props, n.updated_at = $now",
                        rows=chunk,
                        now=now,
                    ).consume()
                    written_nodes += len(chunk)

            by_type: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
            for edge in batch.edges.values():
                if edge.type not in EDGE_TYPES:
                    raise ValueError(f"온톨로지에 없는 관계 타입: {edge.type!r}")
                if edge.type in BUSINESS_EDGE_TYPES and not edge.props.get("claim_ids"):
                    raise ValueError(f"claim_ids 없는 비즈니스 엣지: {edge.type}")
                key = (edge.type, edge.start[0], edge.end[0])
                by_type.setdefault(key, []).append(
                    {
                        "start": edge.start[1],
                        "end": edge.end[1],
                        "props": _storable(edge.props),
                    }
                )

            written_edges = 0
            for (rel_type, start_label, end_label), rows in by_type.items():
                if patch:
                    self._merge_existing_edge_props(
                        session, rel_type, start_label, end_label, rows, force_props
                    )
                for chunk in _chunks(rows, CHUNK):
                    session.run(
                        "UNWIND $rows AS row "
                        f"MATCH (a:{start_label} {{natural_key: row.start}}) "
                        f"MATCH (b:{end_label} {{natural_key: row.end}}) "
                        f"MERGE (a)-[r:{rel_type}]->(b) "
                        "SET r += row.props",
                        rows=chunk,
                    ).consume()
                    written_edges += len(chunk)

        return {"nodes": written_nodes, "edges": written_edges}

    # -- patch 병합 ----------------------------------------------------------
    def _merge_existing_node_props(
        self,
        session: Any,
        label: str,
        rows: list[dict[str, Any]],
        force_props: frozenset[str] | set[str],
    ) -> None:
        """이미 있는 노드는 row.props 를 「기존 ⊕ 새 값」 병합본으로 바꿔치기한다."""
        keys = [row["key"] for row in rows]
        existing: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(keys, CHUNK):
            result = session.run(
                f"MATCH (n:{label}) WHERE n.natural_key IN $keys "
                "RETURN n.natural_key AS key, properties(n) AS props",
                keys=chunk,
            )
            for record in result:
                existing[record["key"]] = dict(record["props"])
        for row in rows:
            current = existing.get(row["key"])
            if current is not None:
                row["props"] = _patch_merge(current, row["props"], force_props, self.run_id)

    def _merge_existing_edge_props(
        self,
        session: Any,
        rel_type: str,
        start_label: str,
        end_label: str,
        rows: list[dict[str, Any]],
        force_props: frozenset[str] | set[str],
    ) -> None:
        pairs = [{"start": row["start"], "end": row["end"]} for row in rows]
        existing: dict[tuple[str, str], dict[str, Any]] = {}
        for chunk in _chunks(pairs, CHUNK):
            result = session.run(
                "UNWIND $pairs AS pair "
                f"MATCH (a:{start_label} {{natural_key: pair.start}})"
                f"-[r:{rel_type}]->"
                f"(b:{end_label} {{natural_key: pair.end}}) "
                "RETURN pair.start AS s, pair.end AS e, properties(r) AS props",
                pairs=chunk,
            )
            for record in result:
                existing[(record["s"], record["e"])] = dict(record["props"])
        for row in rows:
            current = existing.get((row["start"], row["end"]))
            if current is not None:
                row["props"] = _patch_merge(current, row["props"], force_props, None)

    # -- 조회·정리 -----------------------------------------------------------
    def count_prefix(self, prefix: str) -> dict[str, int]:
        with self.graph.read_session() as session:
            nodes = session.run(
                "MATCH (n) WHERE n.natural_key STARTS WITH $p RETURN count(n) AS c", p=prefix
            ).single()["c"]
            edges = session.run(
                "MATCH (a)-[r]->(b) WHERE a.natural_key STARTS WITH $p "
                "AND b.natural_key STARTS WITH $p RETURN count(r) AS c",
                p=prefix,
            ).single()["c"]
        return {"nodes": nodes, "edges": edges}

    def purge_prefix(self, prefix: str) -> int:
        with self.graph.write_session() as session:
            return session.run(
                "MATCH (n) WHERE n.natural_key STARTS WITH $p DETACH DELETE n RETURN count(*) AS c",
                p=prefix,
            ).single()["c"]

    def reset(self) -> int:
        """이 파이프라인이 만든 노드만 지운다. pipeline_run_id 가 없는 노드는 건드리지 않는다."""
        with self.graph.write_session() as session:
            return session.run(
                "MATCH (n) WHERE n.pipeline_run_id IS NOT NULL "
                "DETACH DELETE n RETURN count(*) AS c"
            ).single()["c"]

    def stats(self) -> dict[str, Any]:
        with self.graph.read_session() as session:
            nodes = {
                row["label"]: row["c"]
                for row in session.run(
                    "MATCH (n) WHERE n.pipeline_run_id IS NOT NULL "
                    "UNWIND labels(n) AS label RETURN label, count(*) AS c ORDER BY label"
                ).data()
            }
            edges = {
                row["type"]: row["c"]
                for row in session.run(
                    "MATCH (a)-[r]->(b) WHERE a.pipeline_run_id IS NOT NULL "
                    "RETURN type(r) AS type, count(r) AS c ORDER BY type"
                ).data()
            }
            claims = {
                row["status"]: row["c"]
                for row in session.run(
                    "MATCH (n:Claim) WHERE n.pipeline_run_id IS NOT NULL "
                    "RETURN n.status AS status, count(*) AS c ORDER BY status"
                ).data()
            }
            lanes = {
                row["lane"]: row["c"]
                for row in session.run(
                    "MATCH (n:Claim) WHERE n.pipeline_run_id IS NOT NULL "
                    "UNWIND n.lane AS lane RETURN lane, count(*) AS c ORDER BY lane"
                ).data()
            }
            unmapped_needs = session.run(
                "MATCH (n:Need) WHERE n.pipeline_run_id IS NOT NULL AND n.canonical = false "
                "RETURN count(*) AS c"
            ).single()["c"]
            unmapped_caps = session.run(
                "MATCH (n:Capability) WHERE n.pipeline_run_id IS NOT NULL "
                "AND n.unmapped_raw IS NOT NULL RETURN count(*) AS c"
            ).single()["c"]
        return {
            "nodes_by_label": nodes,
            "edges_by_type": edges,
            "claims_by_status": claims,
            "claims_by_lane": lanes,
            "node_total": sum(nodes.values()),
            "edge_total": sum(edges.values()),
            "unmapped_needs": unmapped_needs,
            "unmapped_capabilities": unmapped_caps,
        }


def _patch_merge(
    current: dict[str, Any],
    incoming: dict[str, Any],
    force_props: frozenset[str] | set[str],
    run_id: str | None,
) -> dict[str, Any]:
    """DB 에 이미 있는 속성과 부분 배치의 속성을 _merge_props 규칙으로 합친다.

    리스트는 순서를 지키며 합집합, 스칼라는 빈 칸만 채운다. force_props 는
    예외로 새 값이 이긴다. 장부 속성(pipeline_run_id·updated_at)은 병합
    대상이 아니라 마지막 쓴 쪽 기준이다.
    """
    merged = dict(current)
    merged.pop("natural_key", None)
    merged.pop("updated_at", None)
    for key, value in incoming.items():
        old = merged.get(key)
        if key in force_props or old is None:
            merged[key] = value
            continue
        if isinstance(old, list) and isinstance(value, list):
            union = list(old)
            for item in value:
                if item not in union:
                    union.append(item)
            merged[key] = union
    if run_id is not None:
        merged["pipeline_run_id"] = run_id
    return merged


def _chunks(rows: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]
