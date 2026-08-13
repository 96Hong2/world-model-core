"""문서·Slack·코드 파서 공용 도구.

내보내는 형식은 contracts/parsed_record.schema.json 이 정본이다. 이 모듈은 그 계약을
그대로 채우는 조립기만 제공하고, 계약 자체를 해석해 바꾸지 않는다.

원칙 셋:
  - 원본 파일을 파싱한다. 추출 txt 는 원본에서 텍스트가 나오지 않는 이미지 PDF 예외에만 쓴다.
  - 마스킹은 발췌를 만들기 전에 끝낸다. 마스킹 전 문자열은 어떤 반환값에도 담기지 않는다.
  - evidence_id 는 source_id 와 locator 만으로 정해진다. 같은 입력을 두 번 파싱하면 같은 집합이 나온다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ingestion.masking import default_masker
from ingestion.source_registry import all_sources

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "sources.yaml"
PII_CONFIG_PATH = ROOT / "config" / "pii-patterns.yaml"
PARSED_DIR = ROOT / "data" / "parsed"

PARSER_VERSION = "1.0.0"
EXCERPT_MAX = 500

# A2b 담당 소스 그룹. xlsx 계열(featuremap·sales_xlsx·bd_registry·pain_registry)은 A2a 몫이다.
OWNED_GROUPS = ("documents", "slack", "repo_seed")

# sources.yaml 의 값 → contracts/parsed_record.schema.json 의 enum
FORMAT_MAP = {
    "pdf": "pdf",
    "docx": "docx",
    "pptx": "pptx",
    "html": "html",
    "md": "md",
    "adoc": "adoc",
    "jsonl": "slack_jsonl",
    "ts": "code",
    "xlsx": "xlsx",
    "txt": "txt",
}
# sources.yaml 은 sensitivity 를 internal/restricted 로 쓰고 계약은 low/medium/high 를 요구한다.
SENSITIVITY_MAP = {"public": "low", "internal": "medium", "restricted": "high"}
# sources.yaml 의 doc_status: approved 는 계약 enum 에 없다. 승인본이므로 final 로 옮긴다.
DOC_STATUS_MAP = {
    "approved": "final",
    "final": "final",
    "draft": "draft",
    "wip": "wip",
    "deprecated": "deprecated",
}
# 고객사가 작성한 문서. 나머지는 사내 작성이다.
CUSTOMER_AUTHORED = {"src_doc_proposal_hankook_capital", "src_doc_proposal_woori_capital"}

_HANGUL = re.compile(r"[가-힣]")


# ==========================================================================
# 설정 읽기
# ==========================================================================
def load_sources_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def load_sources() -> dict[str, dict]:
    """id → 소스 등록 정보. `source_bundles` 로 묶어 적은 것도 펼쳐서 함께 준다."""
    return {s["id"]: s for s in all_sources(CONFIG_PATH)}


def repo_base_dir() -> Path:
    return Path(load_sources_config()["base_dirs"]["repo"])


def email_allowlist() -> list[str]:
    """개인 식별이 불가능한 역할 메일만 예외다 (DECISIONS A-3)."""
    doc = yaml.safe_load(PII_CONFIG_PATH.read_text(encoding="utf-8"))
    for det in doc.get("detectors", []):
        if det.get("id") == "PII-EMAIL":
            return list((det.get("allowlist") or {}).get("values", []))
    return []


# ==========================================================================
# 마스킹 (A2a 의 ingestion/masking.py 를 그대로 쓴다)
# ==========================================================================
def mask_text(text: str) -> tuple[str, list[dict]]:
    """마스킹된 문자열과 pii_hits 를 돌려준다."""
    masked = default_masker().mask(text)
    return masked.text, masked.hit_dicts()


def phone_hits(text: str) -> int:
    return default_masker().phone_hits(text)


# ==========================================================================
# Evidence 조립
# ==========================================================================
@dataclass(frozen=True)
class Unit:
    """locator 하나에 대응하는 원본 조각.

    `text` 를 이미 마스킹해서 넘긴 경우(구조 분해를 마스킹 뒤에 하는 Slack 이 그렇다)
    그때 센 탐지 통계를 `pii_hits` 로 함께 넘긴다. 안 넘기면 그 통계가 사라져
    "무엇을 몇 개 가렸는지"를 확인할 수 없다.
    """

    locator: str
    unit: str
    text: str
    structured: dict | None = None
    authored_at: str | None = None
    author_name: str | None = None
    pii_hits: tuple[tuple[str, int], ...] = ()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_evidence_id(source_id: str, locator: str) -> str:
    return "ev_" + sha256_text(f"{source_id}|{locator}")[:16]


# 파이썬이 줄바꿈으로 세지만 JSON 은 문자열 안 평범한 문자로 취급하는 것들.
# PowerPoint 의 텍스트 프레임은 문단 안 줄바꿈을 `\x0b` 로 준다(실측: 협업센터 매뉴얼 70건).
# 이걸 그대로 두면 jsonl 한 줄이 str.splitlines() 에서 두 줄로 쪼개져 적재가 통째로 깨진다.
EXOTIC_LINE_BREAKS = str.maketrans(
    {c: "\n" for c in "\x0b\x0c\x1c\x1d\x1e\x85  "}
)


def normalize_text(text: str) -> str:
    """줄 끝 공백과 3줄 이상 연속 빈 줄만 정리한다. 내용은 건드리지 않는다.

    줄바꿈 표기는 `\\n` 하나로 모은다(CRLF·CR·수직탭·U+2028 등).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(EXOTIC_LINE_BREAKS)
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def split_excerpts(text: str, limit: int = EXCERPT_MAX) -> list[str]:
    """의미 단위(줄/불릿)를 지키며 limit 이하로 자른다. 잘린 뒤에도 원문 전량이 남는다."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        add = len(line) + (1 if current else 0)
        if current_len + add > limit:
            chunks.append("\n".join(current))
            current, current_len = [line], len(line)
        else:
            current.append(line)
            current_len += add
    if current:
        chunks.append("\n".join(current))
    return [c for c in (chunk.strip() for chunk in chunks) if c]


def build_evidence(source_id: str, unit: Unit) -> list[dict]:
    """Unit 하나 → Evidence 1건 이상. 500자를 넘으면 이어지는 조각에 #n 을 붙인다."""
    masked, hits = mask_text(unit.text)
    masked = normalize_text(masked)
    parts = split_excerpts(masked)
    if not parts:
        return []
    if unit.pii_hits:
        counter = {kind: count for kind, count in unit.pii_hits}
        for hit in hits:
            counter[hit["kind"]] = counter.get(hit["kind"], 0) + hit["count"]
        hits = [{"kind": k, "count": v} for k, v in sorted(counter.items())]

    raw_len = len(masked)
    lang = "ko" if _HANGUL.search(masked) else "en"
    records: list[dict] = []
    for idx, part in enumerate(parts, start=1):
        locator = unit.locator if idx == 1 else f"{unit.locator}#{idx}"
        record = {
            "evidence_id": make_evidence_id(source_id, locator),
            "source_id": source_id,
            "locator": locator,
            "unit": unit.unit,
            "excerpt": part,
            "excerpt_hash": sha256_text(part),
            "authored_at": unit.authored_at,
            "author_name": unit.author_name,
            "pii_masked": True,
            # 탐지 통계는 Unit 단위로 센다. 이어지는 조각에 중복 계상하지 않는다.
            "pii_hits": hits if idx == 1 else [],
            "structured": unit.structured if idx == 1 else None,
            "raw_len": raw_len,
            "lang": lang,
        }
        records.append(record)
    return records


def build_evidences(source_id: str, units: list[Unit]) -> list[dict]:
    out: list[dict] = []
    for unit in units:
        out.extend(build_evidence(source_id, unit))
    return out


# ==========================================================================
# Source 조립
# ==========================================================================
def build_source_record(
    cfg: dict,
    *,
    parser: str,
    extractor: str,
    evidence_count: int,
    content_path: Path | None = None,
    notes: str | None = None,
) -> dict:
    origin_path = Path(cfg["path"])
    hash_path = content_path or origin_path
    stat = hash_path.stat()
    sensitivity = SENSITIVITY_MAP[cfg["sensitivity"]]
    doc_status = DOC_STATUS_MAP.get(cfg.get("doc_status")) if cfg.get("doc_status") else None

    return {
        "source_id": cfg["id"],
        "source_type": cfg["source_type"],
        "format": FORMAT_MAP[cfg["format"]],
        "canonical_location": str(origin_path),
        "version": cfg.get("version"),
        "content_hash": sha256_file(hash_path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "origin": "customer" if cfg["id"] in CUSTOMER_AUTHORED else "internal",
        "author_role": cfg.get("origin"),
        "doc_status": doc_status,
        "acl_visibility": cfg["visibility"],
        "sensitivity": sensitivity,
        "pii_flag": bool(cfg.get("pii_flag")),
        "source_of_record_for": [cfg["source_of_record_for"]]
        if isinstance(cfg.get("source_of_record_for"), str)
        else list(cfg.get("source_of_record_for") or []),
        "parser": parser,
        "parser_version": PARSER_VERSION,
        "parsed_at": datetime.now(tz=timezone.utc).isoformat(),
        "evidence_count": evidence_count,
        "extractor": extractor,
        "notes": notes,
    }


# ==========================================================================
# 실행
# ==========================================================================
def _dispatch(cfg: dict):
    from ingestion.parsers import (
        code_asset,
        doc_html,
        doc_md,
        doc_office,
        doc_pdf,
        doc_table,
        slack_dump,
    )

    fmt = cfg["format"]
    group = cfg["source_group"]
    if group == "repo_seed":
        return code_asset.parse_source
    if group == "slack":
        return slack_dump.parse_source
    if fmt == "pdf":
        return doc_pdf.parse_source
    if fmt in ("docx", "pptx"):
        return doc_office.parse_source
    if fmt == "html":
        return doc_html.parse_source
    if fmt == "md":
        return doc_md.parse_source
    if fmt == "xlsx":
        return doc_table.parse_source
    raise ValueError(f"파서를 찾을 수 없다: {cfg['id']} (format={fmt}, group={group})")


def owned_sources() -> list[dict]:
    return [s for s in all_sources(CONFIG_PATH) if s.get("source_group") in OWNED_GROUPS]


def parse_source(cfg: dict) -> tuple[dict, list[dict]]:
    return _dispatch(cfg)(cfg)


def parse_owned_sources() -> dict[str, tuple[dict, list[dict]]]:
    return {cfg["id"]: parse_source(cfg) for cfg in owned_sources()}


def write_parsed(source: dict, evidences: list[dict], out_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = out_dir or PARSED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = out_dir / f"{source['source_id']}.source.json"
    jsonl_path = out_dir / f"{source['source_id']}.jsonl"
    source_path.write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for ev in evidences:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return source_path, jsonl_path
