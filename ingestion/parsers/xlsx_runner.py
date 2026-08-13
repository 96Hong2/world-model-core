"""xlsx 파서 5종을 한 번에 돌려 `data/parsed/` 를 만든다.

usage:
  python -m ingestion.parsers.xlsx_runner            # 전부
  python -m ingestion.parsers.xlsx_runner featuremap pain
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ingestion.parsers import (
    xlsx_activity,
    xlsx_bd,
    xlsx_featuremap,
    xlsx_pain,
    xlsx_weekly,
)
from ingestion.parsers.xlsx_common import PARSED_DIR, validate_records, write_result

PARSERS = {
    "featuremap": xlsx_featuremap,
    "activity": xlsx_activity,
    "weekly": xlsx_weekly,
    "bd": xlsx_bd,
    "pain": xlsx_pain,
}


def run(names: list[str] | None = None, out_dir: Path | None = None) -> dict:
    report: dict = {}
    for name in names or list(PARSERS):
        mod = PARSERS[name]
        result = mod.parse()
        errors = validate_records(result.source, result.evidence)
        if errors:
            raise SystemExit(f"[{name}] 계약 스키마 위반 {len(errors)}건: {errors[:3]}")
        src_path, jsonl_path = write_result(result, out_dir)
        pii: dict[str, int] = {}
        for ev in result.evidence:
            for h in ev.get("pii_hits", []):
                pii[h["kind"]] = pii.get(h["kind"], 0) + h["count"]
        report[name] = {
            "source_id": result.source["source_id"],
            "evidence": len(result.evidence),
            "pii_hits": pii,
            "stats": result.stats,
            "warnings": result.warnings,
            "files": [str(src_path), str(jsonl_path)],
        }
    return report


if __name__ == "__main__":  # pragma: no cover
    selected = sys.argv[1:] or None
    out = run(selected)
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
