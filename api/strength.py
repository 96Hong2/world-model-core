"""Evidence Strength — 밴드 + basis (REVISED §4 · Answer 계약 evidence_strength).

사용자에게 보여 주는 것은 HIGH / MEDIUM / LOW 와 그 판단의 재료다. 확률처럼 읽히는 숫자를
내보내지 않는다. basis 에 들어가는 정수는 확률이 아니라 **개수**다(서로 다른 Source 몇 개,
자료 유형 몇 가지).

밴드 규칙은 전부 결정적이다.
    LOW     근거가 없거나 · 모순이 감지됐거나 · 가장 높은 권위가 대외 서술(T4)·미상(T5)일 때
    HIGH    그 질문 유형의 정본 자료(T1)가 있고 · 모순이 없고 · 자료가 낡지 않았을 때
    MEDIUM  그 사이

"근거가 여러 개"를 셀 때는 **Source 단위**로 센다. 같은 자료에서 발췌를 열 개 뽑아 온 것은
독립 근거 하나다. 이걸 발췌 수로 세면 한 문서만으로 HIGH 가 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Sequence

from .authority import TIER_ORDER, best_tier, tier_for

#: 관측 시점이 이보다 오래되면 낡았다고 본다.
CURRENT_DAYS = 365
AGING_DAYS = 730


@dataclass(frozen=True)
class EvidenceFact:
    """강도 산출에 필요한 최소 사실. Answer 계약의 evidence 항목에서 그대로 뽑아 쓴다."""

    evidence_id: str
    source_id: str
    source_type: str
    observed_at: str | None = None


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def recency_of(facts: Sequence[EvidenceFact], today: date | None = None) -> str:
    days = [_parse_day(f.observed_at) for f in facts]
    seen = [d for d in days if d is not None]
    if not seen:
        return "unknown"
    newest = max(seen)
    age = ((today or datetime.now(timezone.utc).date()) - newest).days
    if age <= CURRENT_DAYS:
        return "current"
    if age <= AGING_DAYS:
        return "aging"
    return "stale"


def assess(
    facts: Sequence[EvidenceFact],
    *,
    claim_domain: str,
    contradiction: str = "none",
    today: date | None = None,
    neutral: bool = False,
) -> dict[str, Any]:
    """`neutral=True`(질문 유형 미분류)면 T1 이 없으므로 HIGH 가 나오지 않는다.

    질문 유형을 모르면 무엇이 정본인지도 모른다. 그 상태에서 HIGH 를 주면 임의로 고른
    도메인의 정본 자료가 강도까지 올려 받는다.
    """
    sources = {f.source_id for f in facts if f.source_id}
    variety = {f.source_type for f in facts if f.source_type}
    tiers = [tier_for(f.source_type, claim_domain, neutral=neutral) for f in facts]
    top = best_tier(tiers)
    recency = recency_of(facts, today)

    highest_authority = "internal_memo"
    if facts:
        ranked = sorted(
            facts,
            key=lambda f: TIER_ORDER.get(
                tier_for(f.source_type, claim_domain, neutral=neutral), 9
            ),
        )
        highest_authority = ranked[0].source_type or highest_authority

    if not facts or contradiction == "detected" or top in {"T4", "T5"}:
        band = "LOW"
    elif top == "T1" and recency != "stale":
        band = "HIGH"
    else:
        band = "MEDIUM"

    return {
        "band": band,
        "basis": {
            "independent_evidence": len(sources),
            "highest_authority": highest_authority,
            "contradiction": contradiction,
            "recency": recency,
            "source_type_variety": len(variety),
        },
    }
