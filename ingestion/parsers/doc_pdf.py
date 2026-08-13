"""PDF 파서.

원본 PDF 를 직접 읽는다. 다만 이미지 슬라이드 PDF 는 원본에서 텍스트가 나오지 않아
(제품소개서는 쪽당 7자) 사람·모델 판독본을 정본으로 쓴다. 그 경우 extractor 를 vision 으로
표시해 발췌의 출처를 답변까지 끌고 간다.

locator 는 쪽 단위 `p.34`. 500자를 넘는 쪽은 `p.34#2` 로 이어진다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ingestion.parsers.doc_common import (
    Unit,
    build_evidences,
    build_source_record,
    normalize_text,
)

PAGE_BREAK = "\x0c"
VISION_PAGE_RE = re.compile(r"^===== PAGE (\d+) =====$", re.M)
# 판독본이 스스로 불확실하다고 표시한 구간. A4 의 숫자·고유명사 추출에서 제외해야 한다.
UNCERTAIN_MARKERS = ("[판독불가]", "[판독주의]")


@dataclass(frozen=True)
class Page:
    number: int
    text: str


@dataclass(frozen=True)
class PdfDoc:
    path: Path
    pages: list[Page]
    extractor: str


def _pdftotext(path: Path) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    result = subprocess.run(
        [exe, "-layout", str(path), "-"], capture_output=True, check=False
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def read_pdf_pages(path: Path) -> PdfDoc:
    """쪽 경계를 보존해 읽는다. 빈 쪽도 자리를 지켜야 쪽 번호가 원본과 맞는다."""
    raw = _pdftotext(path)
    extractor = "pdftotext"
    if raw is None:
        import pypdf

        reader = pypdf.PdfReader(str(path))
        texts = [(page.extract_text() or "") for page in reader.pages]
        extractor = "native"
    else:
        texts = raw.split(PAGE_BREAK)
        # pdftotext 는 마지막 쪽 뒤에도 구분자를 넣는다
        if texts and not texts[-1].strip():
            texts.pop()
    return PdfDoc(
        path=path,
        pages=[Page(number=i, text=t) for i, t in enumerate(texts, start=1)],
        extractor=extractor,
    )


def read_vision_transcript(path: Path) -> PdfDoc:
    """`===== PAGE n =====` 구분자로 나뉜 판독본을 읽는다."""
    raw = path.read_text(encoding="utf-8")
    matches = list(VISION_PAGE_RE.finditer(raw))
    pages: list[Page] = []
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        pages.append(Page(number=int(m.group(1)), text=raw[m.end() : end]))
    return PdfDoc(path=path, pages=pages, extractor="vision")


def _units(doc: PdfDoc) -> list[Unit]:
    units: list[Unit] = []
    for page in doc.pages:
        text = normalize_text(page.text)
        if not text:
            continue
        units.append(Unit(locator=f"p.{page.number}", unit="page", text=text))
    return units


def parse_source(cfg: dict) -> tuple[dict, list[dict]]:
    if cfg.get("extractor") == "vision":
        transcript = Path(cfg["transcript_path"])
        doc = read_vision_transcript(transcript)
        uncertain = [
            p.number for p in doc.pages if any(m in p.text for m in UNCERTAIN_MARKERS)
        ]
        notes = (
            f"원본이 이미지 슬라이드라 판독본을 정본으로 썼다: {transcript.name}. "
            f"판독 불확실 표기가 있는 쪽은 숫자·고유명사 추출에서 제외한다: "
            f"{', '.join(f'p.{n}' for n in uncertain) or '없음'}"
        )
        parser = "doc_pdf.vision_transcript"
    else:
        doc = read_pdf_pages(Path(cfg["path"]))
        notes = None
        parser = "doc_pdf"

    units = _units(doc)
    evidences = build_evidences(cfg["id"], units)
    source = build_source_record(
        cfg,
        parser=parser,
        extractor=doc.extractor,
        evidence_count=len(evidences),
        notes=notes,
    )
    return source, evidences
