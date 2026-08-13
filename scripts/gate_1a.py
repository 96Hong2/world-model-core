#!/usr/bin/env python
"""Gate 1A 판정기.

REVISED-MASTER-PLAN §5 "1A 완료 정의(Gate 1A)" 8항목 중 **기계로 잴 수 있는 것을 실제로 잰다.**
게이트를 완화하지 않는다. 재지 못한 항목은 통과로 세지 않고 `미측정`으로 남긴다.

    gate_1a.py                     # 8항목 측정 (UI 클릭 경로·재적재는 미측정으로 남는다)
    gate_1a.py --ui                # 6번을 브라우저로 실제 재현(vite dev + Playwright 필요)
    gate_1a.py --reingest          # 7번을 실제 재적재로 측정(그래프를 건드린다)
    gate_1a.py --answers PATH      # 저장해 둔 답변 묶음으로 채점(재질의 없음)

exit code
    0  8항목 전부 PASS
    1  하나 이상 미달 또는 미측정
    2  실행 자체가 불가(서버 · 그래프 · 파일 없음)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS = "PASS"
FAIL = "FAIL"
UNMEASURED = "미측정"

DEFAULT_ENDPOINT = "http://localhost:8099/ask"
DEFAULT_ANSWERS = ROOT / "data" / "cache" / "gate_1a_answers.json"

# §5 1번의 하한값. 이 숫자는 Master Plan 이 정한 것이라 여기서 낮추지 않는다.
MIN_BUSINESS_DOMAIN = 15
MIN_ACCOUNT = 50
MIN_FEATURE = 300

# §6 A2 의 파서 6계열(a~f). data/parsed 의 `parser` 값 앞부분으로 계열을 판정한다.
PARSER_FAMILIES: dict[str, tuple[str, ...]] = {
    "a 기능맵 xlsx": ("xlsx_featuremap",),
    "b 영업 xlsx": ("xlsx_sales_activity", "xlsx_weekly_plan", "xlsx_pain_registry"),
    "c BD Overview": ("xlsx_bd_registry",),
    "d 문서(제안서·매뉴얼 등)": ("doc_",),
    "e Slack 쓰레드 덤프": ("slack_",),
    "f 코드·테스트 시드": ("code_asset",),
}

DEMO_QUESTION_IDS = ("GQ-D1", "GQ-D2", "GQ-D3", "GQ-D4", "GQ-D5")
RARE_CRITICAL_ID = "GQ-D6"


@dataclass
class Item:
    number: int
    title: str
    status: str = UNMEASURED
    measures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self, line: str) -> None:
        self.measures.append(line)

    def fail(self, line: str) -> None:
        self.status = FAIL
        self.measures.append(line)

    def settle(self) -> None:
        """FAIL 이 한 번도 안 났고 측정을 했으면 PASS."""
        if self.status == UNMEASURED and self.measures:
            self.status = PASS


# ---------------------------------------------------------------------------
# 입력 수집
# ---------------------------------------------------------------------------


def ask(endpoint: str, question: str, timeout: int = 900) -> dict[str, Any]:
    import urllib.request

    payload = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect_answers(endpoint: str, golden, cache: pathlib.Path, refresh: bool) -> dict[str, Any]:
    """골든 20문항의 답변을 모은다. 캐시가 있으면 재질의하지 않는다."""
    if cache.exists() and not refresh:
        stored = json.loads(cache.read_text(encoding="utf-8"))
        if sorted(stored.get("answers", {})) == sorted(q["id"] for q in golden.questions):
            return stored
    answers: dict[str, Any] = {}
    started = time.time()
    for q in golden.questions:
        answers[q["id"]] = ask(endpoint, q["question"])
    stored = {
        "endpoint": endpoint,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(time.time() - started, 1),
        "answers": answers,
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
    return stored


def graph_counts() -> dict[str, int]:
    from graph.connection import read_only_graph

    with read_only_graph() as graph, graph.session() as session:
        rows = session.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c")
        counts = {r["l"]: r["c"] for r in rows}
        totals = session.run(
            "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS edges"
        ).single()
        counts["__nodes__"] = totals["nodes"]
        counts["__edges__"] = totals["edges"]
    return counts


def parsed_sources() -> list[dict[str, Any]]:
    out = []
    for path in sorted((ROOT / "data" / "parsed").glob("*.source.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


# ---------------------------------------------------------------------------
# 1. Source 6계열 ingest + 그래프 하한
# ---------------------------------------------------------------------------


def item_1(counts: dict[str, int], sources: list[dict[str, Any]]) -> Item:
    item = Item(1, "Source 6계열 ingest 완료 + BD 15+ · Account 50+ · Feature 300+")

    by_family: dict[str, int] = {name: 0 for name in PARSER_FAMILIES}
    unknown: list[str] = []
    for src in sources:
        parser = src.get("parser") or ""
        for name, prefixes in PARSER_FAMILIES.items():
            if any(parser.startswith(p) for p in prefixes):
                by_family[name] += 1
                break
        else:
            unknown.append(parser)

    for name, n in by_family.items():
        if n:
            item.ok(f"파서 계열 {name}: 소스 {n}개")
        else:
            item.fail(f"파서 계열 {name}: 소스 0개 (계열 미가동)")
    if unknown:
        item.notes.append(f"계열 미분류 parser 값 {len(set(unknown))}종: {sorted(set(unknown))[:5]}")

    item.ok(f"등록 Source 총계: {len(sources)}개 (그래프 Source 노드 {counts.get('Source', 0)}개)")
    if counts.get("Source", 0) != len(sources):
        item.notes.append("data/parsed 소스 수와 그래프 Source 노드 수가 다르다")

    for label, minimum in (
        ("BusinessDomain", MIN_BUSINESS_DOMAIN),
        ("Account", MIN_ACCOUNT),
        ("Feature", MIN_FEATURE),
    ):
        n = counts.get(label, 0)
        line = f"{label}: {n}개 (하한 {minimum})"
        item.ok(line) if n >= minimum else item.fail(line + " — 미달")

    item.ok(f"그래프 총계: 노드 {counts['__nodes__']:,} · 엣지 {counts['__edges__']:,}")
    item.settle()
    return item


# ---------------------------------------------------------------------------
# 2. 데모 질문 5개 Answer JSON + evidence ≥1 + subgraph
# ---------------------------------------------------------------------------


def item_2(answers: dict[str, Any]) -> Item:
    from api.contracts import validate_answer

    item = Item(2, "데모 질문 5개가 Answer JSON 으로 응답 + evidence ≥1 + subgraph 동봉")
    for qid in DEMO_QUESTION_IDS:
        answer = answers.get(qid)
        if answer is None:
            item.fail(f"{qid}: 응답 없음")
            continue
        problems = validate_answer(answer)
        n_ev = len(answer.get("evidence") or [])
        n_nodes = len((answer.get("subgraph") or {}).get("nodes") or [])
        n_edges = len((answer.get("subgraph") or {}).get("edges") or [])
        line = f"{qid}: 계약 위반 {len(problems)}건 · evidence {n_ev}건 · subgraph 노드 {n_nodes}·엣지 {n_edges}"
        if problems or n_ev < 1 or n_nodes < 1:
            item.fail(line)
            for p in problems[:3]:
                item.notes.append(f"{qid} 계약 위반: {p}")
        else:
            item.ok(line)
    item.settle()
    return item


# ---------------------------------------------------------------------------
# 3. traceability 100% + 무근거 숫자·고유명사 0
# ---------------------------------------------------------------------------


def item_3(answers: dict[str, Any], golden) -> Item:
    from eval.checkers import check_traceability

    item = Item(3, "evidence traceability 100%(깨진 인용 0) + 무근거 숫자·고유명사 0건")

    broken = 0
    total_citations = 0
    for qid, answer in answers.items():
        ids = {e.get("evidence_id") for e in answer.get("evidence") or []}
        for cit in answer.get("citations") or []:
            total_citations += 1
            if cit.get("evidence_id") not in ids:
                broken += 1
                item.notes.append(f"{qid}: 각주 [{cit.get('marker')}] 가 없는 evidence 를 가리킨다")
    line = f"인용 {total_citations}건 중 깨진 인용 {broken}건"
    item.ok(line) if broken == 0 else item.fail(line)

    violations = 0
    for qid, answer in answers.items():
        result = check_traceability(answer, golden.by_id(qid), golden)
        if not result.passed:
            violations += len(result.reasons)
            for reason in result.reasons[:3]:
                item.notes.append(f"{qid}: {reason}")
    line = f"무근거 숫자·고유명사 {violations}건 (문항 {len(answers)}개 전수)"
    item.ok(line) if violations == 0 else item.fail(line)

    item.settle()
    return item


# ---------------------------------------------------------------------------
# 4. rare-critical 1회 등장 정보가 답변에 포함
# ---------------------------------------------------------------------------


def item_4(answers: dict[str, Any], golden) -> Item:
    from eval.checkers import answer_prose, evidence_text
    from eval.golden_loader import contains

    item = Item(4, f"rare-critical({RARE_CRITICAL_ID}) 1회 등장 정보가 답변에 포함")
    answer = answers.get(RARE_CRITICAL_ID)
    if answer is None:
        item.fail(f"{RARE_CRITICAL_ID}: 응답 없음")
        item.settle()
        return item

    question = golden.by_id(RARE_CRITICAL_ID)
    prose = answer_prose(answer)
    support = evidence_text(answer)  # evidence + raw_signals("추가 원문 근거")

    # §5 4번은 "Graph 미반영이어도 fulltext 검색으로 추가 원문 근거에 노출"을 통과로 규정한다.
    # 본문 포함(더 엄격한 골든 keyfact 기준)은 따로 세어 함께 남긴다.
    missing_anywhere: list[str] = []
    missing_prose: list[str] = []
    for spec in question.get("must_include") or []:
        fact = spec.get("fact")
        quote = spec.get("source_quote")
        if fact and not contains(prose, fact):
            missing_prose.append(fact)
        found = (fact and contains(prose, fact)) or (quote and contains(support, quote))
        if not found:
            missing_anywhere.append(f"{fact!r} / 발췌 {quote!r}")

    line = f"필수 사실 {len(question.get('must_include') or [])}개 중 답변·추가 원문 근거 어디에도 없는 것 {len(missing_anywhere)}개"
    item.ok(line) if not missing_anywhere else item.fail(line)
    for m in missing_anywhere:
        item.notes.append(f"{RARE_CRITICAL_ID} 누락: {m}")

    n_raw = len(answer.get("raw_signals") or [])
    item.ok(f"참고: 답변 본문에 없는 필수 사실 {len(missing_prose)}개(골든 keyfact 기준) · raw_signals {n_raw}건")
    item.settle()
    return item


# ---------------------------------------------------------------------------
# 5. 마스킹
# ---------------------------------------------------------------------------


def item_5(answers: dict[str, Any]) -> Item:
    from eval.checkers import find_pii, full_payload_text

    item = Item(5, "답변·evidence·subgraph 어디에도 전화번호 정규식 매칭 0건")
    hits = 0
    for qid, answer in answers.items():
        found = find_pii(full_payload_text(answer))
        for det, value in found:
            hits += 1
            item.notes.append(f"{qid}: {det} — {value!r}")
    line = f"문항 {len(answers)}개 전수 PII 매칭 {hits}건"
    item.ok(line) if hits == 0 else item.fail(line)
    item.settle()
    return item


# ---------------------------------------------------------------------------
# 6. UI 3클릭 경로
# ---------------------------------------------------------------------------


def item_6(answers: dict[str, Any], run_ui: bool) -> Item:
    item = Item(6, "UI 질문→답변→각주→노드 하이라이트→노드 클릭→Drawer→원본 위치 (3클릭)")

    # (a) 답변이 3클릭 경로의 재료를 갖고 있는가 — 각주·인용 노드가 없으면 클릭할 것이 없다.
    #     게이트가 요구하는 것은 데모 경로가 동작하는가이므로 판정은 데모 질문으로 한다.
    #     나머지 문항의 각주 부재는 품질 결함으로 함께 남긴다(게이트 항목은 아니다).
    demo = (*DEMO_QUESTION_IDS, RARE_CRITICAL_ID)
    demo_without = [qid for qid in demo if not ((answers.get(qid) or {}).get("citations") or [])]
    line = f"데모 질문 {len(demo)}개 중 각주가 없는 문항 {len(demo_without)}개"
    item.ok(line) if not demo_without else item.fail(line + f" — {demo_without}")

    all_without = [qid for qid, a in answers.items() if not (a.get("citations") or [])]
    if all_without:
        item.notes.append(f"게이트 밖 품질 결함: 각주 0개인 문항 {len(all_without)}개 — {all_without}")

    unmarked = 0
    for qid, answer in answers.items():
        marked = {m for n in (answer.get("subgraph") or {}).get("nodes") or [] for m in (n.get("citation_markers") or [])}
        for cit in answer.get("citations") or []:
            if cit.get("marker") not in marked:
                unmarked += 1
    line = f"대응 노드가 subgraph 에 없는 각주 {unmarked}건"
    item.ok(line) if unmarked == 0 else item.fail(line)

    # (b) Drawer 의 "원본 위치"는 web/src/lib/source-registry.ts 를 본다.
    #     답변에 실제로 실린 source_id 가 이 표에 없으면 파일 경로 줄이 통째로 안 그려진다.
    registry_path = ROOT / "web" / "src" / "lib" / "source-registry.ts"
    if not registry_path.exists():
        item.fail("web/src/lib/source-registry.ts 가 없다 — Drawer 가 원본 위치를 못 그린다")
    else:
        text = registry_path.read_text(encoding="utf-8")
        known = set(re.findall(r"[\"'](src_[A-Za-z0-9_]+)[\"']", text))
        used: set[str] = set()
        for answer in answers.values():
            for e in (answer.get("evidence") or []) + (answer.get("raw_signals") or []):
                if e.get("source_id"):
                    used.add(e["source_id"])
        uncovered = sorted(used - known)
        line = (
            f"답변에 실린 자료 {len(used)}종 중 원본 위치 표에 없는 것 {len(uncovered)}종 "
            f"(표 등재 {len(known)}종)"
        )
        item.ok(line) if not uncovered else item.fail(line)
        for sid in uncovered[:5]:
            item.notes.append(f"원본 위치 미등재: {sid}")

    # (c) 실제 클릭 재현
    if run_ui:
        proc = subprocess.run(
            ["node", "scripts/demo-shots.mjs"],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            timeout=1800,
        )
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        if proc.returncode == 0:
            item.ok(f"브라우저 재현 통과 (demo-shots.mjs) — {' / '.join(tail)}")
        else:
            item.fail(f"브라우저 재현 실패 (demo-shots.mjs, exit {proc.returncode})")
            item.notes.append((proc.stderr or proc.stdout)[-400:])
    else:
        item.notes.append("클릭 경로 실측은 --ui 로만 한다(vite dev :5273 + Playwright 필요)")
        shots = sorted((ROOT / "web" / "screenshots").glob("live-*.png"))
        if shots:
            item.notes.append(f"직전 재현 증적 {len(shots)}장: {[p.name for p in shots]}")
        item.status = UNMEASURED if item.status != FAIL else FAIL
        return item

    item.settle()
    return item


# ---------------------------------------------------------------------------
# 7. 2회 ingest 불변
# ---------------------------------------------------------------------------


def item_7(before: dict[str, int], run_reingest: bool) -> Item:
    item = Item(7, "동일 입력 2회 ingest 시 노드·엣지 수 불변")
    if not run_reingest:
        item.notes.append("--reingest 를 줘야 실제 재적재로 잰다(그래프를 건드리므로 기본값은 미측정)")
        return item

    proc = subprocess.run(
        [sys.executable, "scripts/ingest.py", "run", "--no-llm"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    if proc.returncode != 0:
        item.fail(f"재적재 실패 (exit {proc.returncode})")
        item.notes.append((proc.stderr or proc.stdout)[-400:])
        return item

    after = graph_counts()
    for key, label in (("__nodes__", "노드"), ("__edges__", "엣지")):
        line = f"{label}: {before[key]:,} → {after[key]:,}"
        item.ok(line) if before[key] == after[key] else item.fail(line + " — 변했다")
    item.settle()
    return item


# ---------------------------------------------------------------------------
# 8. eval 하네스 + mutation 셀프테스트
# ---------------------------------------------------------------------------


def item_8(endpoint: str) -> Item:
    item = Item(8, "eval 하네스 실행 + mutation 셀프테스트 통과")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_eval_harness.py", "-q", "-k", "mutation"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    line = f"mutation 셀프테스트: {tail[0]}"
    item.ok(line) if proc.returncode == 0 else item.fail(line)

    proc = subprocess.run(
        [sys.executable, "-m", "eval.runner", "--answer-endpoint", endpoint, "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    summary = [l for l in (proc.stdout or "").splitlines() if l.startswith("문항 ")]
    line = f"하네스 실행: {summary[0] if summary else '요약 줄 없음'}"
    # 하네스가 도는 것이 8번의 요구다. 통과율은 게이트 항목이 아니라 품질 지표라 여기서 세지 않는다.
    if proc.returncode == 2 or not summary:
        item.fail(line + " — 실행 불가")
    else:
        item.ok(line)
        item.notes.append("통과율은 Gate 항목이 아니다. GATE-1A-REPORT.md 의 품질 지표로 따로 본다")

    item.settle()
    return item


# ---------------------------------------------------------------------------


def render(items: list[Item]) -> str:
    mark = {PASS: "PASS", FAIL: "FAIL", UNMEASURED: "미측정"}
    out: list[str] = ["", "=" * 78, "Gate 1A 판정 (REVISED-MASTER-PLAN §5)", "=" * 78]
    for item in items:
        out.append("")
        out.append(f"[{mark[item.status]:<4}] {item.number}. {item.title}")
        for m in item.measures:
            out.append(f"        · {m}")
        for n in item.notes:
            out.append(f"        ! {n}")
    passed = sum(1 for i in items if i.status == PASS)
    failed = sum(1 for i in items if i.status == FAIL)
    unmeasured = sum(1 for i in items if i.status == UNMEASURED)
    out += [
        "",
        "-" * 78,
        f"통과 {passed} · 미달 {failed} · 미측정 {unmeasured} / 8항목",
        f"종합: {'GATE 1A 통과' if passed == len(items) else 'GATE 1A 미달'}",
        "=" * 78,
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate_1a.py", description="Gate 1A 8항목 측정")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--answers", default=str(DEFAULT_ANSWERS), help="답변 묶음 캐시 경로")
    parser.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 질의한다")
    parser.add_argument("--ui", action="store_true", help="6번을 브라우저로 실제 재현한다")
    parser.add_argument("--reingest", action="store_true", help="7번을 실제 재적재로 잰다")
    parser.add_argument("--json", default=None, help="판정 결과를 JSON 으로도 저장한다")
    args = parser.parse_args(argv)

    from eval.golden_loader import load_golden

    try:
        golden = load_golden()
        counts = graph_counts()
        sources = parsed_sources()
        stored = collect_answers(args.endpoint, golden, pathlib.Path(args.answers), args.refresh)
    except Exception as exc:  # noqa: BLE001 - 게이트는 원인을 그대로 보여준다
        print(f"실행 불가: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    answers = stored["answers"]
    items = [
        item_1(counts, sources),
        item_2(answers),
        item_3(answers, golden),
        item_4(answers, golden),
        item_5(answers),
        item_6(answers, args.ui),
        item_7(counts, args.reingest),
        item_8(args.endpoint),
    ]

    print(render(items))
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(
                [
                    {
                        "number": i.number,
                        "title": i.title,
                        "status": i.status,
                        "measures": i.measures,
                        "notes": i.notes,
                    }
                    for i in items
                ],
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    return 0 if all(i.status == PASS for i in items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
