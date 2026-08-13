"""마크다운 문서 파서 (documents 그룹).

LeadFlow BD 분류 프롬프트처럼 문서로 등록된 마크다운을 헤딩 절 단위로 자른다.
코드펜스 안의 `#` 은 헤딩이 아니므로 세지 않는다.

locator 는 제목 경로 `§3. 시스템 프롬프트`.
레포 지식 자산(md·adoc)은 파일 경로가 좌표에 들어가야 해서 code_asset 이 따로 맡는다.
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
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class MdSection:
    level: int
    title: str
    path: tuple[str, ...]
    text: str
    line: int


def _clean_title(title: str) -> str:
    # 마크다운 이스케이프(`## 0\. 이 문서의 성격`)를 좌표에서 걷어낸다
    return re.sub(r"\\([.\-*_])", r"\1", title).strip()


def read_markdown_sections(path: Path) -> list[MdSection]:
    lines = path.read_text(encoding="utf-8").split("\n")
    sections: list[MdSection] = []
    stack: list[str] = []
    current: dict | None = None
    body: list[str] = []
    in_fence = False

    def close() -> None:
        if current is not None:
            sections.append(
                MdSection(
                    level=current["level"],
                    title=current["title"],
                    path=current["path"],
                    text="\n".join(body),
                    line=current["line"],
                )
            )

    for no, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else HEADING_RE.match(line)
        if heading:
            close()
            level = len(heading.group(1))
            title = _clean_title(heading.group(2))
            del stack[level - 1 :]
            stack.append(title)
            current = {"level": level, "title": title, "path": tuple(stack), "line": no}
            body = []
            continue
        if current is not None:
            body.append(line)
    close()
    return sections


def _units(sections: list[MdSection]) -> list[Unit]:
    units: list[Unit] = []
    used: dict[str, int] = {}
    for section in sections:
        text = normalize_text("\n".join([section.title, section.text]))
        if not text:
            continue
        locator = "§" + ">".join(section.path)
        used[locator] = used.get(locator, 0) + 1
        if used[locator] > 1:
            locator = f"{locator} ({used[locator]})"
        units.append(Unit(locator=locator, unit="section", text=text))
    return units


def parse_source(cfg: dict) -> tuple[dict, list[dict]]:
    sections = read_markdown_sections(Path(cfg["path"]))
    evidences = build_evidences(cfg["id"], _units(sections))
    source = build_source_record(
        cfg,
        parser="doc_md",
        extractor="native",
        evidence_count=len(evidences),
        notes=f"헤딩 {len(sections)}개 절로 분해",
    )
    return source, evidences
