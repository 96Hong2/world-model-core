"""저장된 응답만 재생하는 provider.

LLMService 의 content-hash 캐시 디렉토리를 그대로 입력으로 쓴다. 키 계산식이 같으므로
claude_cli 가 남긴 응답을 그대로 꺼낸다.

미스는 예외다. 조용히 실제 모델로 넘어가면 CI·eval 이 오프라인 재현이 아니게 되고
비용도 새 나간다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cache import LLMCache
from ..errors import FixtureMissError
from ..provider import LLMProvider
from ..tiers import TierConfig
from ..types import LLMResult

__all__ = ["FixtureProvider", "FixtureMissError"]


class FixtureProvider(LLMProvider):
    id = "fixture"

    def __init__(self, tiers: TierConfig | None = None, *, cache_dir: str | Path):
        self._tiers = tiers or TierConfig.load()
        self._store = LLMCache(cache_dir)

    def complete(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        tier: str = "S",
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        model = self._tiers.model_for(tier)
        key = self._store.key(model=model, prompt=prompt, schema=schema)
        record = self._store.get(key)
        if record is None:
            raise FixtureMissError(
                f"저장된 응답이 없다: key={key[:12]}… model={model} store={self._store.root}. "
                "실제 모델을 부르려면 claude_cli provider 를 쓴다."
            )
        return LLMResult(
            text=record.get("text", ""),
            parsed=None,
            model=record.get("model", model),
            cost_usd=0.0,
            input_tokens=int(record.get("input_tokens") or 0),
            output_tokens=int(record.get("output_tokens") or 0),
            cache_hit=False,
            attempts=1,
            provider=self.id,
        )
