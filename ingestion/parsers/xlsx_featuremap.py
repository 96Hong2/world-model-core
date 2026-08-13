"""기능맵 파서 — `기능맵 v2.1.xlsx` 형태의 release_spec.

제품 기능의 정본이다. v2.1 시트 한 장에 1.0.0~2.1.0 버전 컬럼이 다 들어 있어
"이 기능 언제부터 되나"를 시트 비교 없이 행 단위로 판정할 수 있다.
v2.0·v1.0 시트는 쓰지 않는다. 기능명이 리네이밍돼 집합 비교가 절반밖에 안 맞는다.
"""

from __future__ import annotations

from pathlib import Path

from ingestion.parsers.xlsx_common import (
    EvidenceBuilder,
    EXCERPT_MAX,
    ParseResult,
    Sheet,
    alpha_digest,
    build_source_record,
    compose_excerpt,
    find_col,
    flat_text,
    head_block_evidence,
    header_map,
    join_excerpt,
    load_workbook,
    parse_cell_date,
    source_config,
    split_overflow,
)

SOURCE_ID = "src_featuremap_v21"
PARSER = "xlsx_featuremap"

DATA_SHEET = "v2.1"
META_SHEET = "문서정보"
FFILL_COLS = (2, 3, 4)  # 화면 · 영역 · 상세분류 (세로 병합은 이 셋뿐)
HEADER_ROW = 3
VERSION_LABELS = ("1.0.0", "1.1.0", "2.0.0", "2.1.0")
CENTER_COLUMNS = {"LiveTalk": "LiveTalk", "GroupTalk": "GroupTalk"}
CHECK_MARKS = {"V", "v", "√", "✔", "✓", "O", "o"}  # 같은 뜻을 여러 기호로 섞어 쓴다
INCLUDED = "O"


def _center_types(sheet: Sheet, row: int, cols: dict[str, int | None]) -> list[str]:
    out = []
    for label, col in cols.items():
        if col is None:
            continue
        v = sheet.text(row, col)
        if v and v.strip() in CHECK_MARKS:
            out.append(label)
    return out


def _version_verdict(sheet: Sheet, row: int, cols: dict[str, int | None]):
    flags: dict[str, str | None] = {}
    available: list[str] = []
    introduced: str | None = None
    for label in VERSION_LABELS:
        col = cols.get(label)
        v = sheet.text(row, col) if col else None
        flags[label] = v
        if v and v.strip().upper() == INCLUDED:
            available.append(label)
            if introduced is None:
                introduced = label
    return introduced, available, flags


def version_sentence(introduced: str | None, available: list[str]) -> str:
    """버전 판정을 사람이 읽는 문장으로 만든다.

    버전 컬럼은 O/. 표기라 구조화 필드에만 넣으면 발췌 검색으로는 영영 안 잡힌다.
    "언제부터 되나"가 이 자료의 핵심 사실이므로 발췌 문장에 값을 그대로 적는다.
    """
    if not available:
        return "제공 버전: 표기 없음"
    listed = ", ".join(available)
    return f"제공 버전: {listed}" + (f" ({introduced}부터)" if introduced else "")


def parse(path: Path | None = None) -> ParseResult:
    cfg = source_config(SOURCE_ID)
    src_path = Path(path or cfg["path"])
    wb = load_workbook(src_path)

    eb = EvidenceBuilder(SOURCE_ID)
    evidence: list[dict] = []
    stats: dict = {"date_parse_failures": 0}
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # v2.1 — 기능 행
    # ------------------------------------------------------------------
    ws = Sheet(wb[DATA_SHEET], FFILL_COLS)
    hdr = header_map(ws, HEADER_ROW, last_col=14)
    col = {
        "화면": find_col(hdr, "화면"),
        "영역": find_col(hdr, "영역"),
        "상세분류": find_col(hdr, "상세분류"),
        "기능": find_col(hdr, "기능"),
        "설명": find_col(hdr, "설명"),
        "비고": find_col(hdr, "비고"),
    }
    center_cols = {k: find_col(hdr, v) for k, v in CENTER_COLUMNS.items()}
    version_cols = {v: find_col(hdr, v) for v in VERSION_LABELS}
    missing = [k for k, v in col.items() if v is None]
    if missing:
        raise ValueError(f"{DATA_SHEET} 헤더에서 컬럼을 못 찾았다: {missing}")

    # 시트 제목과 헤더 라벨 줄. 표 파서는 헤더 아래만 읽어 이 줄이 어디에도 남지 않는다.
    evidence += head_block_evidence(
        ws, eb, last_row=HEADER_ROW, last_col=14, section="시트 머리말"
    )

    data_rows = 0
    pending: dict | None = None  # 직전 기능 행 (설명만 있는 행을 여기에 붙인다)
    block: str | None = None  # 표 중간 구분선(' add on Feature')이 여는 블록

    def flush(rec: dict | None) -> None:
        """버전 문장을 발췌 끝에 붙인다.

        예전에는 이 문장 자리를 만들려고 앞쪽 본문을 잘라 버렸다. 원본을 버리는 것이므로
        그만두었다. 길어지면 `split_overflow` 가 `#n` 조각으로 나눠 전량을 남긴다
        (지금 이 자료는 가장 긴 행도 상한에 못 미쳐 나뉘는 행이 없다).
        """
        if rec is None:
            return
        st = rec["structured"]
        tail = version_sentence(st["introduced_in"], st["available_versions"])
        rec["excerpt"] = join_excerpt([rec["excerpt"], tail])
        rec["excerpt_hash"] = alpha_digest(rec["excerpt"], 32)
        rec["raw_len"] = max(rec["raw_len"], len(rec["excerpt"]))
        evidence.append(rec)

    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        name_raw = ws.text(row, col["기능"])
        desc_raw = ws.text(row, col["설명"])
        if not name_raw and not desc_raw:
            # 표 한가운데 있는 구분선 행(B352 ' add on Feature')이다. 이 아래 기능은 add-on 이라
            # 확장 가능성 질문의 근거가 되는데, 그냥 건너뛰면 그 구분이 어디에도 안 남는다.
            # 화면 컬럼만 스스로 값을 가진 행이 구분선이다(병합 ffill 값은 세지 않는다).
            marker = ws.ws.cell(row, col["화면"]).value
            others = [c for c in (col["영역"], col["상세분류"], col["비고"]) if c]
            if marker and str(marker).strip() and not ws.row_has_own_value(row, others):
                block = flat_text(marker)
                warnings.append(f"{ws.locator(row, col['화면'])}: 블록 구분선 '{block}'")
            continue
        data_rows += 1

        if not name_raw:
            # 기능명 없이 설명만 있는 행은 앞 기능의 부가 설명이다. 별도 노드로 만들지 않는다.
            if pending is None:
                warnings.append(f"{ws.locator(row, col['설명'])}: 앞 기능 없이 설명만 있는 행")
                continue
            extra = eb.literal(desc_raw) or ""
            st = pending["structured"]
            st["설명_병합행"].append(ws.locator(row, col["설명"]))
            st["설명"] = join_excerpt([st["설명"], extra], sep=" ")
            # 자르지 않는다. 길어지면 `split_overflow` 가 조각으로 나눠 전량을 남긴다.
            pending["excerpt"] = join_excerpt([pending["excerpt"], extra], sep=" ")
            continue

        flush(pending)
        eb.reset()
        name = eb.cell(ws, row, col["기능"])
        desc = eb.cell(ws, row, col["설명"])
        screen = eb.cell(ws, row, col["화면"])
        area = eb.cell(ws, row, col["영역"])
        detail = eb.cell(ws, row, col["상세분류"])
        note = eb.cell(ws, row, col["비고"])

        introduced, available, flags = _version_verdict(ws, row, version_cols)
        if introduced is None:
            warnings.append(f"{ws.locator(row, col['기능'])}: 버전 컬럼에 'O' 가 없다")

        structured = {
            "record_kind": "feature_row",
            "sheet": DATA_SHEET,
            "row": row,
            "대분류": screen,
            "중분류": area,
            "상세분류": detail,
            "기능명": name,
            "설명": desc,
            "센터구분": _center_types(ws, row, center_cols),
            "introduced_in": introduced,
            "available_versions": available,
            "version_flags": flags,
            "비고": note,
            "블록": block,
            "설명_병합행": [],
        }
        excerpt = compose_excerpt(
            [
                join_excerpt([screen, area, detail], sep=" > "),
                join_excerpt([name, desc], sep=": "),
            ],
            [("비고", note), ("블록", block)],
        )
        pending = eb.build(
            locator=ws.locator(row, col["기능"]),
            excerpt=excerpt,
            unit="row",
            structured=structured,
        )
    flush(pending)
    stats[f"{DATA_SHEET}_data_rows"] = data_rows
    stats[f"{DATA_SHEET}_feature_rows"] = len(evidence)

    # ------------------------------------------------------------------
    # 문서정보 — Source 권위·시점 메타
    # ------------------------------------------------------------------
    meta = Sheet(wb[META_SHEET])
    meta_header = 8
    mhdr = header_map(meta, meta_header, last_col=14)
    mcol = {
        "작성일": find_col(mhdr, "작성일"),
        "내용": find_col(mhdr, "내용"),
        "작성자": find_col(mhdr, "작성자"),
        "검토자": find_col(mhdr, "검토자"),
        "검토일": find_col(mhdr, "검토일"),
        "버전": find_col(mhdr, "버전"),
    }
    # 헤더 위 머리말(프로젝트명·최종 작성자·버전·최종 작성일)은 이 문서가 언제·누구 기준인지를
    # 말하는 유일한 자리다. 변경정보 표만 읽으면 통째로 사라진다.
    evidence += head_block_evidence(
        meta, eb, last_row=meta_header - 1, last_col=14, section="문서 머리말"
    )
    latest_version = None
    for row in range(meta_header + 1, meta.max_row + 1):
        if not meta.text(row, mcol["내용"]):
            if meta.text(row, mcol["작성일"]):
                continue
            break
        eb.reset()
        written, st1 = parse_cell_date(meta.raw(row, mcol["작성일"]))
        reviewed, st2 = parse_cell_date(meta.raw(row, mcol["검토일"]))
        for s in (st1, st2):
            if s == "unparsable":
                stats["date_parse_failures"] += 1
        content = eb.cell(meta, row, mcol["내용"])
        author = eb.cell(meta, row, mcol["작성자"])
        reviewer = eb.cell(meta, row, mcol["검토자"])
        version = eb.cell(meta, row, mcol["버전"])
        latest_version = version or latest_version
        structured = {
            "record_kind": "doc_section",
            "sheet": META_SHEET,
            "row": row,
            "섹션": "변경정보",
            "버전": version,
            "작성자": author,
            "검토자": reviewer,
            "작성일": written,
            "검토일": reviewed,
        }
        evidence.append(
            eb.build(
                locator=meta.locator(row, mcol["내용"]),
                excerpt=compose_excerpt(
                    [version, content],
                    [("작성", author), ("검토", reviewer), ("작성일", written), ("검토일", reviewed)],
                ),
                unit="row",
                structured=structured,
                authored_at=f"{written}T00:00:00Z" if written else None,
                author_name=author,
            )
        )
    wb.close()

    # 500자를 넘는 칸은 잘라 버리지 않고 `#n` 조각으로 나눈다. 자료 수를 세기 전에 한다.
    evidence = split_overflow(evidence)

    source = build_source_record(
        SOURCE_ID,
        parser=PARSER,
        evidence_count=len(evidence),
        version=latest_version,
        notes="v2.0·v1.0·표지 시트는 제외(sources.yaml scope). introduced_in 은 v2.1 버전 컬럼에서만 뽑는다.",
    )
    return ParseResult(source=source, evidence=evidence, stats=stats, warnings=warnings)


if __name__ == "__main__":  # pragma: no cover
    from ingestion.parsers.xlsx_common import write_result

    r = parse()
    print(write_result(r), len(r.evidence), r.stats)
