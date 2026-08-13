"""골든 문항을 한 번씩 돌려 LLM 캐시를 채운다.

    .venv/bin/python -m api.warmup --endpoint http://localhost:8099/ask

왜 필요한가. 실측 합성 한 번이 95~168초다. eval 하네스의 HTTP 타임아웃은 120초라 첫 호출이
그대로 채점에 들어가면 타임아웃으로 죽는다. content-hash 캐시는 이미 있으니, 채점 전에
한 번 돌려 두면 두 번째 호출부터는 비용 0 · 즉시 응답이다.

호출 사이에 결과 한 줄을 찍는다. 20문항을 조용히 도는 동안 무엇이 느린지 안 보이면
멈춘 것인지 도는 것인지 알 수 없다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from eval.golden_loader import load_golden

DEFAULT_ENDPOINT = "http://localhost:8099/ask"
DEFAULT_TIMEOUT = 600.0


def ask(endpoint: str, question: str, timeout: float) -> dict:
    payload = json.dumps({"question": question}).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="api.warmup")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--only", nargs="*", help="문항 id 를 좁힌다")
    args = parser.parse_args(argv)

    golden = load_golden()
    targets = [q for q in golden.questions if not args.only or q["id"] in args.only]

    failures = 0
    for index, question in enumerate(targets, start=1):
        started = time.monotonic()
        try:
            answer = ask(args.endpoint, question["question"], args.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            failures += 1
            print(f"[{index:>2}/{len(targets)}] {question['id']:<7} 실패: {exc}", flush=True)
            continue
        elapsed = time.monotonic() - started
        print(
            f"[{index:>2}/{len(targets)}] {question['id']:<7} "
            f"{answer['route']['retriever']:<4} "
            f"evidence={len(answer['evidence']):<3} "
            f"citations={len(answer['citations']):<3} "
            f"strength={answer['evidence_strength']['band']:<6} "
            f"{elapsed:6.1f}s",
            flush=True,
        )
    if failures:
        print(f"실패 {failures}건", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
