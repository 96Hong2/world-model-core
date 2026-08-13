"""query audit 로그 (REVISED §0 43행 — 1A 는 이 한 종류만).

누가·언제·무슨 질문을 했고, 어느 경로로 갔고, 몇 건을 인용했는가. 답변 본문과 발췌는
남기지 않는다. 로그는 오래 남고 접근 통제가 다르기 때문에, 자료 본문을 복제해 두면
sensitivity 필터를 우회하는 두 번째 사본이 된다.

질문 문장은 그대로 남기되(무엇을 물었는지가 감사 대상이다) PII 는 지운 뒤 적는다.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .guards import redact

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = REPO_ROOT / "data" / "audit" / "query.jsonl"


def new_query_id() -> str:
    return f"q_{uuid.uuid4().hex[:16]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class QueryAudit:
    path: Path = DEFAULT_AUDIT_PATH

    def record(self, row: dict[str, Any]) -> None:
        payload = dict(row)
        payload["question"] = redact(str(payload.get("question") or ""))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
