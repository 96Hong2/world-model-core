"""demo.sh 가 받아 온 JSON 을 사람이 읽을 모양으로 찍는다.

    _show.py health <파일>   /health 응답 한 줄 요약
    _show.py answer <파일>   /ask 응답을 본문·근거·확신·관계도로 나눠서

셸에서 파이프로 넘기지 않고 파일 경로로 받는다. `python - <<'PY'` 는 스크립트 자체를
stdin 으로 읽어서, 같은 stdin 으로 데이터까지 넘길 수 없다(그 방식으로 짰다가 조용히 깨졌다).
"""

from __future__ import annotations

import json
import sys

BOLD, DIM, OFF = "\033[1m", "\033[2m", "\033[0m"


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def health(path: str) -> int:
    d = load(path)
    g = d.get("graph") or {}
    nodes, edges = g.get("nodes") or 0, g.get("edges") or 0
    print(
        f"  답변 API    {d.get('status')} · 노드 {nodes:,} · 엣지 {edges:,}"
        f" · 합성기 {d.get('synthesizer')}"
    )
    return 0


def answer(path: str) -> int:
    d = load(path)
    a = d.get("answer") or {}

    print(f"\n{BOLD}답변{OFF}\n")
    print(a.get("text") or "(본문이 비어 있다)")

    if a.get("recommendation"):
        print(f"\n{BOLD}권고{OFF}\n\n{a['recommendation']}")

    for part in a.get("estimated_parts") or []:
        print(f"\n  [추정] {part}")

    ev, cit, raw = d.get("evidence") or [], d.get("citations") or [], d.get("raw_signals") or []
    print(f"\n{BOLD}근거{OFF}  발췌 {len(ev)}건 · 각주 {len(cit)}개 · 추가 원문 근거 {len(raw)}건")

    st = d.get("evidence_strength") or {}
    basis = st.get("basis") or {}
    print(f"{BOLD}확신{OFF}  {st.get('band')}")
    for k, label in (
        ("independent_evidence", "독립 근거"),
        ("source_type_variety", "자료 종류"),
        ("highest_authority", "가장 권위 있는 자료"),
        ("contradiction", "모순"),
        ("recency", "최신성"),
    ):
        if k in basis:
            print(f"        {label}: {basis[k]}")

    for u in d.get("unknowns") or []:
        print(f"  [모르는 것] {u}")

    for gap in d.get("gaps") or []:
        what = gap.get("statement") or gap.get("need") or gap.get("capability") or ""
        print(f"  [기능 공백 · {gap.get('verdict')}] {what}")

    sg = d.get("subgraph") or {}
    nodes, edges = sg.get("nodes") or [], sg.get("edges") or []
    # 계약(answer.schema.json)의 엣지 양 끝은 from/to 다. source/target 으로 읽으면
    # 연결이 있는데도 모든 노드가 고아로 세어진다.
    linked = {e.get("from") for e in edges} | {e.get("to") for e in edges}
    orphan = sum(1 for n in nodes if n.get("id") not in linked)
    print(f"{BOLD}관계도{OFF}  노드 {len(nodes)} · 연결 {len(edges)} · 외따로 떨어진 노드 {orphan}")

    r = d.get("route") or {}
    tail = f"경로 {r.get('retriever')} · 규칙 {r.get('matched_rule')} · 권위 축 {r.get('claim_domain')}"
    if r.get("domain_classification_failed"):
        tail += " (축 판별 실패 → 중립)"
    print(f"{DIM}{tail}{OFF}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"health", "answer"}:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return {"health": health, "answer": answer}[argv[1]](argv[2])
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  응답을 읽지 못했다: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
