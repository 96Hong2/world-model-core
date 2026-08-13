"""⑤ LLM 추출 + ④ 2차 canonical 매핑 제안.

LLM 은 후보만 만든다. 이 모듈이 지키는 것 세 가지.

  1. 스키마에 status·lane·claim_id 같은 판정/쓰기 필드를 넣지 못한다
     (llm.extraction.build_extraction_schema 가 거부한다). 응답에 그런 키가 섞여 오면
     `reject_status_writes` 가 파이프라인을 세운다.
  2. canonical 신규 생성 금지 — 매핑 제안의 반환값은 taxonomy 에 이미 있는 id 의 enum 이다.
     "unmapped" 를 고를 수는 있어도 없는 id 를 만들어 낼 수는 없다.
  3. 예산·호출 수 상한을 넘으면 그 자리에서 멈추고 **무엇을 못 했는지 남긴다**.
     조용히 건너뛰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from llm.extraction import (
    FORBIDDEN_EXTRACTION_FIELDS,
    build_extraction_prompt,
    build_extraction_schema,
    t2_gate,
)
from llm.service import LLMService

UNMAPPED = "unmapped"
DEFAULT_BATCH_SIZE = 10


class LLMStatusWriteRejected(ValueError):
    """LLM 응답이 상태·그래프 쓰기 필드를 담고 있다(계약 I4)."""


def build_candidate_schema(
    item_properties: dict[str, Any],
    *,
    required: Sequence[str] = (),
    max_items: int | None = None,
) -> dict[str, Any]:
    return build_extraction_schema(item_properties, required=required, max_items=max_items)


def reject_status_writes(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    checked = list(items)
    for item in checked:
        offending = sorted(set(item) & FORBIDDEN_EXTRACTION_FIELDS)
        if offending:
            raise LLMStatusWriteRejected(
                f"LLM 응답이 쓰면 안 되는 필드를 담았다: {offending}. "
                "상태 판정과 그래프 쓰기는 적재 규칙의 몫이다."
            )
    return checked


@dataclass
class LLMBudget:
    """이번 실행에 쓸 수 있는 상한. 넘으면 멈추고 남은 일을 기록한다."""

    max_usd: float = 8.0
    max_calls: int = 400
    spent_usd: float = 0.0
    calls: int = 0
    cache_hits: int = 0
    # 매핑 단계 몫. 추출은 여기까지 쓰지 못한다.
    # 매핑은 순서상 맨 마지막이라 떼어 두지 않으면 차례가 오기 전에 예산이 빈다.
    reserved_usd: float = 0.0

    def __post_init__(self) -> None:
        # 예산보다 큰 몫을 떼면 추출이 한 콜도 못 한다.
        self.reserved_usd = min(max(self.reserved_usd, 0.0), self.max_usd)

    def can_spend(self, *, use_reserve: bool = False) -> bool:
        ceiling = self.max_usd if use_reserve else self.max_usd - self.reserved_usd
        return self.calls < self.max_calls and self.spent_usd < ceiling

    def record(self, cost: float, *, cache_hit: bool) -> None:
        self.calls += 1
        self.spent_usd += cost
        if cache_hit:
            self.cache_hits += 1


@dataclass
class LLMStageReport:
    budget: LLMBudget
    processed: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    dropped_by_t2: int = 0
    schema_failures: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)


MEETING_ITEM_PROPERTIES: dict[str, Any] = {
    "signal": {
        "type": "string",
        "enum": ["need", "competition", "deal_progress", "risk", "next_action"],
        "description": "이 항목이 무엇에 대한 신호인가",
    },
    "account": {
        "type": "string",
        "description": "발췌에 등장한 고객사·기관 이름. 발췌에 적힌 표기를 그대로 옮긴다.",
    },
    "competitor": {
        "type": "string",
        "description": "발췌에 등장한 경쟁·대체 상대. 없으면 이 필드를 넣지 않는다.",
    },
    "need_raw": {
        "type": "string",
        "description": "고객이 말한 요구·불편·제약을 발췌의 표현으로 한 문장.",
    },
    "statement": {
        "type": "string",
        "description": "발췌가 말하는 사실 한 문장. 해석·추측을 덧붙이지 않는다.",
    },
}

MEETING_INSTRUCTION = (
    "영업 미팅 기록에서 (1) 언급된 고객사, (2) 고객이 말한 요구·불편, "
    "(3) 경쟁·대체 상대, (4) 딜 진행 사실을 뽑아라. "
    "각 항목은 발췌 한 곳에 근거해야 한다."
)

DEAL_AMOUNT_ITEM_PROPERTIES: dict[str, Any] = {
    "account": {
        "type": "string",
        "description": "이 금액의 주인인 고객사·기관 이름. 발췌에 적힌 표기를 그대로 옮긴다.",
    },
    "amount_raw": {
        "type": "string",
        "description": (
            "금액 표현만 발췌에 적힌 그대로 옮긴다(예: '3.77억', '393,000,000원', '약 5억'). "
            "앞뒤 설명을 붙이지 않고, 단위를 바꾸거나 계산하지 않는다."
        ),
    },
    "amount_kind": {
        "type": "string",
        "enum": ["contract", "proposal", "budget"],
        "description": (
            "contract=체결된 계약 금액 · proposal=우리가 제안·견적한 금액 · "
            "budget=고객이 잡은 사업 예산"
        ),
    },
    "statement": {
        "type": "string",
        "description": "발췌가 말하는 사실 한 문장. 해석·추측을 덧붙이지 않는다.",
    },
}

# 귀속을 틀리면 그래프의 유일한 정량 축이 오염된다. 그래서 뽑지 않을 것을 먼저 적는다.
# 실제로 섞여 있던 것: 마인드웨어웍스 900억 투자 유치 · 고객사 콜센터 750억 · kt ds 100억.
DEAL_AMOUNT_INSTRUCTION = (
    "영업 기록에서 **우리가 그 고객사와 하는 이 사업의 금액**만 뽑아라.\n"
    "아래는 딜 금액이 아니다. 발췌에 적혀 있어도 뽑지 않는다.\n"
    "- 고객사·경쟁사의 투자 유치액·매출액·영업이익·시가총액\n"
    "- 고객사가 이미 운영하고 있는 사업(콜센터 등)의 규모\n"
    "- 경쟁사가 다른 곳에서 수주한 금액\n"
    "- 같은 발췌에 함께 등장하는 다른 고객사의 금액\n"
    "- 인건비 단가·월 사용료처럼 사업 총액이 아닌 값\n"
    "금액과 고객사의 관계가 확실하지 않으면 항목을 만들지 않는다. 금액이 없으면 빈 목록을 낸다."
)

MAPPING_RULES = (
    "주어진 후보 id 목록에서만 고른다. 목록에 없는 id 를 만들지 않는다.",
    f"어느 항목에도 해당하지 않으면 {UNMAPPED!r} 를 고른다. 억지로 끼워 맞추지 않는다.",
)


class LLMStage:
    """예산 안에서 후보를 만들어 돌려준다. 그래프는 건드리지 않는다."""

    def __init__(
        self,
        service: LLMService,
        *,
        budget: LLMBudget | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lexicon: Sequence[str] = (),
    ):
        self.service = service
        self.budget = budget or LLMBudget()
        self.batch_size = max(1, int(batch_size))
        self.lexicon = list(lexicon)
        self.report = LLMStageReport(budget=self.budget)

    # -- ④ 2차 매핑 제안 ----------------------------------------------------
    def propose_mappings(
        self,
        *,
        kind: str,
        excerpts: Sequence[dict[str, Any]],
        allowed_ids: Sequence[str],
        catalog: Sequence[tuple[str, str]],
    ) -> dict[str, str]:
        """사전에 안 걸린 원문 표현을 기존 taxonomy 항목에 붙일 후보를 받는다.

        `allowed_ids` 밖의 값은 스키마 enum 이 막는다 — 신규 canonical 은 만들 수 없다.
        """
        if not excerpts:
            return {}
        schema = build_candidate_schema(
            {
                "raw_expression": {
                    "type": "string",
                    "description": "매핑 대상 원문 표현. 발췌에 적힌 그대로 옮긴다.",
                },
                "mapped_id": {"type": "string", "enum": [*allowed_ids, UNMAPPED]},
            },
            required=["raw_expression", "mapped_id"],
        )
        catalog_text = "\n".join(f"- {cid}: {name}" for cid, name in catalog)
        instruction = (
            f"아래 원문 표현 각각을 기존 {kind} 목록의 항목 하나에 연결해라.\n\n"
            f"[{kind} 목록]\n{catalog_text}"
        )

        mapping: dict[str, str] = {}
        for batch in _chunks(list(excerpts), self.batch_size):
            # 떼어 둔 몫을 쓴다. 매핑 1콜이 HAS_NEED 을 만들고, 추출 1콜은 관측 몇 줄이다.
            if not self.budget.can_spend(use_reserve=True):
                self.report.skipped[f"{kind}_mapping"] = self.report.skipped.get(
                    f"{kind}_mapping", 0
                ) + len(batch)
                continue
            items = self._call(
                instruction,
                schema=schema,
                excerpts=batch,
                purpose=f"canonical_mapping:{kind}",
                extra_rules=MAPPING_RULES,
                quote_bound_extra=("raw_expression",),
            )
            for item in items:
                mapped = item.get("mapped_id")
                raw = item.get("raw_expression")
                if raw and mapped and mapped != UNMAPPED and mapped in allowed_ids:
                    mapping[raw] = mapped
            self.report.processed[f"{kind}_mapping"] = self.report.processed.get(
                f"{kind}_mapping", 0
            ) + len(batch)
        return mapping

    # -- ⑤ 미팅 기록 추출 ---------------------------------------------------
    def extract_meeting_candidates(
        self, excerpts: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not excerpts:
            return []
        schema = build_candidate_schema(
            MEETING_ITEM_PROPERTIES, required=["signal", "statement"], max_items=40
        )
        results: list[dict[str, Any]] = []
        for batch in _chunks(list(excerpts), self.batch_size):
            if not self.budget.can_spend():
                self.report.skipped["meeting_note"] = self.report.skipped.get(
                    "meeting_note", 0
                ) + len(batch)
                continue
            results.extend(
                self._call(
                    MEETING_INSTRUCTION,
                    schema=schema,
                    excerpts=batch,
                    purpose="extract:meeting_note",
                    # 이름은 인용이 아니라 발췌 원문과 대조한다(대조는 runner 몫).
                    # 미팅 기록은 회사 이름이 제목 줄에 있고 인용은 본문 한 문장이라,
                    # 인용에 묶으면 구조적으로 다 죽는다(실측: 38건 중 36건 탈락).
                    quote_bound_exempt=("account",),
                )
            )
            self.report.processed["meeting_note"] = self.report.processed.get(
                "meeting_note", 0
            ) + len(batch)
        return results

    def extract_deal_amounts(self, excerpts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """딜 금액이 적힌 자유 서술에서 금액과 그 주인을 뽑는다.

        `amount_raw` 와 `account` 를 원문 그대로 요구한다(quote-bound). 숫자는 t2 게이트가
        발췌 안에 있는지 다시 대조하므로, 모델이 만들어 낸 금액은 그래프에 닿지 못한다.
        """
        if not excerpts:
            return []
        schema = build_candidate_schema(
            DEAL_AMOUNT_ITEM_PROPERTIES,
            required=["account", "amount_raw", "amount_kind", "statement"],
            max_items=20,
        )
        results: list[dict[str, Any]] = []
        for batch in _chunks(list(excerpts), self.batch_size):
            if not self.budget.can_spend():
                self.report.skipped["deal_amount"] = self.report.skipped.get(
                    "deal_amount", 0
                ) + len(batch)
                continue
            results.extend(
                self._call(
                    DEAL_AMOUNT_INSTRUCTION,
                    schema=schema,
                    excerpts=batch,
                    purpose="extract:deal_amount",
                    quote_bound_extra=("amount_raw",),
                    quote_bound_exempt=("account", "statement"),
                    # 회사 이름을 인용에 묶지 않는다. 모델이 고르는 인용은 금액이 있는 한 문장이고
                    # (「계약금액 : 299,000,000원(VAT별도)」) 회사 이름은 발췌 맨 위 제목 줄에 있다.
                    # 묶었을 때 실측으로 10건 중 10건이 버려졌다(2026-08-12 · 3콜 $0.20).
                    # 이름과 인용은 파이프라인이 **발췌 원문**과 대조한다(_extract_deal_amounts).
                )
            )
            self.report.processed["deal_amount"] = self.report.processed.get(
                "deal_amount", 0
            ) + len(batch)
        return results

    def extract_document_candidates(
        self, excerpts: Sequence[dict[str, Any]], *, purpose: str
    ) -> list[dict[str, Any]]:
        if not excerpts:
            return []
        schema = build_candidate_schema(
            MEETING_ITEM_PROPERTIES, required=["signal", "statement"], max_items=40
        )
        results: list[dict[str, Any]] = []
        for batch in _chunks(list(excerpts), self.batch_size):
            if not self.budget.can_spend():
                self.report.skipped[purpose] = self.report.skipped.get(purpose, 0) + len(batch)
                continue
            results.extend(
                self._call(
                    "문서 발췌에서 고객 요구·제품 역량·경쟁 상황에 관한 사실을 뽑아라.",
                    schema=schema,
                    excerpts=batch,
                    purpose=f"extract:{purpose}",
                )
            )
            self.report.processed[purpose] = self.report.processed.get(purpose, 0) + len(batch)
        return results

    # -- 공통 ---------------------------------------------------------------
    def _call(
        self,
        instruction: str,
        *,
        schema: dict[str, Any],
        excerpts: Sequence[dict[str, Any]],
        purpose: str,
        extra_rules: Sequence[str] = (),
        quote_bound_extra: Sequence[str] = (),
        quote_bound_exempt: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        prompt = build_extraction_prompt(
            instruction, schema=schema, excerpts=excerpts, extra_rules=extra_rules
        )
        result = self.service.complete(prompt, schema=schema, tier="S", purpose=purpose)
        self.budget.record(result.cost_usd, cache_hit=result.cache_hit)

        if not result.ok or not isinstance(result.parsed, dict):
            self.report.schema_failures += 1
            self.report.note(f"{purpose}: 스키마 실패 — {result.error}")
            return []

        raw_items = result.parsed.get("items") or []
        checked = reject_status_writes(raw_items)
        gate = t2_gate(
            checked,
            lexicon=self.lexicon,
            extra_quote_bound_fields=quote_bound_extra,
            quote_bound_exempt=quote_bound_exempt,
        )
        self.report.dropped_by_t2 += gate.drop_count
        return gate.kept


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
