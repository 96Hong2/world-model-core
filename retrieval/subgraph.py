"""Supporting Subgraph 조립.

절단 순서는 REVISED §10 이 정한다: **focal → cited → supporting**. Authority 는 노드 중요도에
쓰지 않는다(Authority 는 Evidence 의 신뢰 맥락이지 Entity 의 중요도가 아니다).
같은 위계 안에서만 **연결 수(degree)** 로 순서를 가린다 — 그림을 붙들고 있는 노드를 먼저 남기려는
것이고, 자료의 권위와는 무관하다.
상한은 계약(answer.schema.json)이 정한 노드 50 / 엣지 100 이다.
"""

from __future__ import annotations

from dataclasses import replace

from .types import Subgraph, SubgraphEdge, SubgraphNode

MAX_NODES = 50
MAX_EDGES = 100

RANK_ORDER = {"focal": 0, "cited": 1, "supporting": 2}


class SubgraphBuilder:
    def __init__(self, max_nodes: int = MAX_NODES, max_edges: int = MAX_EDGES):
        self._max_nodes = max_nodes
        self._max_edges = max_edges
        self._nodes: dict[str, SubgraphNode] = {}
        self._edges: list[SubgraphEdge] = []
        self._seen_edges: set[tuple[str, str, str]] = set()

    def add_node(
        self,
        key: str,
        labels: tuple[str, ...] | list[str],
        label_text: str,
        rank: str,
        status: str | None = None,
    ) -> None:
        if not key:
            return
        node = SubgraphNode(
            id=key,
            labels=tuple(labels) or ("Unknown",),
            label_text=label_text or key,
            rank=rank,
            status=status,
        )
        existing = self._nodes.get(key)
        if existing is None:
            self._nodes[key] = node
            return
        # 더 강한 위계로만 올린다. focal 로 잡힌 노드가 supporting 으로 내려가지 않게.
        if RANK_ORDER[rank] < RANK_ORDER[existing.rank]:
            self._nodes[key] = SubgraphNode(
                id=key,
                labels=tuple(dict.fromkeys(existing.labels + node.labels)),
                label_text=existing.label_text or node.label_text,
                rank=rank,
                status=existing.status or status,
            )

    def ensure_focal(self) -> str | None:
        """focal 이 하나도 없으면 가장 앞선 노드를 focal 로 올린다.

        질문이 비즈니스 엔티티를 이름으로 대지 않는 경우가 실재한다(제품 자체를 묻는 질문:
        "통합 테스트가 부족한 곳이 있나", "실패한 이벤트는 누가 처리하나"). 그때 Q-E 는
        focal 을 못 세우고, §10 의 시각 위계(focal → cited → supporting)가 성립하지 않는다.
        Q-S 는 같은 상황을 이미 '집계 첫 행을 focal 로' 처리한다. 그 규칙을 Q-E 로 맞춘다.

        올린 노드 키를 돌려준다(올릴 것이 없으면 None).
        """
        if any(node.rank == "focal" for node in self._nodes.values()):
            return None
        for key, node in self._nodes.items():
            if node.rank == "cited":
                self._nodes[key] = replace(node, rank="focal")
                return key
        # cited 도 없으면 supporting 중 첫 번째를 올린다. 빈 위계보다 낫다
        for key, node in self._nodes.items():
            self._nodes[key] = replace(node, rank="focal")
            return key
        return None

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        claim_ids: tuple[str, ...] = (),
    ) -> None:
        if not source or not target:
            return
        marker = (source, target, edge_type)
        if marker in self._seen_edges:
            return
        self._seen_edges.add(marker)
        self._edges.append(
            SubgraphEdge(source=source, target=target, type=edge_type, claim_ids=claim_ids)
        )

    def build(self) -> Subgraph:
        # 위계는 **시각 강조의 순서**이고 절단의 기준이 아니다. 위계를 1순위로 자르면 연결이
        # 하나도 없는 cited 가 자리를 다 먹고, 그림을 붙들고 있는 supporting 이 통째로 잘린다.
        # 그러면 그 노드에 걸린 엣지도 아래 kept_ids 필터에서 조용히 사라져, 노드는 상한까지
        # 찼는데 선은 몇 개뿐인 뭉치가 된다.
        # 실측: GQ-D2·GQ-D5 가 노드 50 · 엣지 17 · 고아 31 이었다. cited 가 49칸을 채워
        # supporting 인 Industry 가 0개 남고 HAS_NEED 41 · TARGETS 20~28 이 전부 떨어졌다.
        #
        # 그래서 **초점에서 연결을 따라 넓힌다.** 관계도의 목적은 초점 엔티티의 관계를 보여주는
        # 것이므로, 이어지지 않는 노드보다 이어지는 노드를 먼저 남긴다. 위계는 같은 후보들
        # 사이의 순서로만 쓴다(연결 수가 다음, 이름이 마지막).
        degree: dict[str, int] = {}
        adjacent: dict[str, set[str]] = {}
        for edge in self._edges:
            if edge.source not in self._nodes or edge.target not in self._nodes:
                continue
            degree[edge.source] = degree.get(edge.source, 0) + 1
            degree[edge.target] = degree.get(edge.target, 0) + 1
            adjacent.setdefault(edge.source, set()).add(edge.target)
            adjacent.setdefault(edge.target, set()).add(edge.source)

        def priority(node: SubgraphNode) -> tuple[int, int, str]:
            return (RANK_ORDER[node.rank], -degree.get(node.id, 0), node.label_text)

        ordered = sorted(self._nodes.values(), key=priority)

        # 씨앗은 focal 이다. focal 만으로 상한을 넘으면 우선순위 앞쪽만 남긴다.
        seeds = [n for n in ordered if n.rank == "focal"][: self._max_nodes]
        kept_ids: set[str] = {n.id for n in seeds}

        # 이어지는 노드를 우선순위대로 끌어온다. 더 이을 것이 없으면 **다음 무리의 씨앗**을
        # 새로 심고 이어서 키운다. 한 무리만 키우면 초점과 직접 닿지 않는 무리가 통째로
        # 빠진다(전사 집계 질문에서 다른 고객사 무리가 전부 사라졌다).
        while len(kept_ids) < self._max_nodes:
            frontier = [
                n
                for n in ordered
                if n.id not in kept_ids and not adjacent.get(n.id, set()).isdisjoint(kept_ids)
            ]
            if frontier:
                kept_ids.add(frontier[0].id)
                continue
            remaining = [n for n in ordered if n.id not in kept_ids]
            if not remaining:
                break
            # 새 씨앗은 **자라날 수 있는 것**(연결을 가진 것)만 고른다. 선이 하나도 없는 노드는
            # 넣지 않는다. 관계도의 목적은 관계를 보여 주는 것이라, 떠 있는 노드는 칸을 쓰고
            # 아무 관계도 말하지 않는다(실측: GQ-D2 에서 Source 4개가 그렇게 떠 있었다).
            growable = [n for n in remaining if degree.get(n.id, 0) > 0]
            if not growable:
                break
            seed = growable[0]
            kept_ids.add(seed.id)
            # **씨앗은 짝과 함께 심는다.** 혼자 심으면 그 무리를 키울 칸이 남지 않았을 때
            # 그대로 떠 있는 노드가 된다(실측: GQ-D2 에서 Need 5개가 HAS_NEED 상대 없이 남았다).
            if len(kept_ids) < self._max_nodes:
                partners = [
                    n for n in ordered if n.id not in kept_ids and n.id in adjacent.get(seed.id, ())
                ]
                if partners:
                    kept_ids.add(partners[0].id)

        # 화면 위계가 읽히도록 출력 순서는 우선순위대로 되돌린다.
        kept = [n for n in ordered if n.id in kept_ids]
        edges = [e for e in self._edges if e.source in kept_ids and e.target in kept_ids]
        truncated = len(ordered) > len(kept) or len(edges) > self._max_edges
        return Subgraph(nodes=kept, edges=edges[: self._max_edges], truncated=truncated)
