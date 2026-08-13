"""content-hash 캐시.

키는 sha256(model + prompt + schema) 다. provider 는 키에 넣지 않는다 — 넣으면
claude_cli 가 남긴 응답을 fixture provider 가 재생할 수 없어서, 캐시가 fixture 의 입력이
된다는 설계가 깨진다.

저장 내용은 이미 마스킹을 마친 발췌로 만든 프롬프트다(마스킹은 파서 단계에서 끝난다).
data/cache/ 는 gitignore 대상이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class LLMCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*, model: str, prompt: str, schema: dict[str, Any] | None) -> str:
        payload = json.dumps(
            {"model": model, "prompt": prompt, "schema": schema},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, record: dict[str, Any]) -> Path:
        path = self.path(key)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return path
