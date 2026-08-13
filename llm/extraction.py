"""추출 전용 얇은 헬퍼.

두 가지만 한다.

1. 반환 스키마를 만들 때 상태·그래프 쓰기 필드를 아예 금지한다. LLM 은 후보만 만든다.
   status 를 쓰려는 응답은 스키마 검증에서 떨어져 repair 를 거쳐 실패로 돌아온다.
   모든 항목에 source_id·locator·evidence_quote 를 필수로 요구해, 근거 없는 후보가
   파이프라인에 들어오지 못하게 한다.

2. T2 게이트. 추출 결과의 숫자와 지정 고유명사가 evidence_quote 안에 실제로 있는지
   문자열로 확인한다. 없으면 그 항목을 버린다. 판정은 전부 코드다 — 모델에게 되묻지 않는다.

게이트가 못 잡는 것(한계, 추측 아님):
  · 사전에 없고 지정 필드도 아닌 자유 텍스트의 고유명사는 검사하지 않는다.
    고객사·경쟁사 이름은 호출부가 lexicon 으로 넣어 주어야 걸러진다.
  · 숫자는 문자열 포함 검사다. 3.77 과 3.770 을 같은 값으로 보지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# 스키마에 아예 넣지 못하게 막는 이름. 상태 판정과 그래프 쓰기는 코드의 몫이다.
FORBIDDEN_EXTRACTION_FIELDS = frozenset(
    {
        # 상태·판정
        "status",
        "state",
        "lane",
        "verdict",
        "claim_kind",
        # 신뢰도·권위 (deterministic 산출물)
        "evidence_strength",
        "confidence",
        "confidence_band",
        "authority",
        "authority_label",
        "authority_rank",
        # 그래프 쓰기
        "node_id",
        "node_ids",
        "edge",
        "edges",
        "relation",
        "relations",
        "relationship",
        "relationships",
        "claim_id",
        "claim_ids",
        "observation_id",
        "same_as",
        "canonical_id",
        "merge",
        "merge_into",
        "cypher",
    }
)

PROVENANCE_FIELDS = ("source_id", "locator", "evidence_quote")

# 이 이름의 값은 evidence_quote 안에 그대로 있어야 한다. canonical 매핑 대상인
# need/capability/business_domain 은 일부러 넣지 않는다 — 원문과 다른 표현이 정상이다.
QUOTE_BOUND_FIELDS = frozenset(
    {
        "entity",
        "account",
        "company",
        "customer",
        "competitor",
        "product",
        "person",
        "org",
        "organization",
        "vendor",
        "partner",
    }
)

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WS = re.compile(r"\s+")


class ExtractionSchemaError(ValueError):
    """추출 스키마에 넣으면 안 되는 필드를 넣었다."""


def build_extraction_schema(
    item_properties: dict[str, Any],
    *,
    required: Sequence[str] = (),
    max_items: int | None = None,
) -> dict[str, Any]:
    """{"items": [...]} 모양의 추출 스키마를 만든다."""
    forbidden = sorted(set(item_properties) & FORBIDDEN_EXTRACTION_FIELDS)
    if forbidden:
        raise ExtractionSchemaError(
            f"추출 스키마에 넣을 수 없는 필드: {forbidden}. "
            "상태 판정과 그래프 쓰기는 적재 단계가 규칙으로 정한다."
        )
    overlap = sorted(set(item_properties) & set(PROVENANCE_FIELDS))
    if overlap:
        raise ExtractionSchemaError(
            f"근거 필드는 재정의할 수 없다: {overlap}. 형식이 고정이어야 T2 게이트가 성립한다."
        )
    unknown_required = sorted(set(required) - set(item_properties))
    if unknown_required:
        raise ExtractionSchemaError(f"정의되지 않은 필드를 required 로 지정했다: {unknown_required}")

    item_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": [*PROVENANCE_FIELDS, *required],
        "properties": {
            "source_id": {
                "type": "string",
                "description": "이 후보가 나온 Source 의 id. 주어진 값을 그대로 쓴다.",
            },
            "locator": {
                "type": "string",
                "description": "원본에서 이 자리를 찾아갈 좌표. 주어진 값을 그대로 쓴다.",
            },
            "evidence_quote": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "근거가 되는 원문 발췌. 요약·의역 금지, 그대로 옮긴다.",
            },
            **item_properties,
        },
    }

    items_schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if max_items is not None:
        items_schema["maxItems"] = int(max_items)

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {"items": items_schema},
    }


def build_extraction_prompt(
    instruction: str,
    *,
    schema: dict[str, Any],
    excerpts: Sequence[Mapping[str, Any]],
    extra_rules: Sequence[str] = (),
) -> str:
    """추출 프롬프트를 만든다. 스키마를 본문에 함께 싣는 것이 핵심이다.

    스키마를 안 실으면 모델이 제 마음대로 필드 이름을 지어내고, 재검증에서 전부 떨어져
    repair 만 태우다 실패한다(실측으로 확인했다: type/value 같은 임의 필드를 반환해
    3회 호출 $0.048 을 쓰고 실패). 근거 필드를 발췌마다 명시해 두어야 T2 게이트도 성립한다.
    """
    if not excerpts:
        raise ExtractionSchemaError("발췌 없이는 추출을 요청하지 않는다")

    blocks = []
    for excerpt in excerpts:
        missing = [key for key in ("source_id", "locator", "text") if not excerpt.get(key)]
        if missing:
            raise ExtractionSchemaError(f"발췌에 빠진 항목: {missing}")
        blocks.append(
            f"- source_id: {excerpt['source_id']}\n"
            f"  locator: {excerpt['locator']}\n"
            f"  원문: {excerpt['text']}"
        )

    rules = [
        "발췌에 실제로 있는 내용만 뽑는다. 없는 값을 추론하거나 지어내지 않는다.",
        "source_id 와 locator 는 위에 적힌 값을 그대로 옮긴다.",
        "evidence_quote 에는 근거가 된 원문 문장을 그대로 넣는다. 요약·의역하지 않는다.",
        "숫자와 고유명사는 발췌에 나온 표기를 그대로 쓴다.",
        "해당하는 내용이 없으면 items 를 빈 배열로 둔다.",
        *extra_rules,
    ]

    return (
        "너는 문서에서 사실 후보를 뽑는 추출기다. 판정이나 상태 부여는 하지 않는다.\n\n"
        f"[할 일]\n{instruction}\n\n"
        "[발췌]\n" + "\n".join(blocks) + "\n\n"
        "[규칙]\n" + "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, 1)) + "\n\n"
        "[출력 형식]\n"
        "아래 JSON Schema 를 정확히 만족하는 JSON 객체 하나만 출력한다. "
        "스키마에 없는 필드를 추가하지 않는다. 설명 문장을 덧붙이지 않는다.\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )


@dataclass
class T2Report:
    kept: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[tuple[dict[str, Any], list[str]]] = field(default_factory=list)

    @property
    def drop_count(self) -> int:
        return len(self.dropped)


def t2_gate(
    items: Iterable[dict[str, Any]],
    *,
    lexicon: Iterable[str] = (),
    quote_field: str = "evidence_quote",
    extra_quote_bound_fields: Iterable[str] = (),
    quote_bound_exempt: Iterable[str] = (),
) -> T2Report:
    """발췌에 없는 숫자·고유명사를 가진 항목을 버린다.

    `quote_bound_exempt` 는 **이름을 인용이 아니라 발췌 원문과 대조하는** 단계를 위한 것이다.
    대조 자체를 빼는 것이 아니라 대조할 상대를 바꾸는 것이고, 대조는 호출자가 한다.
    금액 추출이 그렇다: 모델이 고르는 인용은 금액이 있는 한 문장이고 회사 이름은 발췌 맨 위
    제목 줄에 있어서, 이름을 인용에 묶으면 실측으로 10건 중 10건이 버려졌다.
    숫자 대조(`_check_numbers`)는 면제 대상에도 그대로 걸린다.
    """
    report = T2Report()
    terms = [term for term in lexicon if term and term.strip()]
    exempt = set(quote_bound_exempt)
    bound_fields = (QUOTE_BOUND_FIELDS | set(extra_quote_bound_fields)) - exempt

    for item in items:
        quote = str(item.get(quote_field) or "")
        if not quote.strip():
            report.dropped.append((item, [f"{quote_field} 가 비어 있다"]))
            continue

        reasons: list[str] = []
        quote_norm = _normalize(quote)
        quote_numeric = quote_norm.replace(",", "")

        for name, value in item.items():
            if name == quote_field or name in ("source_id", "locator"):
                continue
            for text in _scalar_strings(value):
                reasons.extend(_check_numbers(name, text, quote_numeric))
                if name in bound_fields:
                    reasons.extend(_check_verbatim(name, text, quote_norm))
                if name not in exempt:
                    reasons.extend(_check_lexicon(name, text, quote_norm, terms))

        if reasons:
            report.dropped.append((item, _dedupe(reasons)))
        else:
            report.kept.append(item)

    return report


# ----------------------------------------------------------------------
def _normalize(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _scalar_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for entry in value:
            out.extend(_scalar_strings(entry))
        return out
    if isinstance(value, dict):
        out = []
        for entry in value.values():
            out.extend(_scalar_strings(entry))
        return out
    return []


def _check_numbers(field_name: str, text: str, quote_numeric: str) -> list[str]:
    reasons = []
    for token in _NUMBER.findall(text):
        plain = token.replace(",", "").rstrip(".")
        if not plain:
            continue
        if plain not in quote_numeric:
            reasons.append(f"{field_name}: 숫자 {token!r} 이 발췌에 없다")
    return reasons


def _check_verbatim(field_name: str, text: str, quote_norm: str) -> list[str]:
    value = _normalize(text)
    if value and value not in quote_norm:
        return [f"{field_name}: 고유명사 {value!r} 이 발췌에 없다"]
    return []


def _check_lexicon(field_name: str, text: str, quote_norm: str, terms: list[str]) -> list[str]:
    value = _normalize(text)
    lowered_value = value.lower()
    lowered_quote = quote_norm.lower()
    reasons = []
    for term in terms:
        needle = _normalize(term).lower()
        if needle and needle in lowered_value and needle not in lowered_quote:
            reasons.append(f"{field_name}: 사전 표제어 {term!r} 이 발췌에 없다")
    return reasons


def _dedupe(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            out.append(reason)
    return out
