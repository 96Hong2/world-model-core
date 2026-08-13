"""config/tiers.yaml 바인딩.

모델 id 는 이 저장소에서 config/tiers.yaml 에만 적는다는 규칙(tiers.yaml
vendor_lock_in_guards)을 코드로 지킨다. llm/ 안에 모델 문자열을 하드코딩하지 않는다.

`model_policy` 도 여기서 지킨다. 금지 모델이 tier 에 걸려 있으면 tier 를 읽는 순간 거부한다.
금지 목록 자체도 설정에서 읽으므로 이 파일에는 모델 이름이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .errors import LLMConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIERS_PATH = REPO_ROOT / "config" / "tiers.yaml"


@dataclass(frozen=True)
class TierConfig:
    raw: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TierConfig":
        resolved = Path(path) if path is not None else DEFAULT_TIERS_PATH
        return _load_cached(str(resolved))

    # -- tier --------------------------------------------------------------
    def _tier(self, tier: str) -> dict[str, Any]:
        tiers = self.raw.get("tiers") or {}
        if tier not in tiers:
            raise KeyError(f"tiers.yaml 에 없는 tier: {tier!r} (있는 것: {sorted(tiers)})")
        return tiers[tier]

    def model_for(self, tier: str) -> str:
        model = self._tier(tier).get("model")
        if not model:
            raise KeyError(f"tier {tier!r} 에 model 이 없다")
        self._assert_allowed(tier, str(model))
        return str(model)

    # -- 모델 정책 ---------------------------------------------------------
    def forbidden_model_prefixes(self) -> tuple[str, ...]:
        policy = self.raw.get("model_policy") or {}
        return tuple(policy.get("forbidden_model_prefixes") or ())

    def min_tier(self) -> str | None:
        policy = self.raw.get("model_policy") or {}
        value = policy.get("min_tier")
        return str(value) if value else None

    def _assert_allowed(self, tier: str, model: str) -> None:
        """금지 모델을 tier 에 꽂아 두면 거부한다.

        금지 목록은 설정에서 읽는다 — 모델 이름을 코드에 박으면 vendor_lock_in_guards 를 어긴다.
        """
        for prefix in self.forbidden_model_prefixes():
            if model.startswith(prefix):
                minimum = self.min_tier() or "sonnet"
                raise LLMConfigError(
                    f"tier {tier!r} 에 금지된 모델이 걸려 있다: {model!r}. "
                    f"{self.path} 의 model_policy 가 최소 {minimum} 이상을 요구한다. "
                    "비용이 아니라 품질이 기준이다."
                )

    def max_output_tokens(self, tier: str) -> int:
        return int(self._tier(tier).get("max_output_tokens", 4096))

    def measured_cost_per_call_usd(self, tier: str) -> float | None:
        value = self._tier(tier).get("measured_cost_per_call_usd")
        return None if value is None else float(value)

    def tier_of_model(self, model: str) -> str | None:
        for name, body in (self.raw.get("tiers") or {}).items():
            if body.get("model") == model:
                return name
        return None

    # -- provider ----------------------------------------------------------
    @property
    def default_provider(self) -> str:
        return str((self.raw.get("providers") or {}).get("default", "claude_cli"))

    @property
    def available_providers(self) -> list[str]:
        entries = (self.raw.get("providers") or {}).get("available") or []
        return [str(item.get("id")) for item in entries if item.get("id")]

    # -- 재검증·재시도 -------------------------------------------------------
    @property
    def revalidate_structured_output(self) -> bool:
        return bool((self.raw.get("structured_output") or {}).get("revalidate", True))


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> TierConfig:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"tiers 설정을 찾을 수 없다: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return TierConfig(raw=data, path=path)
