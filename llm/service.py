"""상위 LLM 서비스.

한 번의 complete() 안에서 벌어지는 일:

    캐시 조회 → (히트면 비용 0 으로 즉시 반환)
    예산 확인 → provider 호출(일시 오류는 지수 백오프 3회)
    → 펜스 제거 → JSON 파싱 → JSON Schema 재검증
    → 위반이면 오류 메시지를 붙여 repair 2회
    → 그래도 안 되면 실패를 결과 객체로 반환(예외 아님)
    → 성공만 캐시에 저장, 모든 결과를 llm_calls.jsonl 에 기록

두 개의 재시도가 겹쳐 있다. 바깥은 스키마 attempt(원본 1 + repair 2), 안쪽은 일시 오류
재시도(3회)다. 일시 오류로 다시 부른 것은 스키마 attempt 를 늘리지 않는다.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .cache import LLMCache
from .errors import BudgetExceededError, LLMTransientError
from .fences import parse_json_payload
from .provider import LLMProvider
from .tiers import REPO_ROOT, TierConfig
from .types import LLMResult

# ⚠️ 이 상한은 원장(llm_calls.jsonl)의 **평생 누적치**와 비교된다. 2026-08-12 기준 누적이
# $48 을 넘어 이 기본값은 이미 초과 상태다(live 테스트가 이 때문에 거부됐다). 값을 올려도
# 누적은 계속 자라므로 근본 처방이 아니다 — 실행 단위 상한(LLMBudget)으로 관리하는 것이 맞고,
# 이 절대값을 어떻게 할지는 별도 결정 대상으로 남긴다. sonnet 전환과 무관하게 원래 그랬다.
DEFAULT_BUDGET_USD = 20.0
DEFAULT_LEDGER_PATH = REPO_ROOT / "data" / "cache" / "llm_calls.jsonl"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "llm"
MAX_REPAIRS = 2
MAX_TRANSIENT_TRIES = 3
BACKOFF_BASE_SECONDS = 1.0
MAX_ERRORS_IN_REPAIR = 8


def format_schema_errors(schema: dict[str, Any], obj: Any) -> list[str]:
    """사람이 읽고 모델이 고칠 수 있는 형태로 위반을 적는다."""
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    for error in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        location = "$" + "".join(f"[{part!r}]" for part in error.absolute_path)
        messages.append(f"{location}: {error.message}")
    return messages


def build_repair_prompt(original: str, previous_text: str, errors: list[str]) -> str:
    """직전 응답과 위반 지점을 붙여 다시 묻는다."""
    listed = "\n".join(f"- {line}" for line in errors[:MAX_ERRORS_IN_REPAIR])
    return (
        f"{original}\n\n"
        "---\n"
        "직전 응답이 요구 스키마를 만족하지 못했다.\n\n"
        f"[직전 응답]\n{previous_text[:2000]}\n\n"
        f"[스키마 위반]\n{listed}\n\n"
        "위 위반을 고쳐 스키마를 만족하는 JSON 만 다시 출력해라. "
        "설명·머리말·코드 펜스 밖 문장을 붙이지 마라."
    )


class LLMService:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        tiers: TierConfig | None = None,
        cache: LLMCache | None = None,
        ledger_path: str | Path | None = None,
        budget_usd: float = DEFAULT_BUDGET_USD,
        max_repairs: int = MAX_REPAIRS,
        max_transient_tries: int = MAX_TRANSIENT_TRIES,
        backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._provider = provider
        self._tiers = tiers or TierConfig.load()
        self._cache = cache
        self._ledger_path = Path(ledger_path) if ledger_path is not None else DEFAULT_LEDGER_PATH
        self._budget_usd = float(budget_usd)
        self._max_repairs = int(max_repairs)
        self._max_transient_tries = int(max_transient_tries)
        self._backoff_base = float(backoff_base_seconds)
        self._sleep = sleep
        self._spent_usd = self._read_ledger_total()
        # /ask·/brief 가 스레드풀에서 동시에 들어온다. 검사와 가산이 잠금 없이 갈리면
        # 가산이 유실되거나 상한 검사가 낡은 값을 본다. 호출 자체는 잠그지 않으므로
        # 동시 진행 중인 콜 수만큼의 초과는 남는다(콜 단가 수준이라 허용).
        self._budget_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    @property
    def spent_usd(self) -> float:
        return self._spent_usd

    @property
    def budget_usd(self) -> float:
        return self._budget_usd

    def complete(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        tier: str = "S",
        purpose: str = "",
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        model = self._tiers.model_for(tier)
        started = time.monotonic()

        # 캐시를 꺼도 계측에는 남긴다. 실패한 호출도 어느 프롬프트였는지 추적할 수 있어야 한다.
        content_key = LLMCache.key(model=model, prompt=prompt, schema=schema)
        if self._cache is not None:
            hit = self._cache.get(content_key)
            if hit is not None:
                return self._from_cache(
                    hit, model=model, tier=tier, purpose=purpose, content_key=content_key
                )

        attempt_prompt = prompt
        attempts = 0
        cost_total = 0.0
        input_total = 0
        output_total = 0
        last_text = ""
        last_error: str | None = None
        used_model = model

        while attempts < self._max_repairs + 1:
            self._assert_budget(tier)
            attempts += 1

            raw, transient = self._call_with_transient_retries(
                attempt_prompt,
                schema=schema,
                tier=tier,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if raw is None:
                last_error = f"일시 오류 재시도 소진: {transient}"
                return self._finish(
                    text=last_text,
                    parsed=None,
                    model=used_model,
                    cost=cost_total,
                    input_tokens=input_total,
                    output_tokens=output_total,
                    attempts=attempts,
                    ok=False,
                    schema_ok=True,
                    error=last_error,
                    tier=tier,
                    purpose=purpose,
                    started=started,
                    outcome="transient_exhausted",
                    content_key=content_key,
                    store=False,
                    prompt=prompt,
                    schema=schema,
                )

            cost_total += raw.cost_usd
            with self._budget_lock:
                self._spent_usd += raw.cost_usd
            input_total += raw.input_tokens
            output_total += raw.output_tokens
            last_text = raw.text
            used_model = raw.model or model

            parsed, parse_error = parse_json_payload(raw.text)
            errors: list[str]
            if parse_error is not None:
                errors = [parse_error]
            elif schema is not None:
                errors = format_schema_errors(schema, parsed)
            else:
                errors = []

            if not errors:
                return self._finish(
                    text=raw.text,
                    parsed=parsed,
                    model=used_model,
                    cost=cost_total,
                    input_tokens=input_total,
                    output_tokens=output_total,
                    attempts=attempts,
                    ok=True,
                    schema_ok=True,
                    error=None,
                    tier=tier,
                    purpose=purpose,
                    started=started,
                    outcome="ok",
                    content_key=content_key,
                    store=True,
                    prompt=prompt,
                    schema=schema,
                )

            last_error = "; ".join(errors[:MAX_ERRORS_IN_REPAIR])
            attempt_prompt = build_repair_prompt(prompt, raw.text, errors)

        # repair 를 다 쓰고도 못 맞췄다. 성공으로 위장하지 않고 실패를 그대로 돌려준다.
        return self._finish(
            text=last_text,
            parsed=None,
            model=used_model,
            cost=cost_total,
            input_tokens=input_total,
            output_tokens=output_total,
            attempts=attempts,
            ok=False,
            schema_ok=False,
            error=f"스키마 재검증 실패(repair {self._max_repairs}회 후): {last_error}",
            tier=tier,
            purpose=purpose,
            started=started,
            outcome="schema_failed",
            content_key=content_key,
            store=False,
            prompt=prompt,
            schema=schema,
        )

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------
    def _call_with_transient_retries(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None,
        tier: str,
        max_tokens: int | None,
        timeout: float | None,
    ) -> tuple[LLMResult | None, Exception | None]:
        last: Exception | None = None
        for attempt in range(1, self._max_transient_tries + 1):
            try:
                return (
                    self._provider.complete(
                        prompt,
                        schema=schema,
                        tier=tier,
                        max_tokens=max_tokens,
                        timeout=timeout,
                    ),
                    None,
                )
            except LLMTransientError as exc:
                last = exc
                if attempt < self._max_transient_tries:
                    self._sleep(self._backoff_base * (2 ** (attempt - 1)))
        return None, last

    def _assert_budget(self, tier: str) -> None:
        with self._budget_lock:
            spent = self._spent_usd
        if spent >= self._budget_usd:
            raise BudgetExceededError(
                f"누적 LLM 비용 ${spent:.4f} 이 상한 ${self._budget_usd:.2f} 에 도달했다. "
                f"호출을 거부한다(tier={tier}). 상한을 올리려면 budget_usd 를 명시적으로 바꿔라."
            )

    def _from_cache(
        self,
        record: dict[str, Any],
        *,
        model: str,
        tier: str,
        purpose: str,
        content_key: str,
    ) -> LLMResult:
        result = LLMResult(
            text=record.get("text", ""),
            parsed=record.get("parsed"),
            model=record.get("model", model),
            cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
            cache_hit=True,
            attempts=0,
            ok=True,
            schema_ok=True,
            error=None,
            provider=self._provider.id,
            latency_ms=0,
        )
        self._append_ledger(
            {
                "ts": _now(),
                "tier": tier,
                "provider": self._provider.id,
                "model": result.model,
                "purpose": purpose,
                "prompt_hash": content_key,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "cache_hit": True,
                "schema_ok": True,
                "attempts": 0,
                "latency_ms": 0,
                "outcome": "cache_hit",
            }
        )
        return result

    def _finish(
        self,
        *,
        text: str,
        parsed: Any | None,
        model: str,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        attempts: int,
        ok: bool,
        schema_ok: bool,
        error: str | None,
        tier: str,
        purpose: str,
        started: float,
        outcome: str,
        content_key: str,
        store: bool,
        prompt: str,
        schema: dict[str, Any] | None,
    ) -> LLMResult:
        latency_ms = int((time.monotonic() - started) * 1000)
        result = LLMResult(
            text=text,
            parsed=parsed,
            model=model,
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit=False,
            attempts=attempts,
            ok=ok,
            schema_ok=schema_ok,
            error=error,
            provider=self._provider.id,
            latency_ms=latency_ms,
        )

        # 성공만 저장한다. 실패를 캐시하면 다음 실행이 실패를 재생한다.
        if store and ok and self._cache is not None:
            self._cache.put(
                content_key,
                {
                    "key": content_key,
                    "created_at": _now(),
                    "provider": self._provider.id,
                    "model": model,
                    "tier": tier,
                    "purpose": purpose,
                    "prompt": prompt,
                    "schema": schema,
                    "text": text,
                    "parsed": parsed,
                    "cost_usd": cost,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "attempts": attempts,
                },
            )

        self._append_ledger(
            {
                "ts": _now(),
                "tier": tier,
                "provider": self._provider.id,
                "model": model,
                "purpose": purpose,
                "prompt_hash": content_key,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "cache_hit": False,
                "schema_ok": schema_ok,
                "attempts": attempts,
                "latency_ms": latency_ms,
                "outcome": outcome,
            }
        )
        return result

    def _append_ledger(self, row: dict[str, Any]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read_ledger_total(self) -> float:
        """이전 실행이 쓴 비용까지 합쳐야 예산 가드가 재시작을 넘어 작동한다."""
        if not self._ledger_path.exists():
            return 0.0
        total = 0.0
        for line in self._ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                total += float(json.loads(line).get("cost_usd") or 0.0)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return total


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
