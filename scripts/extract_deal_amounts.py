"""자유 서술에 적힌 딜 금액을 이미 있는 딜에 붙인다.

    .venv/bin/python scripts/extract_deal_amounts.py --dry-run   후보 규모만 센다. LLM 0콜
    .venv/bin/python scripts/extract_deal_amounts.py --budget 3  실제로 뽑아서 보여준다
    .venv/bin/python scripts/extract_deal_amounts.py --budget 3 --write   그래프에 적재한다

왜 따로 도나.

`_extract_deal_amounts` 는 파이프라인 ⑦단계이지만 순서상 맨 뒤라, 전체 실행에서는 앞의 문서·미팅
추출이 예산을 다 쓰고 나면 한 콜도 못 받는다(니즈 매핑이 같은 이유로 굶어서 몫을 떼어 줬다).
게다가 전체 실행은 문서·미팅 추출을 다시 부르는데, LLM 캐시 키가 배치 내용 해시라서 풀 구성이
조금만 달라도 전부 미스가 된다(실측으로 $0.87 을 태우고 얻은 것이 0이었다).

그래서 여기서는 **결정적 직행만 전부 돌려** 딜·계정·발췌를 그대로 만든 뒤, LLM 은 금액 단계
하나만 부른다. 붙이는 규칙은 파이프라인의 `_extract_deal_amounts` 를 그대로 호출한다.
이 스크립트가 규칙을 따로 구현하지 않는다 — 두 벌이 되면 어긋난다.

`--write` 없이는 그래프를 건드리지 않는다. 금액은 그래프의 유일한 정량 축이라 사람이 목록을
눈으로 보고 나서 넣는다.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.pipeline.llm_stage import LLMBudget, LLMStage  # noqa: E402
from ingestion.pipeline.runner import (  # noqa: E402
    DOC_EXTRACT_KINDS,
    IngestPipeline,
    PipelineOptions,
    deal_amount_pool,
)
from ingestion.pipeline.settings import Settings  # noqa: E402

# 사람이 눈으로 판정해 딜 금액 칸에서 뺀 자리(2026-08-12). 근거 주장은 그대로 남는다.
# 둘 다 「우리 딜보다 범위가 넓다」다. Deal.amount_krw 에는 범위를 적을 칸이 없어서
# (계약 무수정) 넣으면 나중에 총액을 세는 사람이 우리 딜 규모로 읽는다.
SCOPE_TOO_WIDE = {
    # 수협은행 — 고객 프로그램 전체 예산이고 우리 채널 몫은 원문에 없다.
    "slack:C087J33P9PT/1777525740.020609": "예산은 콜봇/챗봇/채팅 25억 정도",
    # 미르캐피탈 — 다른 사업과 합산한 금액이다.
    "slack:C087J33P9PT/1747184774.401119": "콜봇 사업 금액과 취합해보니 대략 9억",
}

AMOUNT_COUNTERS = (
    "llm_deal_amount_set",
    "llm_deal_amount_kept_sheet",
    "llm_deal_amount_unparsed",
    "llm_deal_amount_account_unresolved",
    "llm_deal_amount_no_deal",
    "llm_deal_amount_ambiguous",
    "llm_deal_amount_bound_not_value",
    "llm_deal_amount_scope_excluded",
    "llm_deal_amount_placeholder_raw",
    "llm_deal_amount_runner_up",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="자유 서술에서 딜 금액 추출")
    parser.add_argument("--dry-run", action="store_true", help="후보만 세고 LLM 을 부르지 않는다")
    parser.add_argument("--budget", type=float, default=12.0, metavar="USD", help="LLM 비용 상한(sonnet 기준)")
    parser.add_argument("--batch-size", type=int, default=10, help="1콜에 묶을 발췌 수")
    parser.add_argument("--limit", type=int, default=0, help="후보 발췌를 이만큼만 본다(0=전부)")
    parser.add_argument("--write", action="store_true", help="그래프에 적재한다")
    parser.add_argument(
        "--keep-wide-scope",
        action="store_true",
        help="사람이 범위 넓다고 뺀 것도 딜 금액으로 쓴다(기본은 뺀다)",
    )
    args = parser.parse_args(argv)

    settings = Settings.load()
    pipeline = IngestPipeline(PipelineOptions(use_llm=False), settings)

    print("결정적 직행 빌드 중(LLM 없이 전체 소스)...")
    ctx, records_by_kind, evidence_refs, _notes, _sources = pipeline.load_deterministic()
    deals = ctx.batch.nodes_by_label("Deal")
    with_amount = [n for n in deals if n.props.get("amount_krw") is not None]
    print(f"  딜 {len(deals)}건 · 금액 보유 {len(with_amount)}건 · 발췌 {len(evidence_refs):,}건")

    pool = deal_amount_pool(
        ctx,
        [
            record
            for kind in ("meeting_note", *DOC_EXTRACT_KINDS)
            for record in (records_by_kind.get(kind) or [])
        ],
    )
    if args.limit:
        pool = pool[: args.limit]
    calls = -(-len(pool) // args.batch_size)
    print(f"\n금액 후보 발췌 {len(pool)}건 · 배치 {args.batch_size} 기준 {calls}콜")
    by_source: dict[str, int] = {}
    for record in pool:
        by_source[record["source_id"]] = by_source.get(record["source_id"], 0) + 1
    for sid, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {sid}")

    if args.dry_run:
        print("\n(dry-run: LLM 을 부르지 않았다)")
        return 0

    from llm.cache import LLMCache
    from llm.providers import ClaudeCLIProvider
    from llm.service import DEFAULT_CACHE_DIR, LLMService

    service = LLMService(ClaudeCLIProvider(), cache=LLMCache(DEFAULT_CACHE_DIR), budget_usd=1e9)
    budget = LLMBudget(max_usd=args.budget, max_calls=400)
    lexicon = [entry["canonical"] for entry in settings.aliases["accounts"]]
    stage = LLMStage(service, budget=budget, batch_size=args.batch_size, lexicon=lexicon)

    before = {ref: ctx.batch.find_node(*ref).props.get("amount_krw") for ref in _deal_refs(ctx)}
    exclude = () if args.keep_wide_scope else tuple(SCOPE_TOO_WIDE)
    if exclude:
        print(f"\n사람이 뺀 자리 {len(exclude)}건(범위가 넓어 딜 금액으로 쓰지 않는다):")
        for locator, why in SCOPE_TOO_WIDE.items():
            print(f"  {locator}  «{why}»")
    pipeline._extract_deal_amounts(
        ctx, stage, pool, evidence_refs, exclude_locators=exclude
    )

    print(
        f"\n[LLM] 호출 {budget.calls} (캐시 히트 {budget.cache_hits}) · "
        f"비용 ${budget.spent_usd:.4f} / 상한 ${budget.max_usd:.2f}"
    )
    print(f"게이트가 버린 항목 {stage.report.dropped_by_t2}건 · 스키마 실패 {stage.report.schema_failures}건")
    for target, count in stage.report.skipped.items():
        print(f"  못 한 것: {target} {count}건")
    for name in AMOUNT_COUNTERS:
        print(f"  {name:<40} {ctx.counters[name]}")

    changed = [
        (ref, ctx.batch.find_node(*ref).props.get("amount_krw"))
        for ref in _deal_refs(ctx)
        if before.get(ref) != ctx.batch.find_node(*ref).props.get("amount_krw")
    ]
    print(f"\n=== 금액이 새로 붙은 딜 {len(changed)}건 (사람이 눈으로 볼 목록) ===")
    print("근거 인용을 함께 낸다. 귀속이 맞는지는 인용을 읽어야 판정된다.\n")
    for ref, after in sorted(changed, key=lambda row: -(row[1] or 0)):
        node = ctx.batch.find_node(*ref)
        print(
            f"  {node.props['account_canonical'][:22]:24s} {after:>15,.0f}  "
            f"raw={node.props.get('amount_raw')}"
        )
        for cand in sorted(
            ctx.amount_candidates.get(ref, []), key=lambda c: c["krw"], reverse=True
        ):
            mark = "채택" if cand["raw"] == node.props.get("amount_raw") else "밀림"
            print(f"      [{mark}] {cand['kind']:9s} {cand['raw']:16s} {cand['observed_at']}")
            print(f"             {cand['quote'][:150]}")
            print(f"             {cand['locator']}")

    if not args.write:
        print("\n(--write 없이 돌았다. 그래프는 그대로다)")
        return 0

    from graph.connection import WritableGraph
    from ingestion.pipeline.writer import GraphWriter

    # 부분 배치라 patch 로 쓴다. 금액 짝은 함께 바꿔야 어긋나지 않으므로 예외로 덮는다.
    written = GraphWriter(WritableGraph()).write(
        ctx.batch, patch=True, force_props=frozenset({"amount_raw", "amount_krw"})
    )
    print(f"\n적재: 노드 {written['nodes']:,} · 엣지 {written['edges']:,}")
    return 0


def _deal_refs(ctx):
    return [("Deal", node.natural_key) for node in ctx.batch.nodes_by_label("Deal")]


if __name__ == "__main__":
    raise SystemExit(main())
