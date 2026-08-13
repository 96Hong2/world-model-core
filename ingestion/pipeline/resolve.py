"""④ Entity Resolution + Need/Capability canonical 매핑.

규칙은 두 줄이다.
  · 이름은 `config/aliases.yaml` 의 exact match 로만 합친다. 부분 문자열·유사도 병합 경로를
    코드에 만들지 않는다(마바손해보험 ≠ 마바캐피탈).
  · Need·Capability 의 canonical 항목은 taxonomy 파일에만 존재한다. 사전에 없으면
    unmapped 로 기록만 하고 새 canonical 을 만들지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from .settings import Settings

# aliases.yaml normalize.steps 를 코드로 옮긴 것. 매칭 키를 만들 때만 쓰고 표시 이름은 원문을 남긴다.
_CORP_AFFIX = re.compile(r"(\(주\)|㈜|주식회사|\(株\)|Inc\.?|Co\.?|Ltd\.?)", re.IGNORECASE)
_PAREN = re.compile(r"[\(（][^)）]*[\)）]")
_SPACE = re.compile(r"\s+")


def clean_display(name: str) -> str:
    """법인 접두·접미와 괄호 주석을 뗀 표시용 이름."""
    value = unicodedata.normalize("NFKC", name or "").strip()
    value = _PAREN.sub(" ", value)
    value = _CORP_AFFIX.sub(" ", value)
    return _SPACE.sub(" ", value).strip(" -·,")


def match_key(name: str) -> str:
    """비교용 키. 공백까지 지워 표기 흔들림을 흡수한다."""
    return _SPACE.sub("", clean_display(name)).lower()


def text_key(text: str) -> str:
    """문장 비교용 키. 구두점·불릿·공백을 지운다."""
    value = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s·•\-–—,.()\[\]/\"'’“”]+", "", value).lower()


class AliasConflictError(ValueError):
    """do_not_merge 로 지정된 쌍이 정규화만으로 같은 키가 된다."""


@dataclass(frozen=True)
class AccountRef:
    canonical_name: str
    raw_name: str
    matched_alias: bool
    excluded: bool = False
    exclusion_reason: str = ""


@dataclass(frozen=True)
class NeedMapping:
    need_id: str | None
    name: str | None
    need_type: str
    canonical: bool
    unmapped_raw: str = ""
    matched_expression: str = ""


@dataclass(frozen=True)
class CapabilityMapping:
    capability_id: str | None
    name: str | None
    canonical: bool
    unmapped_raw: str = ""


@dataclass
class ResolutionLog:
    unknown_accounts: Counter = field(default_factory=Counter)
    excluded_accounts: Counter = field(default_factory=Counter)
    rejected_counterparts: Counter = field(default_factory=Counter)
    unmapped_needs: Counter = field(default_factory=Counter)
    unmapped_capabilities: Counter = field(default_factory=Counter)
    unmapped_domains: Counter = field(default_factory=Counter)


# 미팅 제목에서 뽑힌 상대 이름은 제목 문장이 통째로 들어오는 경우가 있다.
# 문장으로 보이는 값은 Account 로 만들지 않고 기록만 한다(1B 검토 큐).
_SENTENCE_MARKS = ("\n", "?", "!", ":", "*", "…")


def looks_like_company(name: str) -> bool:
    value = (name or "").strip()
    if not (2 <= len(value) <= 20):
        return False
    if any(mark in value for mark in _SENTENCE_MARKS):
        return False
    if re.search(r"[-–—]", value) and " " in value:
        return False
    return len(value.split()) <= 3


class Resolver:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.log = ResolutionLog()

        aliases = settings.aliases
        self._alias_index: dict[str, str] = {}
        for entry in aliases["accounts"]:
            canonical = entry["canonical"]
            self._alias_index[match_key(canonical)] = canonical
            for variant in entry.get("variants") or []:
                self._alias_index[match_key(variant)] = canonical

        self._excluded: dict[str, str] = {}
        for entry in aliases.get("exclusions") or []:
            values = entry.get("values") or ([entry["value"]] if entry.get("value") else [])
            for value in values:
                self._excluded[match_key(value)] = entry.get("reason", "")

        self._self_names = {
            match_key(v)
            for entry in aliases.get("exclusions") or []
            for v in (entry.get("values") or [])
            if "자사" in (entry.get("reason") or "")
        }

        self._assert_do_not_merge(aliases.get("do_not_merge") or [])

        self._competitors = {
            match_key(entry["canonical"]): entry for entry in aliases.get("competitors") or []
        }
        for entry in aliases.get("competitors") or []:
            for variant in entry.get("variants") or []:
                self._competitors[match_key(variant)] = entry

        self._partners = {
            match_key(entry["canonical"]): entry for entry in aliases.get("partners") or []
        }
        for entry in aliases.get("partners") or []:
            for variant in entry.get("variants") or []:
                self._partners[match_key(variant)] = entry

        self._products: dict[str, dict[str, Any]] = {}
        for entry in (aliases.get("product_terms") or {}).get("products") or []:
            self._products[match_key(entry["canonical"])] = entry
            for variant in entry.get("variants") or []:
                self._products[match_key(variant)] = entry

        # 이름 표기가 흔들려도 같은 노드로 가도록 처음 본 표시 이름을 기억한다.
        self._display: dict[str, str] = {}

        self._need_index: dict[str, dict[str, Any]] = {}
        for need in settings.need_taxonomy["needs"]:
            for expression in need.get("raw_expressions") or []:
                self._need_index.setdefault(text_key(expression), need)
            self._need_index.setdefault(text_key(need["name"]), need)

        self._capability_index: dict[str, dict[str, Any]] = {}
        for capability in settings.capability_taxonomy["capabilities"]:
            self._capability_index.setdefault(text_key(capability["name"]), capability)

    # -- 시작 시 자기 검사 --------------------------------------------------
    def _assert_do_not_merge(self, pairs: Iterable[dict[str, Any]]) -> None:
        for entry in pairs:
            left, right = entry["pair"]
            if match_key(left) == match_key(right):
                raise AliasConflictError(
                    f"정규화만으로 {left} 와 {right} 가 같은 키가 된다. 정규화 규칙을 좁혀야 한다."
                )
            if self._alias_index.get(match_key(left)) == self._alias_index.get(
                match_key(right)
            ) and match_key(left) in self._alias_index:
                raise AliasConflictError(f"alias 사전이 {left} 와 {right} 를 같은 canonical 로 묶었다")

    # -- Account -----------------------------------------------------------
    def resolve_account(self, raw: str, *, from_title: bool = False) -> AccountRef | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        key = match_key(raw)
        if not key:
            return None
        if key in self._excluded:
            self.log.excluded_accounts[raw] += 1
            return AccountRef(raw, raw, False, True, self._excluded[key])

        canonical = self._alias_index.get(key)
        if canonical is not None:
            self._display.setdefault(key, canonical)
            return AccountRef(canonical, raw, True)

        if from_title and not looks_like_company(raw) and key not in self._display:
            self.log.rejected_counterparts[raw] += 1
            return None

        display = self._display.get(key)
        if display is None:
            display = clean_display(raw) or raw
            self._display[key] = display
            self.log.unknown_accounts[display] += 1
        return AccountRef(display, raw, False)

    def resolve_competitor(self, raw: str) -> dict[str, Any] | None:
        return self._competitors.get(match_key(raw))

    def resolve_partner(self, raw: str) -> dict[str, Any] | None:
        return self._partners.get(match_key(raw))

    def products_in(self, text: str) -> list[dict[str, Any]]:
        """발췌 안에 실제로 등장한 제품만 돌려준다(근거 없는 FOR_PRODUCT 방지)."""
        found: list[dict[str, Any]] = []
        haystack = unicodedata.normalize("NFKC", text or "").lower()
        for entry in (self.settings.aliases.get("product_terms") or {}).get("products") or []:
            names = [entry["canonical"], *(entry.get("variants") or [])]
            if any(name.lower() in haystack for name in names):
                if entry not in found:
                    found.append(entry)
        return found

    # -- Need / Capability --------------------------------------------------
    def map_need(self, raw: str, *, default_type: str = "pain") -> NeedMapping:
        for line in _statement_lines(raw):
            need = self._need_index.get(text_key(line))
            if need is not None:
                return NeedMapping(
                    need_id=need["id"],
                    name=need["name"],
                    need_type=need["need_type"],
                    canonical=True,
                    matched_expression=line,
                )
        self.log.unmapped_needs[(raw or "").strip()[:120]] += 1
        return NeedMapping(
            need_id=None,
            name=None,
            need_type=default_type,
            canonical=False,
            unmapped_raw=(raw or "").strip()[:500],
        )

    def map_capability(self, raw: str) -> CapabilityMapping:
        capability = self._capability_index.get(text_key(raw))
        if capability is not None:
            return CapabilityMapping(capability["id"], capability["name"], True)
        self.log.unmapped_capabilities[(raw or "").strip()[:120]] += 1
        return CapabilityMapping(None, None, False, (raw or "").strip()[:500])

    def capability_by_id(self, capability_id: str) -> dict[str, Any] | None:
        return self.settings.capability_index.get(capability_id)

    def need_by_id(self, need_id: str) -> dict[str, Any] | None:
        return self.settings.need_index.get(need_id)

    # -- BusinessDomain -----------------------------------------------------
    def map_activity_domain(self, raw: str) -> tuple[list[str], dict[str, Any]]:
        """활동일지 업무 도메인 값 → bd_id 목록.

        `needs_human_confirm` 인 값은 빈 목록을 돌려준다. 근거가 확정되기 전에는
        IN_DOMAIN 엣지를 만들지 않는다(DECISIONS A-6 의 '상담' 74건).
        """
        entry = self.settings.activity_domain_entry(raw)
        if entry.get("needs_human_confirm"):
            self.log.unmapped_domains[raw] += 1
            return [], entry
        bd_ids = [bid for bid in (entry.get("bd_ids") or []) if bid in self.settings.bd_index]
        if not bd_ids:
            self.log.unmapped_domains[raw] += 1
        return bd_ids, entry

    def map_weekly_domain(self, raw: str) -> tuple[list[str], dict[str, Any]]:
        entry = self.settings.weekly_domain_entry(raw)
        if entry.get("needs_human_confirm"):
            self.log.unmapped_domains[raw] += 1
            return [], entry
        bd_ids = [bid for bid in (entry.get("bd_ids") or []) if bid in self.settings.bd_index]
        if not bd_ids:
            self.log.unmapped_domains[raw] += 1
        return bd_ids, entry

    def bd_by_name(self, raw: str) -> str | None:
        return self.settings.bd_name_index.get(_bd_key(raw))


def _bd_key(name: str) -> str:
    value = unicodedata.normalize("NFKC", name or "")
    return _SPACE.sub("", value).lower()


def _statement_lines(raw: str) -> list[str]:
    """pain 셀은 여러 불릿이 한 칸에 들어 있다. '원천 요구' 부연은 사전 매칭에서 제외한다.

    줄바꿈이 공백으로 뭉개진 뒤에 넘어오는 경로가 있어서 불릿 기호로도 자른다.
    가운뎃점(·)은 '통제·관리' 처럼 낱말 안에 쓰이므로 자르지 않는다.
    """
    lines: list[str] = []
    for chunk in (raw or "").split("\n"):
        for line in chunk.split("•"):
            stripped = line.strip().lstrip("·-").strip()
            if not stripped or stripped.startswith("원천 요구"):
                continue
            lines.append(stripped)
    if not lines and (raw or "").strip():
        lines.append(raw.strip())
    return lines
