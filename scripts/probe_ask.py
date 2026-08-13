"""질문을 던져 답변 계약의 핵심만 뽑아 본다.

고치기 전 현재 동작을 기록하는 용도다. 답이 부족하다는 판정을 인상이 아니라
필드 값(citations/evidence/gaps/unknowns/route)으로 남긴다.

  .venv/bin/python scripts/probe_ask.py --tag baseline --file questions.txt
  .venv/bin/python scripts/probe_ask.py --tag one -q "질문"

결과는 data/probe/<tag>.json 에 쌓인다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8099"
OUT = pathlib.Path("data/probe")


def login(user: str = "demo", pw: str = "bwm-demo") -> str:
    body = json.dumps({"username": user, "password": pw}).encode()
    req = urllib.request.Request(
        f"{API}/login", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["token"]


def ask(question: str, token: str, timeout: int = 600) -> dict:
    body = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        f"{API}/ask",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
        payload["_elapsed_s"] = round(time.time() - t0, 1)
        return payload
    except urllib.error.HTTPError as exc:
        return {"_error": f"HTTP {exc.code}", "_body": exc.read().decode()[:500],
                "_elapsed_s": round(time.time() - t0, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"_error": repr(exc), "_elapsed_s": round(time.time() - t0, 1)}


def digest(q: str, a: dict) -> dict:
    """채점에 쓸 값만 남긴다. 전문은 raw 에 그대로 둔다."""
    if "_error" in a:
        return {"question": q, "error": a["_error"], "elapsed_s": a.get("_elapsed_s")}
    ev = a.get("evidence") or []
    subgraph = a.get("subgraph") or {}
    return {
        "question": q,
        "elapsed_s": a.get("_elapsed_s"),
        "route": a.get("route"),
        "answer_len": len(((a.get("answer") or {}).get("text")) or ""),
        "answer_text": ((a.get("answer") or {}).get("text")) or "",
        "citations": len(a.get("citations") or []),
        "evidence": len(ev),
        "evidence_sources": sorted({e.get("source_type") for e in ev if e.get("source_type")}),
        "strength": (a.get("evidence_strength") or {}).get("band"),
        "unknowns": a.get("unknowns") or [],
        "gaps": a.get("gaps") or [],
        "nodes": len(subgraph.get("nodes") or []),
        "edges": len(subgraph.get("edges") or []),
        "node_types": sorted({n.get("type") for n in (subgraph.get("nodes") or []) if n.get("type")}),
        "notices": a.get("notices") or [],
        "next_actions": a.get("next_actions") or [],
        "raw": a,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("-q", "--question", action="append", default=[])
    ap.add_argument("--file", help="한 줄에 질문 하나. # 로 시작하면 주석")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    qs = list(args.question)
    if args.file:
        for line in pathlib.Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                qs.append(line)
    if not qs:
        ap.error("질문이 없다")

    token = login()
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{args.tag}.json"
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []

    for i, q in enumerate(qs, 1):
        print(f"[{i}/{len(qs)}] {q[:70]}", flush=True)
        d = digest(q, ask(q, token, args.timeout))
        results.append(d)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        if "error" in d:
            print(f"    ❌ {d['error']}  {d['elapsed_s']}s", flush=True)
        else:
            print(
                f"    {d['elapsed_s']}s route={d['route']} 근거{d['evidence']} 각주{d['citations']}"
                f" {d['strength']} 노드{d['nodes']} 답{d['answer_len']}자",
                flush=True,
            )
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
