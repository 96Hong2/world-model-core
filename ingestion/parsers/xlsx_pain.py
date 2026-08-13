"""고객사 Pain Point 파서 — `고객사 Pain Point·구축 대응 통합 관리.xlsx`.

need-taxonomy·capability-taxonomy 의 유일한 실물 근거다.
Pain 원문 95건은 요약하지 않고 excerpt 에 그대로 넣는다(Cross-BD 반복 Need 판정의 원재료).
8종 xlsx 중 유일하게 병합이 0개라 forward-fill 이 필요 없다. 헤더는 전부 r1 한 줄.
"""

from __future__ import annotations

import re
from pathlib import Path

from ingestion.parsers.xlsx_common import (
    EvidenceBuilder,
    ParseResult,
    Sheet,
    build_source_record,
    compose_excerpt,
    find_col,
    header_map,
    join_excerpt,
    load_workbook,
    source_config,
    split_overflow,
)

SOURCE_ID = "src_pain_registry"
PARSER = "xlsx_pain_registry"

HEADER_ROW = 1
COMPOUND_SPLIT = re.compile(r"[·/]")


def _as_int(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_pain_list(ws: Sheet, eb: EvidenceBuilder) -> list[dict]:
    hdr = header_map(ws, HEADER_ROW, last_col=12)
    col = {
        "No": find_col(hdr, "No"),
        "고객사": find_col(hdr, "고객사"),
        "분류": find_col(hdr, "분류"),
        "기능": find_col(hdr, "기능"),
        "pain": find_col(hdr, "도입 전 고객 불편 (Pain Point)", "도입 전 고객 불편"),
        "대응": find_col(hdr, "구축·해결 내용"),
        "제공구분": find_col(hdr, "제공구분"),
        "개발공수": find_col(hdr, "개발공수"),
        "cx": find_col(hdr, "CX 중요도"),
        "비고": find_col(hdr, "비고"),
    }
    out = []
    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        if not ws.text(row, col["pain"]) and not ws.text(row, col["고객사"]):
            continue
        eb.reset()
        pain = eb.cell(ws, row, col["pain"]) or ""
        structured = {
            "record_kind": "pain_row",
            "sheet": ws.title,
            "row": row,
            "No": _as_int(ws.text(row, col["No"])),
            "고객사": eb.cell(ws, row, col["고객사"]),
            "분류": eb.cell(ws, row, col["분류"]),
            "대응_기능": eb.cell(ws, row, col["기능"]),
            "pain_원문": pain,
            "구축_해결_내용": eb.cell(ws, row, col["대응"]),
            "제공구분": eb.cell(ws, row, col["제공구분"]),
            "제공구분_분해": [
                p.strip() for p in (ws.text(row, col["제공구분"]) or "").split("+") if p.strip()
            ],
            "개발공수": eb.cell(ws, row, col["개발공수"]),
            "CX_중요도": eb.cell(ws, row, col["cx"]),
            "비고": eb.cell(ws, row, col["비고"]),
        }
        # Pain 원문이 먼저다(요약하지 않는다). 그 뒤에 같은 행의 나머지 열을 붙인다 —
        # 대응 기능·제공구분이 structured 에만 있으면 발췌 검색으로는 영영 안 잡힌다.
        # No 는 맨 뒤에 붙인다. '오픈 후 운영 불편' 시트의 출처 열이 "Pain Point 목록 75번 연계"
        # 처럼 이 번호를 직접 인용하므로 발췌에 있어야 시트 간 연결을 찾을 수 있다.
        excerpt = compose_excerpt(
            [pain],
            [
                ("고객사", structured["고객사"]),
                ("분류", structured["분류"]),
                ("대응 기능", structured["대응_기능"]),
                ("구축·해결", structured["구축_해결_내용"]),
                ("제공구분", structured["제공구분"]),
                ("개발공수", structured["개발공수"]),
                ("CX 중요도", structured["CX_중요도"]),
                ("비고", structured["비고"]),
                ("No", structured["No"]),
            ],
        )
        out.append(
            eb.build(
                locator=ws.locator(row, col["pain"]),
                excerpt=excerpt,
                unit="row",
                structured=structured,
            )
        )
    return out


def _parse_flat_table(ws: Sheet, eb: EvidenceBuilder) -> list[dict]:
    """헤더 한 줄 + 데이터 행인 시트를 컬럼명과 함께 그대로 펼친다.

    '대응 시나리오'·'구축 체크포인트'용이다. 스키마를 짐작하지 않고 원문 열 이름을 쓴다.
    """
    hdr = header_map(ws, HEADER_ROW, last_col=10)
    cols = sorted(hdr.items(), key=lambda kv: kv[1])
    if not cols:
        return []
    first_col = cols[0][1]
    out = []
    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        if not ws.row_has_value(row, [c for _, c in cols]):
            continue
        eb.reset()
        values = {label: eb.cell(ws, row, c) for label, c in cols}
        structured = {
            "record_kind": "pain_row",
            "sheet": ws.title,
            "row": row,
            "No": _as_int(values.get("No")),
            "열": {k: v for k, v in values.items() if v},
        }
        # No 도 라벨과 함께 남긴다. 시트 간 참조가 이 번호로 걸려 있다.
        excerpt = compose_excerpt(
            (),
            [(label, v) for label, v in values.items() if label != "No"]
            + [("No", values.get("No"))],
        )
        out.append(
            eb.build(locator=ws.locator(row, first_col), excerpt=excerpt, unit="row",
                     structured=structured)
        )
    return out


def _parse_plain_rows(ws: Sheet, eb: EvidenceBuilder) -> list[dict]:
    """헤더가 없는 요약 시트. 블록이 여러 개라 컬럼명을 붙이면 오히려 틀린다.

    행의 값을 순서대로 이어 붙여 원문 그대로 남긴다.
    """
    last_col = min(ws.ws.max_column or 1, 10)
    out = []
    for row in range(1, ws.max_row + 1):
        eb.reset()
        values = [eb.cell(ws, row, c) for c in range(1, last_col + 1)]
        parts = [v for v in values if v]
        if not parts:
            continue
        first_col = next(c for c in range(1, last_col + 1) if values[c - 1])
        out.append(
            eb.build(
                locator=ws.locator(row, first_col),
                excerpt=join_excerpt(parts),
                unit="row",
                structured={
                    "record_kind": "pain_row",
                    "sheet": ws.title,
                    "row": row,
                    "섹션": "요약",
                },
            )
        )
    return out


def _parse_blocked(ws: Sheet, eb: EvidenceBuilder) -> list[dict]:
    hdr = header_map(ws, HEADER_ROW, last_col=8)
    col = {
        "No": find_col(hdr, "No"),
        "구분": find_col(hdr, "구분"),
        "요구": find_col(hdr, "요구 항목"),
        "고객사": find_col(hdr, "발생 고객사"),
        "결과": find_col(hdr, "처리 결과"),
        "안내": find_col(hdr, "신규 고객 소통 시 안내"),
    }
    out = []
    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        if not ws.text(row, col["요구"]):
            continue
        eb.reset()
        accounts_raw = eb.cell(ws, row, col["고객사"]) or ""
        structured = {
            "record_kind": "pain_row",
            "sheet": ws.title,
            "row": row,
            "No": _as_int(ws.text(row, col["No"])),
            "구분": eb.cell(ws, row, col["구분"]),
            "요구_항목": eb.cell(ws, row, col["요구"]),
            # 복합값 원문('카파전선·마바손해보험')과 분해 결과를 둘 다 남긴다.
            # 원문이 없으면 골든이 인용한 셀 값 그대로는 검색되지 않고,
            # 분해가 없으면 없는 회사 하나가 생긴다.
            "고객사": accounts_raw or None,
            "발생_고객사": [p.strip() for p in COMPOUND_SPLIT.split(accounts_raw) if p.strip()],
            "처리_결과": eb.cell(ws, row, col["결과"]),
            "안내": eb.cell(ws, row, col["안내"]),
        }
        excerpt = compose_excerpt(
            [structured["구분"], structured["요구_항목"]],
            [
                (ws.flat(HEADER_ROW, col["고객사"]) or "발생 고객사", accounts_raw),
                ("처리 결과", structured["처리_결과"]),
                ("안내", structured["안내"]),
                ("No", structured["No"]),
            ],
        )
        out.append(
            eb.build(
                locator=ws.locator(row, col["요구"]),
                excerpt=excerpt,
                unit="row",
                structured=structured,
            )
        )
    return out


def _parse_operational(ws: Sheet, eb: EvidenceBuilder) -> list[dict]:
    hdr = header_map(ws, HEADER_ROW, last_col=8)
    col = {
        "No": find_col(hdr, "No"),
        "고객사": find_col(hdr, "고객사"),
        "구분": find_col(hdr, "구분"),
        "불편": find_col(hdr, "불편 내용"),
        "조치": find_col(hdr, "현황·조치"),
        "출처": find_col(hdr, "출처"),
    }
    out = []
    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        if not ws.text(row, col["불편"]):
            continue
        eb.reset()
        structured = {
            "record_kind": "pain_row",
            "sheet": ws.title,
            "row": row,
            "No": _as_int(ws.text(row, col["No"])),
            "고객사": eb.cell(ws, row, col["고객사"]),
            "구분": eb.cell(ws, row, col["구분"]),
            "불편_내용": eb.cell(ws, row, col["불편"]),
            "현황_조치": eb.cell(ws, row, col["조치"]),
            "출처": eb.cell(ws, row, col["출처"]),
        }
        # 출처 열은 "Pain Point 목록 75번 연계" 처럼 다른 시트 행을 직접 가리킨다.
        # 발췌에 없으면 그 연결이 검색에서 사라진다.
        excerpt = compose_excerpt(
            [structured["고객사"], structured["구분"], structured["불편_내용"], structured["현황_조치"]],
            [
                ("출처", structured["출처"]),
                ("No", structured["No"]),
            ],
        )
        out.append(
            eb.build(
                locator=ws.locator(row, col["불편"]),
                excerpt=excerpt,
                unit="row",
                structured=structured,
            )
        )
    return out


def parse(path: Path | None = None) -> ParseResult:
    cfg = source_config(SOURCE_ID)
    src_path = Path(path or cfg["path"])
    include = list(cfg.get("scope", {}).get("sheets_include", []))
    wb = load_workbook(src_path)
    eb = EvidenceBuilder(SOURCE_ID)
    result = ParseResult(source={}, stats={"date_parse_failures": 0})

    handlers = {
        "Pain Point 목록": _parse_pain_list,
        "대응 불가·제약": _parse_blocked,
        "오픈 후 운영 불편": _parse_operational,
        "대응 시나리오": _parse_flat_table,
        "구축 체크포인트": _parse_flat_table,
        "요약": _parse_plain_rows,
    }
    for name in include or wb.sheetnames:
        if name not in handlers:
            result.warnings.append(f"핸들러가 없는 시트: {name}")
            continue
        result.evidence += handlers[name](Sheet(wb[name]), eb)
    wb.close()

    # 500자를 넘는 칸은 잘라 버리지 않고 `#n` 조각으로 나눈다. 자료 수를 세기 전에 한다.
    result.evidence = split_overflow(result.evidence)

    result.source = build_source_record(
        SOURCE_ID,
        parser=PARSER,
        evidence_count=len(result.evidence),
        notes="6시트 전부. Pain 원문은 요약 없이 보존하고 같은 행의 대응 기능·제공구분을 발췌에 함께 담는다.",
    )
    return result


if __name__ == "__main__":  # pragma: no cover
    from ingestion.parsers.xlsx_common import write_result

    r = parse()
    print(write_result(r), len(r.evidence), r.stats)
