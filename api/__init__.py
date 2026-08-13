"""A6 — Answer API.

A5 가 만든 `RetrievalResult` 를 Answer 계약 1벌로 조립한다. 그래프에 쓰지 않고,
`contracts/`·`config/` 는 읽기만 한다.

조립 순서는 고정이다.
    근거 선별 → 합성(LLM 또는 결정적 대체) → 하드 게이트 3종 → 계약 조립 → audit 기록

하드 게이트는 조용히 통과시키지 않는다. 무엇을 왜 지웠는지 `GuardReport` 에 남기고
`AnswerService.last_guard_report` 로 꺼내 볼 수 있다.
"""

from .accounts import ACCOUNTS, Account, authenticate
from .contracts import answer_validator, validate_answer
from .guards import GuardReport, find_pii, unsupported_tokens
from .service import AnswerService
from .synthesis import DeterministicSynthesizer, LLMSynthesizer, Synthesis

__all__ = [
    "ACCOUNTS",
    "Account",
    "AnswerService",
    "DeterministicSynthesizer",
    "GuardReport",
    "LLMSynthesizer",
    "Synthesis",
    "answer_validator",
    "authenticate",
    "find_pii",
    "unsupported_tokens",
    "validate_answer",
]
