"""모순 측정 (REVISED §5 — 1A 는 측정만).

1A 에서 하는 일은 **감지해서 나란히 보여 주는 것**까지다. 어느 쪽이 맞는지 고르지 않고,
한쪽으로 뭉개는 단일 합성을 막는 것이 목적이다. CONTRADICTS 엣지 생성·DISPUTED 병렬 제시
UX·승격 가드는 1B(B3)다.

감지 범위는 좁게 잡았다. **같은 것을 재는 서로 다른 금액**만 본다. 실제 자료에서 확인된
모순이 그 형태이고(같은 회사가 만든 두 제안서가 같은 벌금을 10배 다르게 적는다), 규칙을
넓히면 "3분기와 4분기 매출이 다르다" 같은 정상적인 차이를 전부 모순이라고 외친다.

성립 조건 셋을 모두 만족해야 한다.
    (1) 질문이 크기를 묻는다 (얼마·규모·금액·벌금 …)
    (2) 두 발췌가 서로 다른 Source 에서 왔고, 질문의 낱말을 함께 다룬다
    (3) 같은 통화 단위로 환산한 금액이 서로 2배 이상 차이 난다
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from retrieval.text import fold, term_overlap
from retrieval.types import EvidenceRef

MAGNITUDE_QUESTION = re.compile(r"얼마|규모|금액|벌금|비용|가격|몇\s*(억|만|건|원)")

#: 배수 단위. 한국어 수사와 영문 약어를 함께 본다.
SCALES: tuple[tuple[str, float], ...] = (
    ("조", 1e12),
    ("억", 1e8),
    ("만", 1e4),
    ("천", 1e3),
    ("B", 1e9),
    ("M", 1e6),
    ("K", 1e3),
)

CURRENCIES: tuple[str, ...] = ("달러", "USD", "원", "엔", "유로")

#: 숫자 + (배수) + 통화. "180M 달러" · "18억 달러" · "2,564억 원"
_AMOUNT = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만|천|B|M|K)?\s*(달러|USD|원|엔|유로)"
)

#: 이 배수 이상 벌어져야 모순으로 본다.
DIVERGENCE_FACTOR = 2.0


@dataclass(frozen=True)
class Amount:
    text: str
    value: float
    currency: str


def extract_amounts(text: str) -> list[Amount]:
    out: list[Amount] = []
    for match in _AMOUNT.finditer(text or ""):
        raw, scale, currency = match.group(1), match.group(2), match.group(3)
        try:
            number = float(raw.replace(",", ""))
        except ValueError:
            continue
        factor = dict(SCALES).get(scale or "", 1.0)
        out.append(
            Amount(text=match.group(0).strip(), value=number * factor, currency=currency)
        )
    return out


def detect(
    question: str, evidence: Sequence[EvidenceRef], terms: Sequence[str]
) -> list[dict]:
    """감지된 모순을 Answer 계약의 disputes 모양으로 돌려준다."""
    if not MAGNITUDE_QUESTION.search(question or ""):
        return []

    on_topic: list[tuple[EvidenceRef, Amount]] = []
    for ref in evidence:
        if terms and not term_overlap(list(terms), ref.snippet):
            continue
        for amount in extract_amounts(ref.snippet):
            on_topic.append((ref, amount))
    if len(on_topic) < 2:
        return []

    by_currency: dict[str, list[tuple[EvidenceRef, Amount]]] = {}
    for ref, amount in on_topic:
        by_currency.setdefault(amount.currency, []).append((ref, amount))

    for currency, items in by_currency.items():
        ordered = sorted(items, key=lambda pair: pair[1].value)
        low_ref, low = ordered[0]
        high_ref, high = ordered[-1]
        if low_ref.source_id == high_ref.source_id:
            continue
        if low.value <= 0 or high.value / low.value < DIVERGENCE_FACTOR:
            continue
        if fold(low.text) == fold(high.text):
            continue
        return [
            {
                "subject": f"{_subject_of(question)} ({currency} 표기)",
                "sides": [
                    {
                        "statement": f"{low.text} 로 적혀 있습니다.",
                        "evidence_ids": [low_ref.evidence_id],
                    },
                    {
                        "statement": f"{high.text} 로 적혀 있습니다.",
                        "evidence_ids": [high_ref.evidence_id],
                    },
                ],
            }
        ]
    return []


def _subject_of(question: str) -> str:
    text = (question or "").strip().rstrip("?？.! ")
    return text[:80]
