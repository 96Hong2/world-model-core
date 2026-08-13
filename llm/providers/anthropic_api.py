"""Anthropic API 직접 호출 provider.

키가 있을 때만 동작한다. 키가 없으면 생성 시점에 바로 막는다 — 호출 직전까지 끌고 가면
어디서 끊겼는지 알기 어렵다.

⚠️ 이 환경에는 ANTHROPIC_API_KEY 가 없어서 실제 호출을 검증하지 못했다. 배치·병렬이
필요해지면 이 경로를 먼저 실측하고 쓴다.

비용은 추정하지 않고 단가표로 계산한다. 단가를 모르는 모델은 조용히 0 으로 기록하지 않고
설정 오류로 막는다. 잘못된 0 이 예산 가드를 무력화하기 때문이다.
"""

from __future__ import annotations

import os
import time
from typing import Any

from ..errors import LLMConfigError, LLMTransientError
from ..provider import LLMProvider
from ..tiers import TierConfig
from ..types import LLMResult

# USD per 1M tokens (input, output). 모델 id 접두사로 찾는다.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # haiku 는 model_policy 가 금지한 모델이지만 단가는 남겨 둔다. 표에서 빼면 실수로 골랐을 때
    # 「단가를 모른다」로 죽어서 정책 위반이라는 진짜 이유가 가려진다(거부는 tiers.py 가 한다).
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
}

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def resolve_pricing(model: str) -> tuple[float, float] | None:
    matches = [key for key in PRICING_USD_PER_MTOK if model.startswith(key)]
    if not matches:
        return None
    return PRICING_USD_PER_MTOK[max(matches, key=len)]


class AnthropicAPIProvider(LLMProvider):
    id = "anthropic"

    def __init__(
        self,
        tiers: TierConfig | None = None,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        pricing: dict[str, tuple[float, float]] | None = None,
    ):
        self._tiers = tiers or TierConfig.load()
        self._client = client
        self._pricing = pricing
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if self._client is None and not self._api_key:
            raise LLMConfigError(
                "ANTHROPIC_API_KEY 가 없다. 이 환경의 기본 provider 는 claude_cli 다. "
                "API 를 쓰려면 키를 설정하고 다시 만들어라."
            )

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
        price = self._price_for(model)
        client = self._get_client()

        started = time.monotonic()
        try:
            # sampling 파라미터는 보내지 않는다. Opus 5 계열은 temperature/top_p 를 400 으로
            # 거부한다. 구조화 출력도 여기서 강제하지 않고 LLMService 의 공통 재검증에 맡긴다.
            message = client.messages.create(
                model=model,
                max_tokens=int(max_tokens or self._tiers.max_output_tokens(tier)),
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001 - SDK 예외 타입을 여기서 단정하지 않는다
            status = getattr(exc, "status_code", None)
            if status in RETRYABLE_STATUS or status is None:
                raise LLMTransientError(f"anthropic 호출 실패(status={status}): {exc}") from exc
            raise LLMConfigError(f"anthropic 호출 거부(status={status}): {exc}") from exc

        text = "".join(
            block.text for block in getattr(message, "content", []) if getattr(block, "type", "") == "text"
        )
        usage = getattr(message, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000

        return LLMResult(
            text=text,
            parsed=None,
            model=getattr(message, "model", model),
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit=False,
            attempts=1,
            provider=self.id,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------------
    def _price_for(self, model: str) -> tuple[float, float]:
        table = self._pricing or PRICING_USD_PER_MTOK
        matches = [key for key in table if model.startswith(key)]
        if not matches:
            raise LLMConfigError(
                f"모델 {model!r} 의 단가를 모른다. pricing 을 주입하거나 단가표를 갱신해라. "
                "비용을 0 으로 기록하면 예산 가드가 무력해진다."
            )
        return table[max(matches, key=len)]

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise LLMConfigError(
                "anthropic SDK 가 설치되어 있지 않다. `pip install anthropic` 후에 쓴다."
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client
