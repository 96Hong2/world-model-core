#!/usr/bin/env python
"""Snapshot Ingest CLI.

    ingest.py run [--only ID ...] [--no-llm] [--budget USD] [--max-calls N]
    ingest.py stats
    ingest.py reset

`run` 은 build → 계약 검증 → Neo4j MERGE 순으로 간다. 계약을 어긴 payload 는 쓰지 않고
멈춘다. 같은 입력을 두 번 넣어도 노드·엣지 수가 늘지 않는다(natural key MERGE).

한 번에 다 못 끝내도 되게 실행 기록을 `data/cache/ingest_progress.json` 에 누적한다.
LLM 을 어느 소스까지 돌렸는지, 예산 때문에 무엇을 건너뛰었는지가 거기 남는다.
건너뛴 것은 화면에도 반드시 찍는다 — 조용한 누락을 만들지 않는다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.connection import writable_graph  # noqa: E402
from ingestion.pipeline.runner import BuildResult, IngestPipeline, PipelineOptions  # noqa: E402
from ingestion.pipeline.writer import GraphWriter  # noqa: E402

PROGRESS_PATH = ROOT / "data" / "cache" / "ingest_progress.json"


# ---------------------------------------------------------------------------
# 표 출력
# ---------------------------------------------------------------------------


def table(title: str, rows: list[tuple[str, Any]], *, headers: tuple[str, str]) -> str:
    if not rows:
        return f"\n[{title}]\n  (없음)\n"
    left = max([len(headers[0]), *(len(str(k)) for k, _ in rows)])
    right = max([len(headers[1]), *(len(f"{v:,}" if isinstance(v, int) else str(v)) for _, v in rows)])
    line = "  " + "-" * (left + right + 3)
    out = [f"\n[{title}]", f"  {headers[0]:<{left}} | {headers[1]:>{right}}", line]
    for key, value in rows:
        shown = f"{value:,}" if isinstance(value, int) else str(value)
        out.append(f"  {str(key):<{left}} | {shown:>{right}}")
    out.append(line)
    total = sum(v for _, v in rows if isinstance(v, int))
    out.append(f"  {'합계':<{left}} | {total:>{right},}")
    return "\n".join(out) + "\n"


def sorted_desc(mapping: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(mapping.items(), key=lambda item: (-item[1], item[0]))


# ---------------------------------------------------------------------------
# 실행 기록
# ---------------------------------------------------------------------------


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_progress() -> dict[str, Any]:
    if not PROGRESS_PATH.exists():
        return {"runs": [], "llm_done_sources": [], "llm_spent_usd": 0.0, "llm_calls": 0}
    return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))


def save_progress(progress: dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(
        json.dumps(progress, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


def llm_summary(report: Any) -> dict[str, Any]:
    if report is None:
        return {}
    budget = report.budget
    return {
        "calls": budget.calls,
        "cache_hits": budget.cache_hits,
        "spent_usd": round(budget.spent_usd, 4),
        "max_usd": budget.max_usd,
        "reserved_usd": budget.reserved_usd,
        "max_calls": budget.max_calls,
        "processed": dict(report.processed),
        "skipped": dict(report.skipped),
        "dropped_by_t2": report.dropped_by_t2,
        "schema_failures": report.schema_failures,
        "notes": list(report.notes),
    }


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    options = PipelineOptions(
        only=tuple(args.only or ()),
        use_llm=not args.no_llm,
        budget_usd=args.budget,
        max_calls=args.max_calls,
        batch_size=args.batch_size,
        run_id=args.run_id,
        mapping_reserve_usd=args.mapping_usd,
    )
    started = time.time()
    pipeline = IngestPipeline(options)
    targets = [path.name[: -len(".source.json")] for path in pipeline.source_files()]
    if not targets:
        print("적재할 소스가 없다. --only 값을 확인해라.", file=sys.stderr)
        return 2
    print(f"소스 {len(targets)}개 빌드 중 (LLM {'사용' if options.use_llm else '미사용'}) ...")

    result: BuildResult = pipeline.build()
    build_seconds = time.time() - started

    problems = result.batch.validate(limit=20)
    if problems:
        print(f"\n계약 위반 {len(problems)}건. 적재하지 않고 멈춘다.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    written = {"nodes": 0, "edges": 0}
    if args.dry_run:
        print("\n--dry-run: Neo4j 쓰기를 건너뛴다.")
    else:
        with writable_graph() as graph:
            writer = GraphWriter(graph, run_id=options.run_id)
            # --only 부분 빌드는 그래프의 일부만 아는 배치라 patch 로 쓴다.
            # 기존 노드의 리스트(source_ids 등)를 1건짜리로 덮지 않기 위해서다.
            partial = bool(options.only)
            if partial:
                print("부분 적재(--only)라 patch 모드로 쓴다: 기존 값을 덮지 않는다.")
            written = writer.write(result.batch, patch=partial)

    elapsed = time.time() - started
    report = llm_summary(result.llm_report)

    progress = load_progress()
    progress.setdefault("runs", []).append(
        {
            "at": now(),
            "sources": result.sources,
            "only": list(options.only),
            "use_llm": options.use_llm,
            "dry_run": bool(args.dry_run),
            "written": written,
            "summary": result.batch.summary(),
            "unmapped": result.unmapped,
            "counters": dict(result.counters),
            "notes": result.notes,
            "llm": report,
            "build_seconds": round(build_seconds, 1),
            "elapsed_seconds": round(elapsed, 1),
        }
    )
    progress["runs"] = progress["runs"][-30:]
    if options.use_llm and not args.dry_run:
        done = set(progress.get("llm_done_sources") or [])
        done.update(result.sources)
        progress["llm_done_sources"] = sorted(done)
        progress["llm_spent_usd"] = round(
            float(progress.get("llm_spent_usd") or 0.0) + report.get("spent_usd", 0.0), 4
        )
        # 캐시 히트는 provider 를 부르지 않았으니 새 호출로 세지 않는다.
        fresh = max(0, report.get("calls", 0) - report.get("cache_hits", 0))
        progress["llm_calls"] = int(progress.get("llm_calls") or 0) + fresh
    progress["last_unmapped"] = result.unmapped
    save_progress(progress)

    print_build(result, written, elapsed, report)
    return 0


def print_build(result: BuildResult, written: dict[str, int], elapsed: float, report: dict) -> None:
    summary = result.batch.summary()
    print(f"\n소스 {len(result.sources)}개 · {elapsed:.1f}초")
    print(
        f"빌드 노드 {summary['node_total']:,} · 엣지 {summary['edge_total']:,} "
        f"→ 적재 노드 {written['nodes']:,} · 엣지 {written['edges']:,}"
    )
    print(table("라벨별 노드", sorted_desc(summary["nodes_by_label"]), headers=("라벨", "노드 수")))
    print(table("관계 타입별 엣지", sorted_desc(summary["edges_by_type"]), headers=("관계", "엣지 수")))
    print(table("상태별 Claim", sorted_desc(summary["claims_by_status"]), headers=("상태", "Claim 수")))
    print(f"  lane=critical Claim: {summary['critical_claims']:,}")

    unmapped_rows = [(key, len(value)) for key, value in sorted(result.unmapped.items())]
    print(table("unmapped (표현 종류 수)", unmapped_rows, headers=("구분", "종류 수")))

    if report:
        print(
            f"\n[LLM] 호출 {report['calls']} (캐시 히트 {report['cache_hits']}) · "
            f"비용 ${report['spent_usd']:.4f} / 상한 ${report['max_usd']:.2f} "
            f"(매핑 몫 ${report['reserved_usd']:.2f}) · "
            f"T2 탈락 {report['dropped_by_t2']} · 스키마 실패 {report['schema_failures']}"
        )
        for target, count in sorted(report["skipped"].items()):
            print(f"  못 한 것: {target} 발췌 {count}건 (예산·호출 상한)")
        for note in report["notes"]:
            print(f"  note: {note}")

    for note in result.notes:
        print(f"  경고: {note}")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def cmd_stats(_args: argparse.Namespace) -> int:
    with writable_graph() as graph:
        stats = GraphWriter(graph).stats()

    if not stats["nodes_by_label"]:
        print("그래프가 비어 있다. `ingest.py run --no-llm` 을 먼저 돌려라.")
        return 1

    print(f"\n노드 {stats['node_total']:,} · 엣지 {stats['edge_total']:,}")
    print(table("라벨별 노드", sorted_desc(stats["nodes_by_label"]), headers=("라벨", "노드 수")))
    print(table("관계 타입별 엣지", sorted_desc(stats["edges_by_type"]), headers=("관계", "엣지 수")))
    print(table("상태별 Claim", sorted_desc(stats["claims_by_status"]), headers=("상태", "Claim 수")))
    print(table("lane별 Claim", sorted_desc(stats["claims_by_lane"]), headers=("lane", "Claim 수")))
    print(f"  lane=critical Claim: {stats['claims_by_lane'].get('critical', 0):,}")

    progress = load_progress()
    unmapped = progress.get("last_unmapped") or {}
    rows = [(key, sum(value.values())) for key, value in sorted(unmapped.items())]
    print(
        table("unmapped (마지막 run 의 발생 건수)", rows, headers=("구분", "건수"))
        if rows
        else "\n[unmapped] 실행 기록이 없다.\n"
    )
    print(f"  canonical 이 아닌 Need 노드: {stats['unmapped_needs']:,}")

    runs = progress.get("runs") or []
    if runs:
        last = runs[-1]
        scope = "전체" if not last.get("only") else f"--only {' '.join(last['only'])}"
        print(
            f"\n마지막 run: {last['at']} · 소스 {len(last['sources'])}개({scope}) · "
            f"LLM {'사용' if last['use_llm'] else '미사용'}"
        )
        skipped = (last.get("llm") or {}).get("skipped") or {}
        for target, count in sorted(skipped.items()):
            print(f"  예산 상한으로 못 한 LLM 추출: {target} 발췌 {count}건")
        print(
            f"누적 LLM: 신규 호출 {progress.get('llm_calls', 0)}회 · "
            f"${float(progress.get('llm_spent_usd') or 0.0):.4f} (캐시 재생은 세지 않는다)"
        )

    print_llm_coverage(progress)
    return 0


def print_llm_coverage(progress: dict[str, Any]) -> None:
    """LLM 추출을 한 번도 안 돌린 소스를 이름으로 찍는다. 빠뜨린 것을 눈에 보이게 둔다."""
    from ingestion.pipeline.settings import PARSED_DIR

    all_sources = sorted(
        path.name[: -len(".source.json")] for path in PARSED_DIR.glob("*.source.json")
    )
    done = set(progress.get("llm_done_sources") or [])
    pending = [sid for sid in all_sources if sid not in done]
    print(f"\n[LLM 추출 커버리지] 완료 {len(done)} / 전체 {len(all_sources)}")
    if pending:
        print("  아직 LLM 추출을 돌리지 않은 소스:")
        for sid in pending:
            print(f"    - {sid}")


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        answer = input("이 파이프라인이 만든 노드를 전부 지운다. 계속하려면 'yes': ")
        if answer.strip() != "yes":
            print("취소했다.")
            return 1
    with writable_graph() as graph:
        removed = GraphWriter(graph).reset()
    print(f"노드 {removed:,}개를 지웠다. (pipeline_run_id 가 없는 노드는 건드리지 않았다)")
    return 0


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest.py", description="Snapshot Ingest CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="파싱 결과를 그래프에 적재한다")
    run.add_argument("--only", nargs="*", metavar="SOURCE_ID", help="특정 소스만 적재")
    run.add_argument("--no-llm", action="store_true", help="결정적 직행만 한다")
    run.add_argument("--budget", type=float, default=32.0, metavar="USD", help="LLM 비용 상한(sonnet 기준)")
    run.add_argument("--max-calls", type=int, default=400, help="LLM 호출 수 상한")
    run.add_argument("--batch-size", type=int, default=10, help="1콜에 묶을 발췌 수")
    run.add_argument(
        "--mapping-usd",
        type=float,
        default=6.0,
        metavar="USD",
        help="매핑 단계 몫으로 떼어 둘 금액. 추출이 여기까지 쓰지 못한다",
    )
    run.add_argument("--run-id", default="ingest", help="pipeline_run_id 로 남길 값")
    run.add_argument("--dry-run", action="store_true", help="빌드·검증만 하고 쓰지 않는다")
    run.set_defaults(func=cmd_run)

    stats = sub.add_parser("stats", help="적재된 그래프 통계를 표로 본다")
    stats.set_defaults(func=cmd_stats)

    reset = sub.add_parser("reset", help="이 파이프라인이 만든 노드를 지운다")
    reset.add_argument("--yes", action="store_true", help="확인 없이 지운다")
    reset.set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
