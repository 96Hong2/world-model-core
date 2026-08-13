"""LLM 호출 계층.

원칙(REVISED-MASTER-PLAN §2): LLM 은 후보 생성만 한다. 그래프 쓰기 권한이 없고
status 를 바꿀 수 없다. provider 는 추상화 뒤에 두어 목으로 갈아끼워도 상위 코드가 안 바뀐다.
"""

from .cache import LLMCache
from .errors import (
    BudgetExceededError,
    FixtureMissError,
    LLMConfigError,
    LLMError,
    LLMTimeoutError,
    LLMTransientError,
)
from .fences import parse_json_payload, strip_code_fence
from .provider import LLMProvider
from .service import LLMService, build_repair_prompt, format_schema_errors
from .tiers import TierConfig
from .types import LLMResult

__all__ = [
    "BudgetExceededError",
    "FixtureMissError",
    "LLMCache",
    "LLMConfigError",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "LLMService",
    "LLMTimeoutError",
    "LLMTransientError",
    "TierConfig",
    "build_repair_prompt",
    "format_schema_errors",
    "parse_json_payload",
    "strip_code_fence",
]
