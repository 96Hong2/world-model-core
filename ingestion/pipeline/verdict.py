"""⑦ criticality 태깅 + ⑧ 상태 판정.

두 엔진 다 config 를 기계적으로 적용하기만 한다. 판정 근거를 코드에 새로 만들지 않는다.

  VerdictEngine      claim-verdict-rules.yaml 의 (source_type × claim_kind) + override 5종
  CriticalityEngine  criticality-rules.yaml 의 규칙 12종

상태의 뜻은 contracts/state.enum.json 이 정의한다. 핵심은 criticality 가 발화하면
빈도·근거 수와 무관하게 lane=critical 로 들어가 검색에서 사라지지 않는다는 것이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .settings import Settings

_NUMBER = re.compile(r"\d")


@dataclass(frozen=True)
class Verdict:
    status: str
    lane: tuple[str, ...]
    observation_confirmed: bool
    verified_allowed: bool
    claim_domain: str | None
    rule: str
    overrides: tuple[str, ...] = ()


class VerdictEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        rules = settings.claim_verdict
        self._defaults = rules["defaults"]
        self._rules = {
            (rule["source_type"], rule["claim_kind"]): rule for rule in rules["rules"]
        }
        self._overrides = sorted(rules["overrides"], key=lambda item: item["precedence"])

    def decide(
        self,
        *,
        source_type: str,
        claim_kind: str,
        extractor: str = "deterministic",
        criticality_fired: Sequence[str] = (),
        contradiction: bool = False,
        transcription_flags: Sequence[str] = (),
    ) -> Verdict:
        rule = self._rules.get((source_type, claim_kind))
        if rule is None:
            base = dict(self._defaults)
            rule_id = f"default({source_type}×{claim_kind})"
            claim_domain = None
        else:
            base = dict(rule)
            rule_id = f"{source_type}×{claim_kind}"
            claim_domain = rule.get("claim_domain")

        observation_confirmed = bool(base.get("observation_confirmed", True))
        verified_allowed = bool(base.get("verified_allowed", False))
        status = base.get("default_status") or ("VERIFIED" if verified_allowed else "CANDIDATE")
        lanes = list(base.get("lane") or self._defaults.get("lane") or ["default"])
        applied: list[str] = []

        for override in self._overrides:
            when = override.get("when") or {}
            if not self._matches(
                when,
                extractor=extractor,
                criticality_fired=criticality_fired,
                contradiction=contradiction,
                transcription_flags=transcription_flags,
            ):
                continue
            applied.append(override["id"])
            if "observation_confirmed" in override:
                observation_confirmed = bool(override["observation_confirmed"])
            if "verified_allowed" in override:
                verified_allowed = bool(override["verified_allowed"])
            for lane in override.get("force_lane_add") or []:
                if lane not in lanes:
                    lanes.append(lane)
            if override.get("force_status"):
                status = override["force_status"]
            if verified_allowed and override.get("force_status_when_verified_allowed"):
                status = override["force_status_when_verified_allowed"]
            if not verified_allowed and override.get("force_status_when_not_verified_allowed"):
                status = override["force_status_when_not_verified_allowed"]

        if not verified_allowed and status == "VERIFIED":
            # 규칙표 밖 조합이 VERIFIED 로 새는 것을 막는 마지막 빗장.
            status = "CANDIDATE"

        return Verdict(
            status=status,
            lane=tuple(lanes),
            observation_confirmed=observation_confirmed,
            verified_allowed=verified_allowed,
            claim_domain=claim_domain,
            rule=rule_id,
            overrides=tuple(applied),
        )

    @staticmethod
    def _matches(
        when: dict[str, Any],
        *,
        extractor: str,
        criticality_fired: Sequence[str],
        contradiction: bool,
        transcription_flags: Sequence[str],
    ) -> bool:
        if "extractor" in when and when["extractor"] != extractor:
            return False
        if "criticality_fired" in when and bool(criticality_fired) != bool(
            when["criticality_fired"]
        ):
            return False
        if "contradiction_detected" in when and bool(contradiction) != bool(
            when["contradiction_detected"]
        ):
            return False
        if "transcription_flag_in" in when:
            allowed = set(when["transcription_flag_in"])
            if not (allowed & set(transcription_flags)):
                return False
        if "entity_name_conflicts_with_document_subject" in when:
            # 파서가 표 셀 단위 교차검증 결과를 내보내지 않아 1A 에서는 발화하지 않는다.
            return False
        return True


# ---------------------------------------------------------------------------


@dataclass
class CriticalityReport:
    fired: tuple[str, ...] = ()
    lanes: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)


class CriticalityEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        rules = settings.criticality
        self._rules = rules["rules"]
        self._status_on_fire = rules["status_on_fire"]
        self._default_lane = rules.get("default_lane_on_fire", "critical")
        self.fire_counts: dict[str, int] = {}

    def evaluate(
        self,
        *,
        applies_to: str,
        text: str = "",
        fields: dict[str, Any] | None = None,
    ) -> list[str]:
        values = fields or {}
        fired: list[str] = []
        for rule in self._rules:
            if applies_to not in rule.get("applies_to", []):
                continue
            if self._condition(rule.get("condition") or {}, text, values):
                fired.append(rule["id"])
                self.fire_counts[rule["id"]] = self.fire_counts.get(rule["id"], 0) + 1
        return fired

    def rule(self, rule_id: str) -> dict[str, Any]:
        return next((rule for rule in self._rules if rule["id"] == rule_id), {})

    @staticmethod
    def is_tag_only(rule: dict[str, Any]) -> bool:
        """config 가 '차단이 아니라 태깅' 이라고 적은 규칙은 상태·lane 을 건드리지 않는다.

        CR-NO-OBSERVED-AT 이 그 경우다. 숫자가 있고 관측 시점이 없다는 것만으로 모든 주장을
        CRITICAL 로 올리면 상태 구분 자체가 사라진다. 캐비앗 표시는 답변 계층의 몫이다.
        """
        return "태깅" in str(rule.get("action") or "")

    def lanes_for(self, fired: Iterable[str]) -> list[str]:
        lanes: list[str] = []
        for rule_id in fired:
            rule = self.rule(rule_id)
            if self.is_tag_only(rule):
                continue
            lane = rule.get("lane_override", self._default_lane)
            if lane not in lanes:
                lanes.append(lane)
        return lanes

    def escalating(self, fired: Iterable[str]) -> list[str]:
        """상태를 CRITICAL/UNVERIFIED 로 올리는 규칙만 골라 낸다.

        lane_override 가 걸린 규칙(conflict)은 1A 에서 lane 만 부여한다.
        """
        out: list[str] = []
        for rule_id in fired:
            rule = self.rule(rule_id)
            if self.is_tag_only(rule) or rule.get("lane_override"):
                continue
            out.append(rule_id)
        return out

    # -- 조건 평가 ----------------------------------------------------------
    def _condition(self, condition: dict[str, Any], text: str, values: dict[str, Any]) -> bool:
        if not condition:
            return False
        if "any_of" in condition:
            return any(self._condition(item, text, values) for item in condition["any_of"])
        if "all_of" in condition:
            return all(self._condition(item, text, values) for item in condition["all_of"])
        if "text_contains_any" in condition:
            return any(term in text for term in condition["text_contains_any"])
        if "account_canonical_in" in condition:
            account = values.get("account_canonical")
            return bool(account) and account in condition["account_canonical_in"]
        if "seed_terms" in condition:
            # term_document_frequency 는 전량 적재 후에야 계산되지만, seed_terms 는 R0 실측이라
            # config 가 1패스 없이 즉시 발화하도록 정해 두었다.
            return any(term in text for term in condition["seed_terms"])
        if "subject_key_in" in condition:
            subject = values.get("subject_key")
            return bool(subject) and subject in condition["subject_key_in"]
        if "field" in condition:
            return self._field_condition(condition, values)
        return False

    @staticmethod
    def _field_condition(condition: dict[str, Any], values: dict[str, Any]) -> bool:
        name = condition["field"]
        operator = condition["operator"]
        actual = values.get(name)
        if operator == "is_null":
            return actual is None
        if operator == "is_null_but_raw_present":
            raw_name = name.replace("_krw", "_raw")
            return actual is None and bool(values.get(raw_name))
        if operator == "contains_number":
            return bool(actual) and bool(_NUMBER.search(str(actual)))
        if actual is None:
            return False
        if operator == ">=":
            try:
                return float(actual) >= float(condition["value"])
            except (TypeError, ValueError):
                return False
        if operator == "==":
            return actual == condition["value"]
        if operator == "in":
            return actual in condition["value"]
        return False

    # -- 상태 ---------------------------------------------------------------
    def status_for(self, *, verified_allowed: bool) -> str:
        key = "when_verified_allowed" if verified_allowed else "when_not_verified_allowed"
        return self._status_on_fire[key]
