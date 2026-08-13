"""근거 후보 id 를 모으는 그릇.

한 줄로 이어 붙인 뒤 앞에서 잘라내면, 행이 많은 집계 하나가 상한을 통째로 먹고 뒤에
오는 집계는 한 건도 못 들어온다. 실측이 그랬다.

    가나손해보험 질문(Q-S)에서 상한 40 을 채우는 데 BD 집계 78건 · Need 집계 72건이면
    충분했고, 정작 답에 필요한 딜 근거 4건은 185번째라 후보에 들어오지도 못했다.
    B2B유통 질문도 같은 이유로 Need 집계의 뒤쪽 행이 통째로 잘렸다.

그래서 **어디서 온 후보인지**(집계 단계와 그 안의 몇 번째 행인지)를 그룹으로 들고 있다가,
상한을 그룹에 고르게 나눈다. 행 하나가 최소 한 건씩은 후보에 들어간 다음에야 어느 행이든
두 번째 후보를 낸다. 자료가 많은 고객사·도메인이 작은 자료를 밀어내지 않게 하는 장치다.

여기서 순위를 매기지 않는다. 무엇을 답변에 실을지 고르는 일은 A6 몫이고, 이 모듈의 책임은
"고를 기회조차 없는 후보"를 만들지 않는 것뿐이다.

다만 **어디서 온 후보인지는 A6 에게 넘긴다**(`origins`). 후보를 공평하게 모아 놓아도 A6 가
마지막에 24건으로 자를 때 다시 한 줄로 세워 자르면 같은 굶주림이 그 자리에서 되풀이되기
때문이다. 실측이 그랬다 — 후보 120건 중 BD 20행이 전부 들어와 있는데 최종 24건에는 두 행만
남았다.
"""

from __future__ import annotations

from typing import Iterable


class CandidatePool:
    def __init__(self) -> None:
        self._groups: dict[str, list[str]] = {}
        self._origin: dict[str, str] = {}

    def add(self, group: str, evidence_ids: Iterable[str] | None) -> None:
        """`group` 이 낸 후보를 순서대로 쌓는다. 같은 이름으로 여러 번 불러도 이어 붙는다."""
        if not evidence_ids:
            return
        bucket = self._groups.setdefault(group, [])
        for eid in evidence_ids:
            if eid:
                bucket.append(eid)
                self._origin.setdefault(eid, group)

    def add_rows(self, stage: str, rows: Iterable[dict], key: str = "evidence_ids") -> None:
        """집계 행 목록을 행마다 따로 된 그룹으로 넣는다."""
        for index, row in enumerate(rows):
            self.add(f"{stage}#{index}", (row or {}).get(key))

    @property
    def group_count(self) -> int:
        return len(self._groups)

    def origins(self) -> dict[str, str]:
        """근거 id → 그 후보를 처음 낸 그룹 이름. 같은 id 를 여러 그룹이 내면 첫 그룹만 남는다."""
        return dict(self._origin)

    def balanced(self, limit: int | None) -> list[str]:
        """두 번에 나눠 채운다. 중복 id 는 처음 나온 자리에만 남는다.

        1차는 그룹마다 한 건씩 돌린다. 어느 집계 행도 후보에 이름을 못 올리는 일을 없앤다.
        2차는 남은 자리를 원래 순서대로 깊이 채운다. 질문의 중심 엔티티에서 나온 앞쪽 그룹은
        보통 여러 건이 다 근거가 되므로, 한 건씩만 끊어 가면 오히려 답이 얇아진다.
        """
        out: list[str] = []
        seen: set[str] = set()
        cursors = {name: 0 for name in self._groups}

        def take(name: str) -> bool:
            bucket = self._groups[name]
            index = cursors[name]
            while index < len(bucket) and bucket[index] in seen:
                index += 1
            if index >= len(bucket):
                cursors[name] = index
                return False
            cursors[name] = index + 1
            seen.add(bucket[index])
            out.append(bucket[index])
            return True

        for name in self._groups:
            if limit is not None and len(out) >= limit:
                return out
            take(name)

        for name in self._groups:
            while limit is None or len(out) < limit:
                if not take(name):
                    break
            if limit is not None and len(out) >= limit:
                break
        return out


__all__ = ["CandidatePool"]
