"""제품 레포 지식 자산 파서 (읽기 전용).

sources.yaml 이 고른 파일만 읽는다. 레포는 절대 수정하지 않는다.

locator 는 레포 상대경로가 앞에 온다. 파일이 좌표의 일부여야 사람이 원본을 찾아갈 수 있다.
    md    path/to/file.md#섹션 제목
    adoc  path/to/file.adoc#명시앵커      (없으면 h2:헤딩 원문)
    ts    path/to/file.ts#L120

repo-inventory §1.3 이 지적한 대로 헤딩에 한글·괄호·백틱이 섞여 있어 slug 규칙에 기대면
재현이 깨진다. 그래서 헤딩 원문을 그대로 좌표에 쓰고 structured 에도 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ingestion.parsers.doc_common import (
    Unit,
    build_evidences,
    build_source_record,
    normalize_text,
    repo_base_dir,
)
from ingestion.parsers.doc_md import read_markdown_sections

ADOC_HEADING_RE = re.compile(r"^(={1,5})\s+(.*\S)\s*$")
ADOC_ANCHOR_RE = re.compile(r"^\[#([A-Za-z0-9_\-.]+)\]\s*$")
ADOC_FENCE_RE = re.compile(r"^-{4,}\s*$")
ROUTE_RE = re.compile(
    r"(?P<key>[A-Za-z_][\w]*)\s*:\s*defineRoute(?:<.*?>)?\(\s*'(?P<path>[^']+)'",
    re.S,
)


@dataclass(frozen=True)
class AdocSection:
    level: int
    title: str
    anchor: str | None
    path: tuple[str, ...]
    text: str
    line: int


@dataclass(frozen=True)
class RouteSymbol:
    key: str
    route: str
    line: int
    text: str


def _relpath(path: Path) -> str:
    base = repo_base_dir()
    try:
        return str(path.relative_to(base))
    except ValueError:
        return path.name


# --------------------------------------------------------------------------
# adoc
# --------------------------------------------------------------------------
def read_adoc_sections(path: Path) -> list[AdocSection]:
    lines = path.read_text(encoding="utf-8").split("\n")
    sections: list[AdocSection] = []
    stack: list[str] = []
    current: dict | None = None
    body: list[str] = []
    pending_anchor: str | None = None
    in_block = False

    def close() -> None:
        if current is not None:
            sections.append(
                AdocSection(
                    level=current["level"],
                    title=current["title"],
                    anchor=current["anchor"],
                    path=current["path"],
                    text="\n".join(body),
                    line=current["line"],
                )
            )

    for no, line in enumerate(lines, start=1):
        if ADOC_FENCE_RE.match(line):
            in_block = not in_block
        anchor = ADOC_ANCHOR_RE.match(line.strip()) if not in_block else None
        if anchor:
            pending_anchor = anchor.group(1)
            if current is not None:
                body.append(line)
            continue
        heading = ADOC_HEADING_RE.match(line) if not in_block else None
        if heading:
            close()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            del stack[level - 1 :]
            stack.append(title)
            current = {
                "level": level,
                "title": title,
                "anchor": pending_anchor,
                "path": tuple(stack),
                "line": no,
            }
            pending_anchor = None
            body = []
            continue
        if current is not None:
            body.append(line)
    close()
    return sections


# --------------------------------------------------------------------------
# TypeScript 라우트
# --------------------------------------------------------------------------
def read_route_symbols(path: Path) -> list[RouteSymbol]:
    text = path.read_text(encoding="utf-8")
    symbols: list[RouteSymbol] = []
    for m in ROUTE_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        snippet = re.sub(r"\s+", " ", text[m.start() : m.end() + 2]).strip()
        symbols.append(
            RouteSymbol(key=m.group("key"), route=m.group("path"), line=line, text=snippet)
        )
    return symbols


# --------------------------------------------------------------------------
def _preamble_unit(path: Path, rel: str, first_heading_line: int | None) -> Unit | None:
    """첫 헤딩 앞의 본문. 없으면 None.

    레포 adoc 12개는 헤딩이 하나도 없이 include 지시자와 속성 정의만 들어 있다
    (`:install-home-path: /home/appuser`). 헤딩 절만 뽑으면 이 파일들이 통째로 사라진다.
    문서 사이 포함 관계와 경로 기본값이 여기에만 있으므로 preamble 좌표로 남긴다.
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    head = lines if first_heading_line is None else lines[: first_heading_line - 1]
    text = normalize_text("\n".join(head))
    if not text:
        return None
    return Unit(
        locator=f"{rel}#preamble",
        unit="section",
        text=text,
        structured={
            "record_kind": "doc_section",
            "file": rel,
            "heading": "(preamble)",
            "heading_path": [],
            "level": 0,
            "line": 1,
        },
    )


def _md_units(path: Path, rel: str) -> list[Unit]:
    units: list[Unit] = []
    used: dict[str, int] = {}
    sections = read_markdown_sections(path)
    preamble = _preamble_unit(path, rel, sections[0].line if sections else None)
    if preamble is not None:
        units.append(preamble)
    for section in sections:
        text = normalize_text("\n".join([section.title, section.text]))
        if not text:
            continue
        locator = f"{rel}#{section.title}"
        used[locator] = used.get(locator, 0) + 1
        if used[locator] > 1:
            locator = f"{locator} ({used[locator]})"
        units.append(
            Unit(
                locator=locator,
                unit="section",
                text=text,
                structured={
                    "record_kind": "doc_section",
                    "file": rel,
                    "heading": section.title,
                    "heading_path": list(section.path),
                    "level": section.level,
                    "line": section.line,
                },
            )
        )
    return units


def _adoc_units(path: Path, rel: str) -> list[Unit]:
    units: list[Unit] = []
    used: dict[str, int] = {}
    sections = read_adoc_sections(path)
    preamble = _preamble_unit(path, rel, sections[0].line if sections else None)
    if preamble is not None:
        units.append(preamble)
    for section in sections:
        text = normalize_text("\n".join([section.title, section.text]))
        if not text:
            continue
        fragment = section.anchor or f"h{section.level}:{section.title}"
        locator = f"{rel}#{fragment}"
        used[locator] = used.get(locator, 0) + 1
        if used[locator] > 1:
            locator = f"{locator} ({used[locator]})"
        units.append(
            Unit(
                locator=locator,
                unit="section",
                text=text,
                structured={
                    "record_kind": "doc_section",
                    "file": rel,
                    "heading": section.title,
                    "heading_path": list(section.path),
                    "anchor": section.anchor,
                    "level": section.level,
                    "line": section.line,
                },
            )
        )
    return units


def _code_units(path: Path, rel: str) -> list[Unit]:
    return [
        Unit(
            locator=f"{rel}#L{sym.line}",
            unit="code_symbol",
            text=sym.text,
            structured={
                "record_kind": "code_asset",
                "file": rel,
                "symbol": sym.key,
                "route": sym.route,
                "line": sym.line,
            },
        )
        for sym in read_route_symbols(path)
    ]


def parse_source(cfg: dict) -> tuple[dict, list[dict]]:
    path = Path(cfg["path"])
    rel = _relpath(path)
    fmt = cfg["format"]

    if fmt == "md":
        units = _md_units(path, rel)
        parser = "code_asset.md"
    elif fmt == "adoc":
        units = _adoc_units(path, rel)
        parser = "code_asset.adoc"
    elif fmt == "ts":
        units = _code_units(path, rel)
        parser = "code_asset.code"
    else:
        raise ValueError(f"repo_seed 에서 지원하지 않는 형식: {fmt} ({cfg['id']})")

    evidences = build_evidences(cfg["id"], units)
    source = build_source_record(
        cfg,
        parser=parser,
        extractor="native",
        evidence_count=len(evidences),
        notes=f"{rel} · 단위 {len(units)}개",
    )
    return source, evidences
