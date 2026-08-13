"""LLM 계층 예외.

재시도 가능한 것과 아닌 것을 타입으로 가른다. 서비스는 LLMTransientError 만 되풀이하고
나머지는 그대로 위로 올린다. 조용히 삼키면 실패가 성공으로 보인다.
"""

from __future__ import annotations


class LLMError(Exception):
    """LLM 호출 계층의 모든 예외의 뿌리."""


class LLMTransientError(LLMError):
    """타임아웃·일시 오류. 지수 백오프로 재시도한다."""


class LLMTimeoutError(LLMTransientError):
    """정해진 시간 안에 응답이 오지 않았다."""


class LLMConfigError(LLMError):
    """자격증명·설정이 없어서 애초에 호출할 수 없다. 재시도해도 소용없다."""


class FixtureMissError(LLMError):
    """저장된 응답이 없다. 오프라인 재현 중에 새 호출이 필요해졌다는 뜻이다."""


class BudgetExceededError(LLMError):
    """누적 비용이 상한에 도달했다. 더 쓰지 않고 거부한다."""
