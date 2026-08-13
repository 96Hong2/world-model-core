"""적재 payload 자료형.

핵심은 하나다: **비즈니스 엣지는 claim_ids 없이 만들 수 없다.**
런타임 검사만으로는 부족하다는 요구라서, 엣지를 만드는 코드 경로 자체를 둘로 갈랐다.

    knowledge_edge(...)              지식 백본 8종 전용. 비즈니스 타입을 넣으면 거부한다.
    business_edge(..., claim_ids=)   비즈니스 10종 전용. claim_ids 가 필수 키워드 인자다.

두 함수 모두 같은 비공개 생성기 `_add_edge` 를 부르고, 그 안에서 타입군과 claim_ids 를
동시에 확인한다. EdgeSpec 을 만드는 곳이 여기 하나뿐이라 우회 경로가 없다.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "contracts"
_ONTOLOGY = json.loads((CONTRACTS_DIR / "ontology.schema.json").read_text(encoding="utf-8"))

NODE_LABELS: frozenset[str] = frozenset(
    _ONTOLOGY["x-labels"]["business"] + _ONTOLOGY["x-labels"]["knowledge"]
)
KNOWLEDGE_EDGE_TYPES: frozenset[str] = frozenset(_ONTOLOGY["x-relations"]["knowledge_backbone"])
BUSINESS_EDGE_TYPES: frozenset[str] = frozenset(_ONTOLOGY["x-relations"]["business"])
EDGE_TYPES: frozenset[str] = KNOWLEDGE_EDGE_TYPES | BUSINESS_EDGE_TYPES

NodeRef = tuple[str, str]  # (label, natural_key)


class MissingClaimEvidenceError(ValueError):
    """근거(claim_ids) 없이 비즈니스 엣지를 만들려 했다."""


def _contract_registry() -> Registry:
    registry = Registry()
    for name in (
        "ontology.schema.json",
        "source_type.enum.json",
        "state.enum.json",
        "policy.schema.json",
        "answer.schema.json",
    ):
        doc = json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))
        uri = doc.get("$id") or f"https://worldmodel.example/contracts/{name}"
        registry = registry.with_resource(uri, Resource.from_contents(doc))
        # ontology.schema.json 이 상대 파일명으로 참조한다.
        registry = registry.with_resource(name, Resource.from_contents(doc))
    return registry


_VALIDATOR: Draft202012Validator | None = None


def ontology_validator() -> Draft202012Validator:
    global _VALIDATOR
    if _VALIDATOR is None:
        _VALIDATOR = Draft202012Validator(_ONTOLOGY, registry=_contract_registry())
    return _VALIDATOR


def sha(*parts: Any) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass
class NodeSpec:
    label: str
    natural_key: str
    props: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "id": self.natural_key,
            "natural_key": self.natural_key,
        }
        payload.update({k: v for k, v in self.props.items() if v is not None})
        return payload


@dataclass
class EdgeSpec:
    type: str
    start: NodeRef
    end: NodeRef
    props: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "from": self.start[1],
            "to": self.end[1],
            "from_label": self.start[0],
            "to_label": self.end[0],
        }
        payload.update({k: v for k, v in self.props.items() if v is not None})
        return payload


def _merge_props(current: dict[str, Any], incoming: dict[str, Any]) -> None:
    """같은 노드가 여러 Evidence 에서 다시 나올 때 속성을 합친다.

    리스트는 순서를 지키며 합집합(중복 제거), 스칼라는 비어 있을 때만 채운다.
    이미 있는 값을 덮어쓰지 않는 이유는 먼저 온 정본 소스의 값을 나중 소스가
    조용히 바꿔 버리는 것을 막기 위해서다.
    """
    for key, value in incoming.items():
        if value is None:
            continue
        if key not in current or current[key] is None:
            current[key] = value
            continue
        old = current[key]
        if isinstance(old, list) and isinstance(value, list):
            merged = list(old)
            for item in value:
                if item not in merged:
                    merged.append(item)
            current[key] = merged


class GraphBatch:
    """적재할 노드·엣지를 모아 두는 통. natural key 기준으로 자동 dedup 된다."""

    # EdgeSpec 을 만드는 유일한 자리다. 새로 만들지 말고 이 함수를 거쳐라.
    __slots__ = ("nodes", "edges")

    def __init__(self) -> None:
        self.nodes: dict[NodeRef, NodeSpec] = {}
        self.edges: dict[tuple[str, str, str], EdgeSpec] = {}

    # -- 노드 ---------------------------------------------------------------
    def node(self, label: str, natural_key: str, **props: Any) -> NodeRef:
        if label not in NODE_LABELS:
            raise ValueError(f"온톨로지에 없는 라벨: {label!r}")
        if not natural_key:
            raise ValueError(f"{label} 의 natural_key 가 비었다")
        ref: NodeRef = (label, natural_key)
        existing = self.nodes.get(ref)
        if existing is None:
            self.nodes[ref] = NodeSpec(label, natural_key, {k: v for k, v in props.items()})
        else:
            _merge_props(existing.props, props)
        return ref

    def has_node(self, ref: NodeRef) -> bool:
        return ref in self.nodes

    def find_node(self, label: str, natural_key: str) -> NodeSpec | None:
        return self.nodes.get((label, natural_key))

    def nodes_by_label(self, label: str) -> list[NodeSpec]:
        return [node for (lbl, _), node in self.nodes.items() if lbl == label]

    # -- 엣지 ---------------------------------------------------------------
    def knowledge_edge(self, rel_type: str, start: NodeRef, end: NodeRef, **props: Any) -> None:
        if rel_type in BUSINESS_EDGE_TYPES:
            raise ValueError(
                f"{rel_type} 은 비즈니스 엣지다. business_edge(claim_ids=...) 로만 만든다."
            )
        self._add_edge(rel_type, start, end, props)

    def business_edge(
        self,
        rel_type: str,
        start: NodeRef,
        end: NodeRef,
        *,
        claim_ids: Sequence[str],
        **props: Any,
    ) -> None:
        if rel_type not in BUSINESS_EDGE_TYPES:
            raise ValueError(f"{rel_type} 은 비즈니스 엣지가 아니다. knowledge_edge 를 써라.")
        self._add_edge(rel_type, start, end, {**props, "claim_ids": list(claim_ids)})

    def _add_edge(
        self, rel_type: str, start: NodeRef, end: NodeRef, props: dict[str, Any]
    ) -> None:
        if rel_type not in EDGE_TYPES:
            raise ValueError(f"온톨로지에 없는 관계 타입: {rel_type!r}")
        claim_ids = [cid for cid in (props.get("claim_ids") or []) if cid]
        if rel_type in BUSINESS_EDGE_TYPES and not claim_ids:
            raise MissingClaimEvidenceError(
                f"{rel_type} 엣지는 claim_ids 가 최소 1개 필요하다: {start} → {end}"
            )
        if rel_type in BUSINESS_EDGE_TYPES:
            props = {**props, "claim_ids": claim_ids}
        for ref in (start, end):
            if ref not in self.nodes:
                raise LookupError(f"엣지의 끝점 노드가 아직 없다: {ref} ({rel_type})")
        key = (rel_type, start[1], end[1])
        existing = self.edges.get(key)
        if existing is None:
            self.edges[key] = EdgeSpec(rel_type, start, end, dict(props))
        else:
            _merge_props(existing.props, props)

    def edges_of_type(self, rel_type: str) -> list[EdgeSpec]:
        return [edge for edge in self.edges.values() if edge.type == rel_type]

    # -- 출력 ---------------------------------------------------------------
    def to_payload(self) -> dict[str, Any]:
        return {
            "nodes": [node.as_payload() for node in self.nodes.values()],
            "edges": [edge.as_payload() for edge in self.edges.values()],
        }

    def fingerprint(self) -> str:
        """두 번 빌드했을 때 같은 그래프가 나오는지 비교하는 지문."""
        node_keys = sorted(f"{label}\t{key}" for label, key in self.nodes)
        edge_keys = sorted("\t".join(key) for key in self.edges)
        return sha("\n".join(node_keys), "\n".join(edge_keys))

    def summary(self) -> dict[str, Any]:
        nodes_by_label: dict[str, int] = {}
        for label, _ in self.nodes:
            nodes_by_label[label] = nodes_by_label.get(label, 0) + 1
        edges_by_type: dict[str, int] = {}
        for rel_type, _, _ in self.edges:
            edges_by_type[rel_type] = edges_by_type.get(rel_type, 0) + 1
        claims_by_status: dict[str, int] = {}
        lanes: dict[str, int] = {}
        for node in self.nodes_by_label("Claim"):
            status = node.props.get("status", "?")
            claims_by_status[status] = claims_by_status.get(status, 0) + 1
            for lane in node.props.get("lane") or []:
                lanes[lane] = lanes.get(lane, 0) + 1
        return {
            "nodes_by_label": nodes_by_label,
            "edges_by_type": edges_by_type,
            "claims_by_status": claims_by_status,
            "claims_by_lane": lanes,
            "critical_claims": lanes.get("critical", 0),
            "node_total": len(self.nodes),
            "edge_total": len(self.edges),
        }

    def validate(self, *, limit: int | None = None) -> list[str]:
        """계약 위반을 문자열 목록으로 돌려준다. 빈 목록이면 통과."""
        validator = ontology_validator()
        problems: list[str] = []
        for node in self.nodes.values():
            for error in validator.iter_errors({"nodes": [node.as_payload()], "edges": []}):
                problems.append(f"{node.label}/{node.natural_key}: {error.message}")
                break
            if limit is not None and len(problems) >= limit:
                return problems
        for edge in self.edges.values():
            for error in validator.iter_errors({"nodes": [], "edges": [edge.as_payload()]}):
                problems.append(f"{edge.type} {edge.start[1]}→{edge.end[1]}: {error.message}")
                break
            if limit is not None and len(problems) >= limit:
                return problems
        return problems


def dedupe(values: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if value is not None and value not in out:
            out.append(value)
    return out
