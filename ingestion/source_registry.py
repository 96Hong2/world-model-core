"""config/sources.yaml 을 읽어 등록된 Source 전량을 돌려준다.

sources.yaml 은 두 가지 방식으로 Source 를 등록한다.

  sources:         파일 하나를 손으로 적은 항목. 골든 근거·파서 주의사항이 붙는다.
  source_bundles:  디렉토리와 glob 으로 묶어 적은 항목. 레포 문서처럼 수백 개라
                   손으로 적으면 오히려 빠뜨리는 것들이다.

번들은 파일 하나마다 개별 등록 항목과 똑같은 모양의 dict 로 펼쳐진다. 그래서 이 함수만
거치면 아래 계층(파서·파이프라인)은 두 방식의 차이를 몰라도 된다.

규칙 셋:
  - 개별 등록이 이긴다. 번들이 훑은 경로가 이미 개별 등록에 있으면 번들 쪽을 버린다.
  - id 는 `<id_prefix>_<루트 기준 상대경로 slug>` 로 결정된다. 같은 파일은 항상 같은 id 다.
  - overrides 는 경로 접두사로 걸린다. 민감 자료를 restricted 로 올리는 통로다.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.yaml"

# 번들 항목이 개별 등록 항목과 공유하는 필드. 번들 본문에 적으면 전개된 항목이 그대로 물려받는다.
INHERITED_FIELDS = (
    "source_group",
    "format",
    "source_type",
    "visibility",
    "sensitivity",
    "pii_flag",
    "extractor",
    "parser",
    "source_of_record_for",
    "doc_status",
    "origin",
    "pii_note",
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """source_id 에 쓸 수 있는 조각으로 바꾼다(contracts 의 `^src_[a-z0-9_]+$`)."""
    return _SLUG_STRIP.sub("_", text.lower()).strip("_")


def load_config(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8"))


def _matches(rel: str, pattern: str) -> bool:
    """overrides 의 경로 규칙. 접두사와 fnmatch 둘 다 받는다."""
    if pattern.endswith("/**"):
        return rel == pattern[:-3] or rel.startswith(pattern[:-2])
    return Path(rel).match(pattern) or rel.startswith(pattern.rstrip("/") + "/")


def expand_bundle(bundle: dict[str, Any], taken_paths: set[str]) -> list[dict[str, Any]]:
    root = Path(bundle["root"])
    if not root.exists():
        raise FileNotFoundError(f"번들 root 가 없다: {bundle.get('id')} → {root}")

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    excluded = list(bundle.get("exclude") or [])
    # rglob 이 아니라 glob 이다. 재귀는 include 패턴의 `**/` 이 정하게 두어야
    # `*.md` 로 그 폴더만 훑는 번들이 하위 폴더까지 삼키지 않는다.
    for path in sorted(root.glob(bundle["include"])):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if str(path) in taken_paths:
            continue  # 개별 등록이 이긴다
        if any(_matches(rel, pattern) for pattern in excluded):
            continue

        entry: dict[str, Any] = {
            k: bundle[k] for k in INHERITED_FIELDS if k in bundle
        }
        entry["id"] = f"{bundle['id_prefix']}_{slugify(rel.rsplit('.', 1)[0])}"
        entry["path"] = str(path)
        entry["from_bundle"] = bundle["id"]
        for rule in bundle.get("overrides") or []:
            if _matches(rel, rule["match"]):
                entry.update(
                    {k: v for k, v in rule.items() if k not in {"match", "reason"}}
                )
                entry["override_reason"] = rule.get("reason")
        if entry["id"] in seen_ids:
            raise ValueError(f"번들 안에서 source_id 가 겹친다: {entry['id']} ({rel})")
        seen_ids.add(entry["id"])
        entries.append(entry)
    return entries


def expand_config(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """이미 읽어 둔 sources.yaml dict 를 전체 Source 목록으로 펼친다."""
    explicit = list(cfg.get("sources") or [])
    taken = {str(Path(s["path"])) for s in explicit if s.get("path")}
    out = list(explicit)
    for bundle in cfg.get("source_bundles") or []:
        out.extend(expand_bundle(bundle, taken))
    ids = [s["id"] for s in out]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        raise ValueError(f"source id 중복: {duplicated}")
    return out


@functools.lru_cache(maxsize=4)
def _all_sources(config_key: str) -> tuple[dict[str, Any], ...]:
    return tuple(expand_config(load_config(Path(config_key))))


def all_sources(path: Path | None = None) -> list[dict[str, Any]]:
    """개별 등록 + 번들 전개를 합친 전체 Source 목록."""
    return [dict(s) for s in _all_sources(str(path or CONFIG_PATH))]


def source_index(path: Path | None = None) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in all_sources(path)}


def clear_cache() -> None:
    _all_sources.cache_clear()
