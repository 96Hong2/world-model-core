"""Golden 실행기.

    python -m eval.runner --fixture eval/fixtures/normal_answer.json
    python -m eval.runner --answer-endpoint http://localhost:8000/ask

fixture 모드가 먼저다. 답변 API 는 A6 산출물이라 아직 없고, 채점기 자체를 먼저 검증해야 한다.
endpoint 모드는 인터페이스만 두고 A6 완료 후 연결한다.

exit code
    0  전 문항 통과
    1  하나 이상 실패 (게이트 판정용)
    2  실행 자체가 불가 (파일 없음 · 알 수 없는 문항 id 등)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.checkers import CheckResult, run_checks
from eval.golden_loader import Golden, load_golden

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class QuestionOutcome:
    question_id: str
    question_type: str
    results: list[CheckResult]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed_names(self) -> list[str]:
        return [r.name for r in self.results if not r.passed]


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------


def load_fixture(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """픽스처 파일에서 (question_id, Answer JSON) 목록을 꺼낸다.

    단건:  {"question_id": "...", "answer": {...}}
    묶음:  {"answers": [{"question_id": "...", "answer": {...}}, ...]}
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["answers"] if "answers" in data else [data]
    out: list[tuple[str, dict[str, Any]]] = []
    for entry in entries:
        out.append((entry["question_id"], entry["answer"]))
    return out


def fetch_from_endpoint(endpoint: str, question: dict[str, Any]) -> dict[str, Any]:
    """A6 의 POST /ask 를 호출한다. A6 완료 전에는 쓰지 않는다."""
    import urllib.request

    payload = json.dumps({"question": question["question"]}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # 합성 1회가 실측 95~168초다(캐시 미스 시). 120초면 정상 답변을 타임아웃으로 잃는다.
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------


def score(golden: Golden, pairs: list[tuple[str, dict[str, Any]]]) -> list[QuestionOutcome]:
    outcomes: list[QuestionOutcome] = []
    for qid, answer in pairs:
        question = golden.by_id(qid)
        outcomes.append(
            QuestionOutcome(
                question_id=qid,
                question_type=question.get("type", "?"),
                results=run_checks(answer, question, golden),
            )
        )
    return outcomes


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------


def render(outcomes: list[QuestionOutcome], verbose: bool = True) -> str:
    lines: list[str] = []
    for oc in outcomes:
        mark = "PASS" if oc.passed else "FAIL"
        lines.append(f"[{mark}] {oc.question_id} ({oc.question_type})")
        if verbose:
            for r in oc.results:
                if r.passed and not r.reasons:
                    lines.append(f"       ok    {r.name}")
                elif r.passed:
                    note = "skip" if r.skipped else "ok"
                    lines.append(f"       {note}  {r.name} — {'; '.join(r.reasons)}")
                else:
                    lines.append(f"       FAIL  {r.name}")
                    for reason in r.reasons:
                        lines.append(f"             · {reason}")

    total = len(outcomes)
    passed = sum(1 for oc in outcomes if oc.passed)
    lines.append("")
    lines.append("=" * 68)
    lines.append(f"문항 {total}건 · 통과 {passed} · 실패 {total - passed}")
    if total - passed:
        lines.append("")
        lines.append("실패 요약")
        lines.append(f"{'문항':<10} {'유형':<14} 깨진 채점기")
        for oc in outcomes:
            if not oc.passed:
                lines.append(
                    f"{oc.question_id:<10} {oc.question_type:<14} {', '.join(oc.failed_names)}"
                )
    lines.append("=" * 68)
    return "\n".join(lines)


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.runner", description="Golden 채점 실행기")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", help="Answer JSON 픽스처 경로(단건 또는 묶음)")
    src.add_argument("--answer-endpoint", help="POST /ask 엔드포인트 (A6 완료 후)")
    parser.add_argument("--golden", default=None, help="golden.yaml 경로")
    parser.add_argument("--only", nargs="*", help="채점할 문항 id 를 좁힌다")
    parser.add_argument("--quiet", action="store_true", help="채점기별 상세를 접는다")
    parser.add_argument(
        "--save",
        help="받은 답변 묶음을 이 경로에 저장한다. 한 번 받은 payload 로 여러 채점기를 "
        "돌려 비교할 때 쓴다(답변을 두 번 생성하면 채점기 차이와 답변 변동이 섞인다)",
    )
    args = parser.parse_args(argv)

    golden = load_golden(args.golden) if args.golden else load_golden()

    fetch_errors: list[tuple[str, str]] = []
    try:
        if args.fixture:
            path = Path(args.fixture)
            if not path.is_absolute():
                path = REPO_ROOT / path
            pairs = load_fixture(path)
        else:
            targets = [
                q for q in golden.questions if not args.only or q["id"] in args.only
            ]
            # 한 문항이 터져도 나머지를 버리지 않는다. 20문항을 다시 받으려면 LLM 비용이
            # 다시 들기 때문에, 받아 둔 것은 --save 로 남겨 두 채점기 비교에 쓸 수 있어야 한다.
            pairs = []
            for q in targets:
                try:
                    pairs.append((q["id"], fetch_from_endpoint(args.answer_endpoint, q)))
                except Exception as exc:  # noqa: BLE001 - 원인 문자열을 그대로 보고한다
                    fetch_errors.append((q["id"], f"{type(exc).__name__}: {exc}"))
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(f"실행 불가: {exc}", file=sys.stderr)
        return 2

    for qid, err in fetch_errors:
        print(f"답변 못 받음 {qid}: {err}", file=sys.stderr)

    if args.only:
        pairs = [(qid, ans) for qid, ans in pairs if qid in args.only]

    if args.save:
        out = Path(args.save)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"answers": [{"question_id": q, "answer": a} for q, a in pairs]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"답변 묶음 저장: {out} ({len(pairs)}문항)", file=sys.stderr)

    try:
        outcomes = score(golden, pairs)
    except KeyError as exc:
        print(f"실행 불가: {exc}", file=sys.stderr)
        return 2

    print(render(outcomes, verbose=not args.quiet))
    if fetch_errors:
        # 일부 문항을 아예 못 받았다. 받은 것이 전부 통과했더라도 통과로 보고하지 않는다.
        print(
            f"채점 불완전: {len(fetch_errors)}문항 미수신 "
            f"({', '.join(q for q, _ in fetch_errors)})",
            file=sys.stderr,
        )
        return 2
    return 0 if all(oc.passed for oc in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
