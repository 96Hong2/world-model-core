"""호출 결과 자료형."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMResult:
    """한 번의 논리적 호출 결과.

    provider 는 text·비용·토큰까지만 채운다. parsed·schema_ok·ok 는 상위 서비스가
    공통 재검증을 마친 뒤 채운다. 스키마를 못 맞춘 실패도 예외가 아니라 이 객체로 돌아온다
    (ok=False). 호출부가 실패를 못 보고 지나치지 않게 하려는 것이다.
    """

    text: str
    parsed: Any | None
    model: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    attempts: int
    ok: bool = True
    schema_ok: bool = True
    error: str | None = None
    provider: str = ""
    latency_ms: int = 0
