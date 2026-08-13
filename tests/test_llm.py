"""LLMService 검증.

기대값의 근거는 REVISED-MASTER-PLAN §6 A3 / §4 ③ / config/tiers.yaml / contracts/state.enum.json
이지 실제 출력이 아니다. 구현보다 먼저 쓴다.

기본 실행에서 live 호출은 제외된다. 실제 claude CLI 를 한 번 태우려면:
    BWM_LLM_LIVE=1 .venv/bin/python -m pytest tests/test_llm.py -q -k live -s
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm import (  # noqa: E402
    BudgetExceededError,
    LLMCache,
    LLMConfigError,
    LLMProvider,
    LLMResult,
    LLMService,
    LLMTransientError,
    TierConfig,
    parse_json_payload,
    strip_code_fence,
)
from llm.extraction import (  # noqa: E402
    FORBIDDEN_EXTRACTION_FIELDS,
    ExtractionSchemaError,
    build_extraction_prompt,
    build_extraction_schema,
    t2_gate,
)
from llm.providers.anthropic_api import AnthropicAPIProvider  # noqa: E402
from llm.providers.claude_cli import ClaudeCLIProvider  # noqa: E402
from llm.providers.fixture import FixtureMissError, FixtureProvider  # noqa: E402

LIVE = os.environ.get("BWM_LLM_LIVE") == "1"

# --------------------------------------------------------------------------
# 테스트용 provider — 상위 코드가 provider 를 몰라야 한다는 AC 를 이걸로 검증한다
# --------------------------------------------------------------------------


class SpyProvider(LLMProvider):
    """정해 둔 응답을 순서대로 돌려주고 호출 횟수를 센다."""

    id = "spy"

    def __init__(self, responses, *, cost_usd: float = 0.01, model: str = "spy-model"):
        self._responses = list(responses)
        self._cost = cost_usd
        self._model = model
        self.calls: list[str] = []

    def complete(self, prompt, *, schema=None, tier="S", max_tokens=None, timeout=None):
        self.calls.append(prompt)
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        item = self._responses[idx]
        if isinstance(item, Exception):
            raise item
        return LLMResult(
            text=item,
            parsed=None,
            model=self._model,
            cost_usd=self._cost,
            input_tokens=11,
            output_tokens=7,
            cache_hit=False,
            attempts=1,
        )


SIMPLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


def make_service(tmp_path: Path, provider, **kwargs) -> LLMService:
    """호출부는 provider 종류를 몰라도 되어야 한다 — 인자만 바꿔 끼운다."""
    kwargs.setdefault("cache", LLMCache(tmp_path / "cache" / "llm"))
    kwargs.setdefault("ledger_path", tmp_path / "cache" / "llm_calls.jsonl")
    kwargs.setdefault("sleep", lambda _seconds: None)
    return LLMService(provider, **kwargs)


def call_under_test(service: LLMService) -> LLMResult:
    """provider 를 바꿔도 이 함수는 바뀌지 않아야 한다."""
    return service.complete(
        "질문: 한 줄 답을 JSON 으로",
        schema=SIMPLE_SCHEMA,
        tier="S",
        purpose="unit_test",
    )


# --------------------------------------------------------------------------
# 1. 코드 펜스 제거 (claude CLI 응답이 ```json 으로 감싸여 온다 — DECISIONS A-9)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('{"a": 1}', '{"a": 1}'),
        ('  ```json\n{"a": 1}\n```  ', '{"a": 1}'),
        ('설명입니다.\n```json\n{"a": 1}\n```\n끝.', '{"a": 1}'),
        ("```JSON\n[1, 2]\n```", "[1, 2]"),
    ],
)
def test_strip_code_fence(raw, expected):
    assert strip_code_fence(raw) == expected


def test_parse_json_payload_handles_fenced_and_bare():
    obj, err = parse_json_payload('```json\n{"a": 1}\n```')
    assert err is None
    assert obj == {"a": 1}

    obj, err = parse_json_payload("이건 JSON 이 아니다")
    assert obj is None
    assert err


# --------------------------------------------------------------------------
# 2. provider 목 교체 시 상위 코드 무변경 (A3 AC)
# --------------------------------------------------------------------------


def test_provider_swap_keeps_caller_code_unchanged(tmp_path):
    shared_cache_dir = tmp_path / "shared"

    spy = SpyProvider(['```json\n{"answer": "네"}\n```'])
    svc_live = make_service(tmp_path, spy, cache=LLMCache(shared_cache_dir))
    first = call_under_test(svc_live)

    assert first.ok is True
    assert first.parsed == {"answer": "네"}

    # 같은 호출부, provider 만 fixture 로 교체. 서비스 캐시는 비워 두어
    # fixture provider 가 실제로 불리게 한다.
    tiers = TierConfig.load()
    fixture = FixtureProvider(tiers, cache_dir=shared_cache_dir)
    svc_offline = make_service(
        tmp_path, fixture, cache=None, ledger_path=tmp_path / "offline.jsonl"
    )
    second = call_under_test(svc_offline)

    assert second.ok is True
    assert second.parsed == first.parsed
    assert second.cost_usd == 0.0
    assert len(spy.calls) == 1  # 두 번째 호출은 실제 모델을 쓰지 않았다


def test_fixture_provider_miss_raises(tmp_path):
    tiers = TierConfig.load()
    fixture = FixtureProvider(tiers, cache_dir=tmp_path / "empty")
    svc = make_service(tmp_path, fixture, cache=None)

    with pytest.raises(FixtureMissError):
        call_under_test(svc)


# --------------------------------------------------------------------------
# 3. 스키마 위반 → repair 2회 후 실패 반환 (A3 AC, tiers.yaml retry_ladder)
# --------------------------------------------------------------------------


def test_schema_violation_returns_failure_after_two_repairs(tmp_path):
    bad = '```json\n{"answer": 123}\n```'  # answer 는 string 이어야 한다
    spy = SpyProvider([bad, bad, bad, bad])
    svc = make_service(tmp_path, spy)

    result = call_under_test(svc)

    assert result.ok is False, "스키마를 못 맞췄는데 성공으로 위장하면 안 된다"
    assert result.schema_ok is False
    assert result.parsed is None
    assert result.attempts == 3, "원본 1회 + repair 2회"
    assert len(spy.calls) == 3
    assert result.error

    # repair 프롬프트에는 위반 내용이 붙어야 한다
    assert spy.calls[0] != spy.calls[1]
    assert "answer" in spy.calls[1]

    # 실패는 캐시에 남기지 않는다 — 다음 실행이 실패를 재생하면 안 된다
    assert list((tmp_path / "cache" / "llm").glob("*.json")) == []

    # 실패해도 어느 프롬프트였는지는 추적할 수 있어야 한다
    ledger = tmp_path / "cache" / "llm_calls.jsonl"
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert row["outcome"] == "schema_failed"
    assert row["schema_ok"] is False
    assert len(row["prompt_hash"]) == 64


def test_schema_violation_recovers_when_repair_succeeds(tmp_path):
    spy = SpyProvider(['{"answer": 123}', '```json\n{"answer": "고쳤다"}\n```'])
    svc = make_service(tmp_path, spy)

    result = call_under_test(svc)

    assert result.ok is True
    assert result.schema_ok is True
    assert result.parsed == {"answer": "고쳤다"}
    assert result.attempts == 2


# --------------------------------------------------------------------------
# 4. content-hash 캐시 — 히트 시 비용 0, provider 미호출 (A3 AC)
# --------------------------------------------------------------------------


def test_cache_hit_costs_zero_and_skips_provider(tmp_path):
    spy = SpyProvider(['```json\n{"answer": "한 번만"}\n```'])
    svc = make_service(tmp_path, spy)

    first = call_under_test(svc)
    assert first.cache_hit is False
    assert first.cost_usd == pytest.approx(0.01)
    assert len(spy.calls) == 1

    second = call_under_test(svc)
    assert second.cache_hit is True
    assert second.cost_usd == 0.0
    assert second.parsed == first.parsed
    assert len(spy.calls) == 1, "캐시 히트인데 provider 가 불렸다"


def test_cache_key_changes_with_schema(tmp_path):
    cache = LLMCache(tmp_path / "c")
    other_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "integer"}},
    }
    k1 = cache.key(model="m", prompt="p", schema=SIMPLE_SCHEMA)
    k2 = cache.key(model="m", prompt="p", schema=other_schema)
    k3 = cache.key(model="m2", prompt="p", schema=SIMPLE_SCHEMA)
    k4 = cache.key(model="m", prompt="p2", schema=SIMPLE_SCHEMA)
    assert len({k1, k2, k3, k4}) == 4


# --------------------------------------------------------------------------
# 5. 재시도 사다리 — 일시 오류는 지수 백오프 3회
# --------------------------------------------------------------------------


def test_transient_errors_retry_then_succeed(tmp_path):
    spy = SpyProvider(
        [
            LLMTransientError("timeout"),
            LLMTransientError("timeout"),
            '{"answer": "세 번째에 성공"}',
        ]
    )
    slept: list[float] = []
    svc = make_service(tmp_path, spy, sleep=slept.append)

    result = call_under_test(svc)

    assert result.ok is True
    assert len(spy.calls) == 3
    assert result.attempts == 1, "일시 오류 재시도는 스키마 attempt 를 늘리지 않는다"
    assert slept == sorted(slept) and len(slept) == 2, "지수 백오프로 2번 쉬어야 한다"
    assert slept[1] > slept[0]


def test_transient_errors_exhausted_returns_failure(tmp_path):
    spy = SpyProvider([LLMTransientError("boom")] * 5)
    svc = make_service(tmp_path, spy)

    result = call_under_test(svc)

    assert result.ok is False
    assert result.parsed is None
    assert len(spy.calls) == 3, "일시 오류 재시도는 3회까지"
    assert "boom" in (result.error or "")


# --------------------------------------------------------------------------
# 6. 예산 가드
# --------------------------------------------------------------------------


def test_budget_guard_refuses_when_limit_exceeded(tmp_path):
    spy = SpyProvider(['{"answer": "ok"}'], cost_usd=0.02)
    svc = make_service(tmp_path, spy, budget_usd=0.03)

    call_under_test(svc)  # 누적 0.02
    svc.complete("다른 질문 A", schema=SIMPLE_SCHEMA, purpose="unit_test")  # 누적 0.04

    with pytest.raises(BudgetExceededError):
        svc.complete("다른 질문 B", schema=SIMPLE_SCHEMA, purpose="unit_test")

    assert len(spy.calls) == 2, "예산 초과 호출은 provider 에 닿지 않아야 한다"


def test_budget_guard_reads_previous_ledger(tmp_path):
    ledger = tmp_path / "llm_calls.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"ts": "2026-08-07T00:00:00Z", "cost_usd": 20.5}) + "\n",
        encoding="utf-8",
    )
    spy = SpyProvider(['{"answer": "ok"}'], cost_usd=0.02)
    svc = make_service(tmp_path, spy, ledger_path=ledger, budget_usd=20.0)

    with pytest.raises(BudgetExceededError):
        call_under_test(svc)
    assert spy.calls == []


def test_cache_hit_is_not_blocked_by_budget(tmp_path):
    spy = SpyProvider(['{"answer": "ok"}'], cost_usd=0.02)
    svc = make_service(tmp_path, spy, budget_usd=0.01)

    first = call_under_test(svc)
    assert first.ok is True

    second = call_under_test(svc)  # 캐시 히트라 비용 0 → 거부되면 안 된다
    assert second.cache_hit is True
    assert second.ok is True


# --------------------------------------------------------------------------
# 7. llm_call 계측
# --------------------------------------------------------------------------


def test_ledger_records_required_fields(tmp_path):
    spy = SpyProvider(['{"answer": "ok"}'])
    ledger = tmp_path / "cache" / "llm_calls.jsonl"
    svc = make_service(tmp_path, spy, ledger_path=ledger)

    call_under_test(svc)
    call_under_test(svc)  # 캐시 히트도 기록되어야 한다

    lines = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x]
    assert len(lines) == 2

    required = {
        "ts",
        "tier",
        "provider",
        "model",
        "purpose",
        "prompt_hash",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cache_hit",
        "schema_ok",
        "attempts",
        "latency_ms",
        "outcome",
    }
    for row in lines:
        assert required <= set(row), f"빠진 필드: {required - set(row)}"

    assert lines[0]["cache_hit"] is False
    assert lines[0]["tier"] == "S"
    assert lines[0]["purpose"] == "unit_test"
    assert lines[1]["cache_hit"] is True
    assert lines[1]["cost_usd"] == 0.0


# --------------------------------------------------------------------------
# 8. tiers.yaml 바인딩
# --------------------------------------------------------------------------


def test_tier_config_binds_s_and_l():
    tiers = TierConfig.load()
    assert tiers.model_for("S") == "claude-sonnet-5"
    assert tiers.model_for("L") == "claude-opus-5"
    assert tiers.max_output_tokens("S") == 4096
    assert tiers.max_output_tokens("L") == 8192
    assert tiers.default_provider == "claude_cli"
    with pytest.raises(KeyError):
        tiers.model_for("M")


# --------------------------------------------------------------------------
# 9. 추출 스키마 — LLM 은 후보만 만든다 (§2, contracts/state.enum.json I4)
# --------------------------------------------------------------------------


def test_extraction_schema_requires_provenance():
    schema = build_extraction_schema({"claim_text": {"type": "string"}})
    item = schema["properties"]["items"]["items"]
    assert {"source_id", "locator", "evidence_quote"} <= set(item["required"])
    assert item["additionalProperties"] is False


def test_extraction_schema_rejects_status_and_graph_write_fields():
    for field in ["status", "lane", "claim_ids", "cypher", "evidence_strength"]:
        assert field in FORBIDDEN_EXTRACTION_FIELDS
        with pytest.raises(ExtractionSchemaError):
            build_extraction_schema({field: {"type": "string"}})


def test_llm_cannot_write_status_even_if_it_tries(tmp_path):
    """LLM 이 status VERIFIED 를 반환해도 스키마가 거부해야 한다."""
    schema = build_extraction_schema({"claim_text": {"type": "string"}})
    sneaky = json.dumps(
        {
            "items": [
                {
                    "source_id": "src_x",
                    "locator": "sheet1!A1",
                    "evidence_quote": "원문 그대로",
                    "claim_text": "원문 그대로",
                    "status": "VERIFIED",
                }
            ]
        },
        ensure_ascii=False,
    )
    spy = SpyProvider([sneaky] * 4)
    svc = make_service(tmp_path, spy)

    result = svc.complete("추출해라", schema=schema, purpose="extract")

    assert result.ok is False
    assert result.schema_ok is False
    assert result.attempts == 3


def test_extraction_prompt_carries_schema_and_provenance():
    """스키마를 프롬프트에 안 실으면 모델이 필드 이름을 지어낸다(실측으로 확인)."""
    schema = build_extraction_schema({"account": {"type": "string"}})
    prompt = build_extraction_prompt(
        "고객사 이름을 뽑아라",
        schema=schema,
        excerpts=[
            {"source_id": "src_probe", "locator": "sheet1!A12", "text": "가나손해보험 제안 건"}
        ],
    )

    assert '"additionalProperties": false' in prompt
    assert "evidence_quote" in prompt
    assert "src_probe" in prompt and "sheet1!A12" in prompt
    assert "가나손해보험 제안 건" in prompt
    assert "지어내지 않는다" in prompt


def test_extraction_prompt_requires_provenance_on_every_excerpt():
    schema = build_extraction_schema({"account": {"type": "string"}})
    with pytest.raises(ExtractionSchemaError):
        build_extraction_prompt(
            "뽑아라", schema=schema, excerpts=[{"source_id": "src_x", "text": "본문"}]
        )
    with pytest.raises(ExtractionSchemaError):
        build_extraction_prompt("뽑아라", schema=schema, excerpts=[])


# --------------------------------------------------------------------------
# 10. T2 게이트 — 숫자·고유명사 보존 검사 (§4 ③, A4 AC)
# --------------------------------------------------------------------------


def test_t2_gate_keeps_item_whose_numbers_are_in_quote():
    items = [
        {
            "source_id": "src_sales",
            "locator": "sheet1!A12",
            "evidence_quote": "가나손해보험 제안 금액은 2.85억원이다.",
            "account": "가나손해보험",
            "amount_text": "2.85억원",
        }
    ]
    report = t2_gate(items)
    assert len(report.kept) == 1
    assert report.dropped == []


def test_t2_gate_drops_number_absent_from_quote():
    items = [
        {
            "source_id": "src_sales",
            "locator": "sheet1!A12",
            "evidence_quote": "가나손해보험 제안 금액은 2.85억원이다.",
            "account": "가나손해보험",
            "amount_text": "5.20억원",  # 발췌에 없는 숫자
        }
    ]
    report = t2_gate(items)
    assert report.kept == []
    assert len(report.dropped) == 1
    dropped_item, reasons = report.dropped[0]
    assert dropped_item is items[0]
    assert any("5.20" in r or "5.2" in r for r in reasons)


def test_t2_gate_drops_proper_noun_absent_from_quote():
    items = [
        {
            "source_id": "src_sales",
            "locator": "sheet1!A12",
            "evidence_quote": "마바손해보험과 협상 중이다.",
            "account": "마바캐피탈",  # 발췌에 없는 고유명사 — 오병합 차단
        }
    ]
    report = t2_gate(items)
    assert report.kept == []
    assert any("마바캐피탈" in r for r in report.dropped[0][1])


def test_t2_gate_uses_injected_lexicon():
    items = [
        {
            "source_id": "src_doc",
            "locator": "p.3",
            "evidence_quote": "경쟁사 대응 전략을 논의했다.",
            "note": "한울SI 가 가격으로 경쟁한다",  # 발췌에 없는 사전 표제어
        }
    ]
    report = t2_gate(items, lexicon=["한울SI", "늘봄화재"])
    assert report.kept == []
    assert any("한울SI" in r for r in report.dropped[0][1])

    report_ok = t2_gate(items)  # 사전을 안 주면 note 는 자유 텍스트로 통과
    assert len(report_ok.kept) == 1


def test_t2_gate_ignores_locator_digits():
    """locator 의 숫자(sheet1!A12)는 주장이 아니므로 검사 대상이 아니다."""
    items = [
        {
            "source_id": "src_sales",
            "locator": "sheet1!A12",
            "evidence_quote": "협상 단계로 진행한다.",
            "stage": "협상",
        }
    ]
    assert len(t2_gate(items).kept) == 1


def test_t2_gate_drops_item_without_quote():
    items = [{"source_id": "src_x", "locator": "p.1", "evidence_quote": "  "}]
    report = t2_gate(items)
    assert report.kept == []


# --------------------------------------------------------------------------
# 11. anthropic provider — 키 없으면 명확한 예외
# --------------------------------------------------------------------------


def test_anthropic_provider_without_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tiers = TierConfig.load()
    with pytest.raises(LLMConfigError) as exc:
        AnthropicAPIProvider(tiers)
    assert "ANTHROPIC_API_KEY" in str(exc.value)


# --------------------------------------------------------------------------
# 12. claude CLI provider
# --------------------------------------------------------------------------


def test_claude_cli_provider_builds_expected_argv():
    tiers = TierConfig.load()
    provider = ClaudeCLIProvider(tiers)
    argv = provider.build_argv(tier="S")
    assert argv[:2] == ["claude", "-p"]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert "--strict-mcp-config" in argv
    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[-2:] == ["--output-format", "json"]


def test_claude_cli_provider_runs_in_neutral_cwd():
    """프로젝트 컨텍스트가 딸려오면 비용·오염 위험이 있다."""
    provider = ClaudeCLIProvider(TierConfig.load())
    cwd = Path(provider.cwd).resolve()
    assert ROOT not in cwd.parents and cwd != ROOT


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="BWM_LLM_LIVE=1 일 때만 실제 모델을 호출한다")
def test_live_claude_cli_extraction(tmp_path, capsys):
    """실동작 1건. 결과는 캐시에 남아 fixture provider 의 입력이 된다."""
    cache_dir = ROOT / "data" / "cache" / "llm"
    ledger = ROOT / "data" / "cache" / "llm_calls.jsonl"
    tiers = TierConfig.load()
    provider = ClaudeCLIProvider(tiers)
    svc = LLMService(
        provider,
        tiers=tiers,
        cache=LLMCache(cache_dir),
        ledger_path=ledger,
        budget_usd=1.0,
    )

    schema = build_extraction_schema(
        {"account": {"type": "string"}, "amount_text": {"type": "string"}}
    )
    prompt = build_extraction_prompt(
        "발췌에서 고객사 이름(account)과 금액 표기(amount_text)를 뽑아라.",
        schema=schema,
        excerpts=[
            {
                "source_id": "src_live_probe",
                "locator": "sheet1!A12",
                "text": "가나손해보험 제안 금액은 2.85억원이다.",
            }
        ],
    )

    started = time.monotonic()
    result = svc.complete(prompt, schema=schema, tier="S", purpose="live_probe")
    elapsed = time.monotonic() - started

    assert result.ok is True, result.error
    assert result.schema_ok is True
    assert result.parsed["items"], "items 가 비었다"

    report = t2_gate(result.parsed["items"])
    with capsys.disabled():
        print(
            f"\n[live] cost=${result.cost_usd:.6f} elapsed={elapsed:.1f}s "
            f"attempts={result.attempts} cache_hit={result.cache_hit} "
            f"in={result.input_tokens} out={result.output_tokens}\n"
            f"[live] kept={len(report.kept)} dropped={len(report.dropped)}\n"
            f"[live] cache_dir={cache_dir}\n"
        )

    assert list(cache_dir.glob("*.json")), "캐시 파일이 안 남았다"


# ---------------------------------------------------------------------------
# 모델 정책 — 하이쿠 금지, 최소 소넷 (2026-08-12 지시)
# ---------------------------------------------------------------------------


class TestModelPolicy:
    """`model_policy.min_tier: sonnet` 을 코드가 지킨다.

    설정 파일에 문장으로만 적어 두면 다음 사람이 「저비용 단계니까」라며 하이쿠를 되돌려 놓는다.
    실제로 이 저장소는 S tier 를 하이쿠로 시작했다. 강제되지 않는 정책은 주석이다.
    """

    @staticmethod
    def _config(model: str):
        import yaml

        from llm.tiers import TierConfig

        raw = yaml.safe_load((ROOT / "config" / "tiers.yaml").read_text(encoding="utf-8"))
        raw["tiers"]["S"]["model"] = model
        return TierConfig(raw=raw, path=ROOT / "config" / "tiers.yaml")

    def test_하이쿠를_넣으면_거부한다(self):
        from llm.errors import LLMConfigError

        with pytest.raises(LLMConfigError) as exc:
            self._config("claude-haiku-4-5-20251001").model_for("S")
        assert "하이쿠" in str(exc.value) or "haiku" in str(exc.value)

    @pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-opus-5"])
    def test_소넷_이상은_통과한다(self, model):
        assert self._config(model).model_for("S") == model

    def test_설정에_정책이_적혀_있다(self):
        from llm.tiers import TierConfig

        policy = TierConfig.load().raw.get("model_policy") or {}
        assert policy.get("min_tier") == "sonnet", policy

    def test_금지_목록은_설정에서_읽는다(self):
        """코드에 모델 문자열을 박으면 vendor_lock_in_guards 를 어긴다."""
        source = (ROOT / "llm" / "tiers.py").read_text(encoding="utf-8")
        assert "haiku" not in source, "금지 모델 이름이 코드에 박혔다"
