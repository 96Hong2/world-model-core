"""이미 지불한 추출 응답에서 니즈 후보를 되살려 taxonomy 에 붙인다(1회성 백필).

    .venv/bin/python scripts/backfill_llm_needs.py --dry-run     규모만 센다. LLM 0콜
    .venv/bin/python scripts/backfill_llm_needs.py --budget 2.5  실제로 매핑하고 적재한다

왜 백필이 필요한가.

`_map_llm_needs` 를 파이프라인에 넣었지만, 그 단계는 **이번 실행이 추출한 것**만 본다.
추출을 다시 돌리려면 돈을 또 내야 한다. 캐시가 있는데도 그렇다: 캐시 키가 배치 내용 해시이고
배치는 문서 풀 구성으로 정해지므로, 풀이 바뀌면(대상 계열·소스 목록이 달라지면) 같은 발췌라도
다른 배치에 담겨 캐시를 못 맞춘다. 실측으로 캐시는 문서 소스 18개를 담고 있는데 지금 풀은 16개다.

그래서 여기서는 캐시에 남은 응답(항목 1,840건 · need_raw 625건)을 그대로 입력으로 쓰고,
매핑 단계만 새로 부른다. 붙이는 규칙은 파이프라인의 `_map_llm_needs` 를 그대로 호출한다.
이 스크립트가 규칙을 따로 구현하지 않는다 — 두 벌이 되면 어긋난다.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.connection import WritableGraph  # noqa: E402
from ingestion.pipeline.direct import (  # noqa: E402
    LoadContext,
    account_node,
    add_evidence,
    build_feature_capability_index,
)
from ingestion.pipeline.llm_stage import LLMBudget, LLMStage  # noqa: E402
from ingestion.pipeline.model import GraphBatch  # noqa: E402
from ingestion.pipeline.resolve import Resolver  # noqa: E402
from ingestion.pipeline.runner import IngestPipeline, PipelineOptions, register_source  # noqa: E402
from ingestion.pipeline.settings import PARSED_DIR, Settings  # noqa: E402
from ingestion.pipeline.verdict import CriticalityEngine, VerdictEngine  # noqa: E402
from ingestion.pipeline.writer import GraphWriter  # noqa: E402

CACHE = ROOT / "data" / "cache" / "llm"


def cached_need_items() -> list[dict]:
    """캐시의 추출 응답에서 need_raw 와 계정이 함께 온 항목만 모은다."""
    items: list[dict] = []
    for path in sorted(CACHE.glob("*.json")):
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not str(obj.get("purpose") or "").startswith("extract:"):
            continue
        for item in (obj.get("parsed") or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            if not (item.get("need_raw") or "").strip():
                continue
            if not (item.get("account") or "").strip():
                continue
            if not (item.get("source_id") and item.get("locator")):
                continue
            items.append(item)
    return items


def parsed_records(source_ids: set[str]) -> dict[tuple[str, str], dict]:
    """(source_id, locator) → 파싱 레코드. Evidence 노드를 같은 키로 다시 찾으려면 필요하다."""
    index: dict[tuple[str, str], dict] = {}
    for sid in sorted(source_ids):
        path = PARSED_DIR / f"{sid}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            index[(record["source_id"], record["locator"])] = record
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="캐시에 남은 니즈 후보를 taxonomy 에 붙인다")
    parser.add_argument("--dry-run", action="store_true", help="LLM 을 부르지 않고 규모만 센다")
    parser.add_argument("--budget", type=float, default=10.0, metavar="USD")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="이번 실행에서 매핑할 표현 수 상한(0=전부). 나눠 돌 때 쓴다. "
        "끝난 배치는 LLM 캐시에 남아 다시 돌려도 공짜다",
    )
    args = parser.parse_args(argv)

    settings = Settings.load()
    items = cached_need_items()
    print(f"캐시에서 되살린 니즈 후보 {len(items)}건 (표현 {len({i['need_raw'].strip() for i in items})}종)")

    index = parsed_records({item["source_id"] for item in items})
    print(f"파싱 레코드로 위치를 맞춘 소스 {len({k[0] for k in index})}개")

    resolver = Resolver(settings)
    ctx = LoadContext(
        settings=settings,
        resolver=resolver,
        verdicts=VerdictEngine(settings),
        criticality=CriticalityEngine(settings),
        batch=GraphBatch(),
        feature_capability_index=build_feature_capability_index(settings),
    )

    # Source 노드와 계열을 config 로 채운다. 계열이 비면 판정 규칙이 엉뚱한 상태를 준다.
    for sid in {item["source_id"] for item in items}:
        source_path = PARSED_DIR / f"{sid}.source.json"
        if not source_path.exists():
            continue
        props = register_source(json.loads(source_path.read_text(encoding="utf-8")), settings)
        ctx.source_type[sid] = props["source_type"]

    evidence_refs: dict[str, object] = {}
    reasons: collections.Counter = collections.Counter()
    for item in items:
        record = index.get((item["source_id"], item["locator"]))
        if record is None:
            reasons["파싱 레코드에서 위치를 못 찾음"] += 1
            continue
        raw = item["need_raw"].strip()
        if resolver.map_need(raw).canonical:
            reasons["사전에 이미 걸림(대상 아님)"] += 1
            continue
        account_ref = account_node(
            ctx, item["account"].strip(), source_id=record["source_id"], from_title=True
        )
        if account_ref is None:
            reasons["계정 이름을 못 붙임"] += 1
            continue
        evidence_refs[record["evidence_id"]] = add_evidence(ctx, record)
        ctx.llm_need_pending.append({"raw": raw, "record": record, "account": account_ref})
        reasons["매핑 대기줄에 들어감"] += 1

    for reason, count in reasons.most_common():
        print(f"  {reason:<28} {count:>4}")
    if args.limit:
        keep = list(dict.fromkeys(entry["raw"] for entry in ctx.llm_need_pending))[: args.limit]
        wanted = set(keep)
        dropped = len({e["raw"] for e in ctx.llm_need_pending}) - len(wanted)
        ctx.llm_need_pending = [e for e in ctx.llm_need_pending if e["raw"] in wanted]
        print(f"  --limit {args.limit}: 이번에 {len(wanted)}종만 · 다음 실행으로 {dropped}종 남김")

    expressions = {entry["raw"] for entry in ctx.llm_need_pending}
    calls = -(-len(expressions) // args.batch_size)
    print(f"\n매핑 대상 표현 {len(expressions)}종 · 배치 {args.batch_size} 기준 {calls}콜")

    if args.dry_run:
        print("(dry-run: LLM 을 부르지 않았다)")
        return 0

    from llm.cache import LLMCache
    from llm.providers import ClaudeCLIProvider
    from llm.service import DEFAULT_CACHE_DIR, LLMService

    service = LLMService(ClaudeCLIProvider(), cache=LLMCache(DEFAULT_CACHE_DIR), budget_usd=1e9)
    budget = LLMBudget(max_usd=args.budget, max_calls=400, reserved_usd=args.budget)
    lexicon = [entry["canonical"] for entry in settings.aliases["accounts"]]
    stage = LLMStage(service, budget=budget, batch_size=args.batch_size, lexicon=lexicon)

    pipeline = IngestPipeline(PipelineOptions(use_llm=True), settings)
    pipeline._map_llm_needs(ctx, stage, evidence_refs)

    print(
        f"\n[LLM] 호출 {budget.calls} (캐시 히트 {budget.cache_hits}) · "
        f"비용 ${budget.spent_usd:.4f} / 상한 ${budget.max_usd:.2f}"
    )
    for target, count in stage.report.skipped.items():
        print(f"  못 한 것: {target} {count}건")
    print(f"매핑 성공 표현 {ctx.counters['llm_doc_need_mapping']}종 · HAS_NEED {ctx.counters['llm_doc_need_edges']}건")

    graph = WritableGraph()
    # 캐시 순회로 만든 부분 배치다. patch 로 써서 기존 노드의 리스트를 덮지 않는다.
    written = GraphWriter(graph).write(ctx.batch, patch=True)
    print(f"적재: 노드 {written['nodes']:,} · 엣지 {written['edges']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
