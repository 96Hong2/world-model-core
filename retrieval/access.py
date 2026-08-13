"""공통 후처리 1단 — 하드 필터.

`contracts/policy.schema.json` 의 consumption_points 가 순서를 못 박고 있다.
    후보 Evidence → (1) sensitivity 하드 필터 → (2) Time 게이트 → (3) Authority 적용

그래서 하드 필터는 **ACL 과 시간 둘뿐**이다. authority·status 로 거르지 않는다.
같은 파일 x-rules: "authority·status 를 필터로 쓰면 rare-critical 이 사라진다".
무엇이 걸러졌는지는 개수·존재 모두 밝히지 않고 `results_may_be_incomplete` 불리언 하나로만
알린다(answer.schema.json x-forbidden-fields.denied_source_count).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .types import EvidenceRef

#: 시간 게이트에서 탈락시키는 문서 상태. 점수 감점이 아니라 탈락이다(policy.schema modifiers).
SUPERSEDED_STATES = frozenset({"superseded", "retired", "obsolete"})


@dataclass(frozen=True)
class AccessPolicy:
    """데모 계정 하나에 대한 접근 정책.

    1A 는 per-user RBAC 을 만들지 않는다(V2). 승인된 소수 내부 사용자 전제이므로
    기본값은 세 등급을 모두 허용하되, 정책 객체를 바꿔 끼우면 즉시 좁혀진다.
    """

    allowed_sensitivity: frozenset[str] = frozenset({"public", "internal", "restricted"})
    drop_superseded: bool = True

    def permits(self, evidence: EvidenceRef) -> bool:
        sensitivity = (evidence.sensitivity or "internal").lower()
        if sensitivity not in self.allowed_sensitivity:
            return False
        if self.drop_superseded:
            if (evidence.doc_status or "").lower() in SUPERSEDED_STATES:
                return False
            if (evidence.source_status or "").lower() in SUPERSEDED_STATES:
                return False
        return True


DEMO_POLICY = AccessPolicy()


def apply_hard_filters(
    evidence: Iterable[EvidenceRef], policy: AccessPolicy
) -> tuple[list[EvidenceRef], bool]:
    """(통과한 근거, 무언가 걸러졌는가)."""
    kept: list[EvidenceRef] = []
    filtered = False
    for item in evidence:
        if policy.permits(item):
            kept.append(item)
        else:
            filtered = True
    return kept, filtered


def permitted_source_ids(
    sources: Sequence[dict], policy: AccessPolicy
) -> tuple[set[str], bool]:
    """Source 메타 목록에서 접근 가능한 source_id 집합을 만든다(원문 검색 경로용)."""
    allowed: set[str] = set()
    filtered = False
    for row in sources:
        probe = EvidenceRef(
            evidence_id="",
            source_id=str(row.get("source_id") or ""),
            locator="",
            snippet="",
            sensitivity=str(row.get("sensitivity") or ""),
            doc_status=str(row.get("doc_status") or ""),
            source_status=str(row.get("source_status") or ""),
        )
        if policy.permits(probe):
            allowed.add(probe.source_id)
        else:
            filtered = True
    return allowed, filtered
