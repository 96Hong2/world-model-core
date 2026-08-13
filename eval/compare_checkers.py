"""같은 답변 묶음을 '교정 전 채점기'와 '교정 후 채점기' 두 벌로 채점한다.

`eval/CHECKER-CHANGES.md` 의 「점수 두 벌」 표를 만든 도구다.
채점 범위 교정으로 무엇이 올라갔는지 숨기지 않기 위한 증거이지, 상시 실행하는 하네스가 아니다.

    python -m eval.compare_checkers data/cache/gate_1a_answers.json
    python -m eval.compare_checkers <fixture 또는 스냅샷>.json

교정 전 채점기는 손댄 두 함수(check_evidence_match / check_keyfact)를 교정 전 모습 그대로
여기에 복제해 둔 것이다. 나머지 6종은 바꾸지 않았으므로 현재 모듈 것을 그대로 쓴다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from eval import checkers as C
from eval.checkers import CheckResult
from eval.golden_loader import Golden, contains, load_golden


# ---------------------------------------------------------------------------
# 교정 전 원본 (GATE-1A-REPORT R6 시점 코드 그대로)
# ---------------------------------------------------------------------------


def before_evidence_match(
    answer: dict[str, Any], question: dict[str, Any], golden: Golden
) -> CheckResult:
    reasons: list[str] = []
    evidence = answer.get("evidence") or []
    ids = [e.get("evidence_id") for e in evidence]

    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        reasons.append(f"evidence_id 중복: {sorted(dupes)}")

    id_set = set(ids)
    for cit in answer.get("citations") or []:
        if cit.get("evidence_id") not in id_set:
            reasons.append(
                f"각주 [{cit.get('marker')}] 가 없는 evidence_id 를 가리킨다: {cit.get('evidence_id')}"
            )

    minimum = question.get("expected_evidence_min", 1)
    if len(evidence) < minimum:
        reasons.append(f"evidence 부족: 기대 최소 {minimum}건 / 실제 {len(evidence)}건")

    snippets = "\n".join(e.get("snippet") or "" for e in evidence)
    for item in question.get("must_include") or []:
        quote = item.get("source_quote")
        if quote and not contains(snippets, quote):
            reasons.append(f"기대 발췌가 evidence 에 없다: {quote!r} ({item.get('locator')})")

    for e in evidence:
        if not e.get("locator"):
            reasons.append(f"locator 없는 evidence: {e.get('evidence_id')}")

    return CheckResult("evidence_match", not reasons, reasons)


def before_keyfact(
    answer: dict[str, Any], question: dict[str, Any], golden: Golden
) -> CheckResult:
    reasons: list[str] = []
    prose = C.answer_prose(answer)
    for item in question.get("must_include") or []:
        fact = item.get("fact")
        if fact and not contains(prose, fact):
            reasons.append(f"답변에 없는 필수 사실: {fact!r}")

    payload = C.full_payload_text(answer)
    for banned in question.get("must_not_include") or []:
        if contains(payload, banned):
            reasons.append(f"나오면 안 되는 표현이 있다: {banned!r}")

    return CheckResult("keyfact", not reasons, reasons)


BEFORE_CHECKS = (
    C.check_route,
    before_evidence_match,
    before_keyfact,
    C.check_masking,
    C.check_traceability,
    C.check_gap_verdict,
    C.check_subgraph,
    C.check_conflict,
)


# ---------------------------------------------------------------------------


def load_pairs(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """runner 픽스처 형식과 gate_1a_answers.json 형식을 둘 다 읽는다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    answers = data.get("answers", data)
    if isinstance(answers, list):
        return [(e["question_id"], e["answer"]) for e in answers]
    if isinstance(answers, dict):
        return list(answers.items())
    raise ValueError(f"모르는 형식: {path}")


def failed_by_question(golden, pairs, checks) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for qid, answer in pairs:
        question = golden.by_id(qid)
        results = [fn(answer, question, golden) for fn in checks]
        out[qid] = sorted(r.name for r in results if not r.passed)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(__doc__)
        return 2

    path = Path(argv[0])
    golden = load_golden()
    pairs = load_pairs(path)

    before = failed_by_question(golden, pairs, BEFORE_CHECKS)
    after = failed_by_question(golden, pairs, C.ALL_CHECKS)

    n_before = sum(1 for v in before.values() if not v)
    n_after = sum(1 for v in after.values() if not v)

    print(f"입력: {path}  ({len(pairs)}문항)")
    print(f"교정 전 채점기 : {n_before}/{len(pairs)} 통과")
    print(f"교정 후 채점기 : {n_after}/{len(pairs)} 통과")
    print()
    print(f"{'문항':<9}{'교정 전':<34}{'교정 후':<34}변화")
    for qid, _ in pairs:
        b, a = before[qid], after[qid]
        if not b and a:
            note = "  ← 교정으로 실패로 바뀜(!)"
        elif b and not a:
            note = "  ← 교정으로 PASS"
        elif b != a:
            note = "  ← 깨진 채점기만 줄어듦"
        else:
            note = ""
        print(f"{qid:<9}{(', '.join(b) or 'PASS'):<34}{(', '.join(a) or 'PASS'):<34}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
