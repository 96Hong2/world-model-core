"""HTML 파서.

docs-inventory §0-1 이 실측한 대로 HTML 4종에는 locator 마커가 하나도 없다. 그래서 원본을
다시 파싱해 두 가지 규칙 중 하나로 좌표를 만든다.

  direct    : 원본에 heading 이 살아 있는 문서(신한EZ손보 제안서, sections 17 / headings 20).
              제목 경로를 그대로 locator 로 쓴다 → `§솔루션 제안>제안방안 01 · …`
  unescape  : 본문이 escape 된 채 스크립트 데이터에 박혀 있어 heading 이 0개인 문서
              (Q&A 덱). 판정은 실측으로 한다 — 직파싱 116자 vs unescape 후 10,315자.
              이 경우 문서 자체 번호 라벨(INTRO 01~04 / MAIN Q1~Q6 / APPENDIX / REVIEW)을
              locator 로 쓴다.

두 규칙 중 어느 쪽인지는 원문 길이 비로 정한다(추측하지 않는다).
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from ingestion.parsers.doc_common import (
    Unit,
    build_evidences,
    build_source_record,
    normalize_text,
)

SPLIT_HEADINGS = ["h1", "h2", "h3", "h4"]
# docs-inventory 가 센 기준(h1~h3). 실측치와 대조하려면 같은 기준으로 세야 한다.
COUNT_HEADINGS = ["h1", "h2", "h3"]
UNESCAPE_GAIN = 1.5

# Q&A 덱의 자체 번호 라벨. 상단 목차 카드에도 같은 낱말이 있으나 그쪽은 이모지 접두가 없다.
QNA_SECTION_RE = re.compile(r"^[\U0001F300-\U0001FAFF☀-➿]️?\s*(?:★\s*)?(INTRO|MAIN|APPENDIX|REVIEW)\b")
QNA_ITEM_RE = re.compile(r"^(Q\d+)$")
QNA_NUMBER_RE = re.compile(r"^(0[1-9])$")


@dataclass
class HtmlDoc:
    path: Path
    mode: str  # direct | unescape
    lines: list[str]
    headings: list[tuple[int, str]] = field(default_factory=list)
    section_count: int = 0
    heading_count: int = 0


@dataclass(frozen=True)
class LabelUnit:
    label: str
    text: str


def _soup_text_lines(soup: BeautifulSoup) -> list[str]:
    for tag in soup(["script", "style"]):
        tag.decompose()
    return [ln.strip() for ln in soup.get_text("\n").splitlines() if ln.strip()]


def read_html(path: Path) -> HtmlDoc:
    raw = path.read_bytes().decode("utf-8", errors="replace")

    direct_soup = BeautifulSoup(raw, "html.parser")
    direct_lines = _soup_text_lines(BeautifulSoup(raw, "html.parser"))

    unescaped = raw.replace("\\u003c", "<").replace("\\u003e", ">").replace('\\"', '"')
    unescaped_soup = BeautifulSoup(html_mod.unescape(unescaped), "html.parser")
    unescaped_lines = _soup_text_lines(BeautifulSoup(html_mod.unescape(unescaped), "html.parser"))

    direct_len = sum(len(ln) for ln in direct_lines)
    unescaped_len = sum(len(ln) for ln in unescaped_lines)

    if unescaped_len > direct_len * UNESCAPE_GAIN:
        return HtmlDoc(path=path, mode="unescape", lines=unescaped_lines)

    headings = [
        (int(tag.name[1]), tag.get_text(" ", strip=True))
        for tag in direct_soup.find_all(SPLIT_HEADINGS)
        if tag.get_text(" ", strip=True)
    ]
    return HtmlDoc(
        path=path,
        mode="direct",
        lines=direct_lines,
        headings=headings,
        section_count=len(direct_soup.find_all(["section", "article"])),
        heading_count=len(direct_soup.find_all(COUNT_HEADINGS)),
    )


# --------------------------------------------------------------------------
# direct 모드 — 제목 경로 locator
# --------------------------------------------------------------------------
def heading_units(doc: HtmlDoc) -> list[LabelUnit]:
    pending = list(doc.headings)
    path: list[str] = []
    units: list[LabelUnit] = []
    body: list[str] = []
    label = "§머리말"

    for line in doc.lines:
        if pending and line == pending[0][1]:
            if body:
                units.append(LabelUnit(label=label, text="\n".join(body)))
                body = []
            level, title = pending.pop(0)
            del path[level - 1 :]
            path.append(title)
            label = "§" + ">".join(path)
            continue
        body.append(line)
    if body:
        units.append(LabelUnit(label=label, text="\n".join(body)))

    # 같은 제목 경로가 반복되면 뒤에 번호를 붙여 좌표를 유일하게 만든다
    seen: dict[str, int] = {}
    out: list[LabelUnit] = []
    for unit in units:
        seen[unit.label] = seen.get(unit.label, 0) + 1
        label = unit.label if seen[unit.label] == 1 else f"{unit.label} ({seen[unit.label]})"
        out.append(LabelUnit(label=label, text=unit.text))
    return out


# --------------------------------------------------------------------------
# unescape 모드 — 문서 자체 번호 라벨 locator
# --------------------------------------------------------------------------
def qna_label_units(doc: HtmlDoc) -> list[LabelUnit]:
    units: list[LabelUnit] = []
    section: str | None = None
    item: str | None = None
    body: list[str] = []

    def label_of() -> str:
        if section is None:
            return "COVER"
        return f"{section}/{item}" if item else section

    def flush() -> None:
        nonlocal body
        if body:
            units.append(LabelUnit(label=label_of(), text="\n".join(body)))
            body = []

    for line in doc.lines:
        m = QNA_SECTION_RE.match(line)
        if m:
            flush()
            section, item = m.group(1), None
            body.append(line)
            continue
        if section is not None:
            m_item = QNA_ITEM_RE.match(line)
            # 숫자 항목 번호는 INTRO 절에만 있다. 본문 안 숫자('10' 납기 지연 알림)를
            # 항목으로 오인하면 MAIN 의 좌표가 통째로 어긋난다.
            if m_item is None and section == "INTRO":
                m_item = QNA_NUMBER_RE.match(line)
            if m_item:
                flush()
                item = m_item.group(1)
                body.append(line)
                continue
        body.append(line)
    flush()

    merged: dict[str, list[str]] = {}
    order: list[str] = []
    for unit in units:
        if unit.label not in merged:
            merged[unit.label] = []
            order.append(unit.label)
        merged[unit.label].append(unit.text)
    return [LabelUnit(label=lbl, text="\n".join(merged[lbl])) for lbl in order]


def parse_source(cfg: dict) -> tuple[dict, list[dict]]:
    doc = read_html(Path(cfg["path"]))
    if doc.mode == "unescape":
        label_units = qna_label_units(doc)
        unit_kind = "qa_item"
        notes = "본문이 escape 된 채 박혀 있어 unescape 후 파싱했다. locator 는 문서 자체 번호 라벨."
        parser = "doc_html.self_numbering"
    else:
        label_units = heading_units(doc)
        unit_kind = "section"
        notes = f"원본 재파싱: sections {doc.section_count} · headings(h1~h3) {doc.heading_count}"
        parser = "doc_html.headings"

    units = [
        Unit(locator=lu.label, unit=unit_kind, text=normalize_text(lu.text))
        for lu in label_units
        if normalize_text(lu.text)
    ]
    evidences = build_evidences(cfg["id"], units)
    source = build_source_record(
        cfg,
        parser=parser,
        extractor="bs4",
        evidence_count=len(evidences),
        notes=notes,
    )
    return source, evidences
