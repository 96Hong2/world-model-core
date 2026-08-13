"""provider 추상화.

상위 서비스는 provider 를 이 인터페이스로만 안다. 목으로 갈아끼워도 호출부가 안 바뀌는 것이
A3 의 수용 기준이다. provider 는 전송만 책임진다 — 펜스 제거·JSON 파싱·스키마 검증·캐시·
계측은 전부 LLMService 가 한다.
"""

from __future__ import annotations

import abc
from typing import Any

from .types import LLMResult


class LLMProvider(abc.ABC):
    """텍스트를 넣고 텍스트를 받는 최소 계약."""

    id: str = "unknown"

    @abc.abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        tier: str = "S",
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        """모델 응답을 받아 온다.

        parsed 는 채우지 않는다(None). 일시 오류는 LLMTransientError 로 올린다.
        """
        raise NotImplementedError
