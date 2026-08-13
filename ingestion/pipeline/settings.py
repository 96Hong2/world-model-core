"""config/ 로딩. 읽기만 한다 — 이 계층은 config 를 절대 쓰지 않는다."""

from __future__ import annotations

import functools
import pathlib
from dataclasses import dataclass
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
PARSED_DIR = ROOT / "data" / "parsed"

CONFIG_FILES = {
    "sources": "sources.yaml",
    "bd_seed": "bd-seed.yaml",
    "aliases": "aliases.yaml",
    "need_taxonomy": "need-taxonomy.yaml",
    "capability_taxonomy": "capability-taxonomy.yaml",
    "claim_verdict": "claim-verdict-rules.yaml",
    "criticality": "criticality-rules.yaml",
    "pii_patterns": "pii-patterns.yaml",
}


@dataclass(frozen=True)
class Settings:
    sources: dict[str, Any]
    bd_seed: dict[str, Any]
    aliases: dict[str, Any]
    need_taxonomy: dict[str, Any]
    capability_taxonomy: dict[str, Any]
    claim_verdict: dict[str, Any]
    criticality: dict[str, Any]
    pii_patterns: dict[str, Any]

    @classmethod
    def load(cls, config_dir: pathlib.Path | None = None) -> "Settings":
        return _load(str(config_dir or CONFIG_DIR))

    # -- sources -----------------------------------------------------------
    @functools.cached_property
    def source_index(self) -> dict[str, dict[str, Any]]:
        """개별 등록 + `source_bundles` 전개를 합친 등록부. 등록 없는 소스는 적재하지 않는다."""
        from ingestion.source_registry import expand_config

        return {entry["id"]: entry for entry in expand_config(self.sources)}

    # -- BD ----------------------------------------------------------------
    @functools.cached_property
    def bd_index(self) -> dict[str, dict[str, Any]]:
        return {bd["id"]: bd for bd in self.bd_seed["business_domains"]}

    @functools.cached_property
    def bd_name_index(self) -> dict[str, str]:
        """BD 이름·별칭 → bd_id. 신·구 명칭은 병합하지 않는다(DECISIONS A-5)."""
        index: dict[str, str] = {}
        for bd in self.bd_seed["business_domains"]:
            for name in [bd["name"], *(bd.get("aliases") or [])]:
                index[_key(name)] = bd["id"]
        return index

    def activity_domain_entry(self, raw: str) -> dict[str, Any]:
        for entry in self.bd_seed["activity_log_domain_map"]["entries"]:
            if entry["raw"] == raw:
                return entry
        return {"raw": raw, "bd_ids": [], "status": "unknown", "needs_human_confirm": True}

    def weekly_domain_entry(self, raw: str) -> dict[str, Any]:
        for entry in self.bd_seed["weekly_plan_domain_map"]["entries"]:
            if entry["raw"] == raw:
                return entry
        return {"raw": raw, "bd_ids": [], "status": "unknown", "needs_human_confirm": True}

    @functools.cached_property
    def activity_split_rules(self) -> list[str]:
        return list(self.bd_seed["activity_log_domain_map"].get("split_rules") or [])

    # -- taxonomy ----------------------------------------------------------
    @functools.cached_property
    def need_index(self) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in self.need_taxonomy["needs"]}

    @functools.cached_property
    def capability_index(self) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in self.capability_taxonomy["capabilities"]}


def _key(text: str) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize("NFKC", text or "")
    value = re.sub(r"\s+", "", value)
    return value.lower()


@functools.lru_cache(maxsize=4)
def _load(config_dir: str) -> Settings:
    base = pathlib.Path(config_dir)
    payload = {
        field: yaml.safe_load((base / filename).read_text(encoding="utf-8"))
        for field, filename in CONFIG_FILES.items()
    }
    return Settings(**payload)
