"""문서·Slack·코드 소스를 파싱해 data/parsed/ 로 내보낸다.

    .venv/bin/python -m ingestion.parsers.doc_cli            전체
    .venv/bin/python -m ingestion.parsers.doc_cli src_doc_x  일부

내보내는 것은 `<source_id>.source.json` 과 `<source_id>.jsonl` 두 벌뿐이다(DECISIONS A-8).
"""

from __future__ import annotations

import argparse
import re
import sys

from ingestion.parsers.doc_common import (
    owned_sources,
    parse_source,
    phone_hits,
    write_parsed,
)

URL_TOKEN = re.compile(r"<?https?://\S+")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A2b 파서 실행")
    parser.add_argument("source_ids", nargs="*", help="비우면 담당 소스 전체")
    args = parser.parse_args(argv)

    targets = owned_sources()
    if args.source_ids:
        wanted = set(args.source_ids)
        targets = [s for s in targets if s["id"] in wanted]
        missing = wanted - {s["id"] for s in targets}
        if missing:
            print(f"등록되지 않은 source_id: {sorted(missing)}", file=sys.stderr)
            return 2

    total_evidence = 0
    total_hits = 0
    leaks = 0
    for cfg in targets:
        source, evidences = parse_source(cfg)
        write_parsed(source, evidences)
        hits = sum(h["count"] for ev in evidences for h in ev["pii_hits"])
        leaks += sum(1 for ev in evidences if phone_hits(URL_TOKEN.sub(" ", ev["excerpt"])))
        total_evidence += len(evidences)
        total_hits += hits
        print(
            f"{source['source_id']:<32} {source['format']:<12} "
            f"evidence {len(evidences):>6}  마스킹 {hits:>5}건"
        )

    print("-" * 72)
    print(f"소스 {len(targets)}개 · Evidence {total_evidence}건 · 마스킹 {total_hits}건")
    print(f"발췌에 남은 전화번호(잔여 누출): {leaks}건")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
