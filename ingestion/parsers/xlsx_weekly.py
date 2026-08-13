"""주간활동계획 파서 — `영업본부_주간활동계획.xlsx` 형태의 sales_weekly_plan.

한 시트가 "주차 블록"의 세로 반복이다(사람당 최대 11블록). 반복이므로 최신 1블록만 쓴다.
블록 안에서 헤더가 안정적인 표는 `5. 사업실행협의 이상 고객사` 와
`6. 핵심관리 고객사 리스트 및 제안전략(TOP5)` 둘뿐이라 이 둘만 뽑는다.
일별 계획 표는 같은 G열이 주차마다 `중요도`였다가 `고객사 담당자`가 되는 7가지 변형이 있어 넣지 않는다.
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
    load_workbook,
    money_cell,
    source_config,
    split_overflow,
)

SOURCE_ID = "src_sales_weekly_plan"
PARSER = "xlsx_weekly_plan"

# 블록 앵커는 '금주주간활동(<이름>)' 문구가 아니라 주차 라벨로 잡는다.
# 어떤 사람 시트에는 '금주주간활동' 문구 자체가 없고, 있는 시트도 B열과 C열을 오간다.
WEEK_LABEL_RE = re.compile(r"^\s*\d{2}년\s*\d+\s*(?:주차|차)\s*$")
SECTION_RE = re.compile(r"^\s*([56])\.\s*(.+?)\s*$")
TABLE_TITLES = {
    "5": "5. 사업실행협의 이상 고객사",
    "6": "6. 핵심관리 고객사 리스트 및 제안전략(TOP5)",
}
ANCHOR_COLS = (2, 3)
# 다음 블록의 제목 행이 앞 블록 범위 안으로 들어오는 시트가 있다(사람에 따라 C열에 있다).
# 그대로 두면 '금주주간활동(담당자명)' 이 고객사 값으로 수집된다.
STRUCTURAL_RE = re.compile(r"^(금주주간활동|차주주간활동|No\.?$|\d+\.\s|\d{2}년\s*\d+)")


def _is_structural(value: str) -> bool:
    return bool(STRUCTURAL_RE.match(value.strip()))


def _week_anchors(sheet: Sheet) -> list[tuple[int, str]]:
    out = []
    for row in range(1, sheet.max_row + 1):
        for c in ANCHOR_COLS:
            v = sheet.text(row, c)
            if v and WEEK_LABEL_RE.match(v):
                out.append((row, v.strip()))
                break
    return out


def _sections_in(sheet: Sheet, start: int, end: int) -> list[tuple[int, str, str]]:
    out = []
    for row in range(start, end):
        for c in ANCHOR_COLS:
            v = sheet.text(row, c)
            if not v:
                continue
            m = SECTION_RE.match(v)
            if m:
                out.append((row, m.group(1), TABLE_TITLES[m.group(1)]))
                break
    return out


def _parse_table(
    sheet: Sheet, eb: EvidenceBuilder, week: str, head_row: int, title: str, end: int
) -> list[dict]:
    hdr = header_map(sheet, head_row + 1, last_col=10)
    col = {
        "No": find_col(hdr, "No.", "No"),
        "고객사": find_col(hdr, "고객사"),
        "업무도메인": find_col(hdr, "업무도메인", "업무 도메인"),
        "금액": find_col(hdr, "금액"),
        "현재단계": find_col(hdr, "현재 단계", "현재단계"),
        "내용": find_col(hdr, "진행사항", "추진 내용 / 제안 전략 / Action Item", "추진 내용"),
    }
    if not col["고객사"]:
        return []
    # 발췌에 붙일 라벨은 원문 헤더 문구를 쓴다(시트마다 'No.' / 'No' 이고,
    # 5.표는 '금액·진행사항', 6.표는 '현재 단계·추진 내용 / 제안 전략 / Action Item' 이다).
    def label(key: str, fallback: str) -> str:
        c = col.get(key)
        if not c:
            return fallback
        return next((lab for lab, cc in hdr.items() if cc == c), fallback)

    out = []
    for row in range(head_row + 2, end):
        account = sheet.text(row, col["고객사"])
        if not account:
            continue  # No. 만 있고 고객사가 빈 행은 데이터가 아니다
        if _is_structural(account) or any(
            (sheet.text(row, c) or "") and _is_structural(sheet.text(row, c)) for c in ANCHOR_COLS
        ):
            break  # 다음 블록·다음 섹션의 제목 행에 닿았다
        eb.reset()
        structured = {
            "record_kind": "weekly_row",
            "sheet": sheet.title,
            "row": row,
            "주차": week,
            "표": title,
            "No": eb.cell(sheet, row, col["No"]) if col["No"] else None,
            "고객사": eb.cell(sheet, row, col["고객사"]),
            "업무도메인": eb.cell(sheet, row, col["업무도메인"]) if col["업무도메인"] else None,
            "금액": money_cell(sheet, eb, row, col["금액"]),
            "현재단계": eb.cell(sheet, row, col["현재단계"]) if col["현재단계"] else None,
            "내용": eb.cell(sheet, row, col["내용"]) if col["내용"] else None,
        }
        excerpt = compose_excerpt(
            [f"{week} {title}", structured["고객사"]],
            [
                (label("업무도메인", "업무도메인"), structured["업무도메인"]),
                (label("금액", "금액"), structured["금액"]),
                (label("현재단계", "현재 단계"), structured["현재단계"]),
                (label("내용", "진행사항"), structured["내용"]),
                (label("No", "No"), structured["No"]),
            ],
        )
        out.append(
            eb.build(
                locator=sheet.locator(row, col["고객사"]),
                excerpt=excerpt,
                unit="row",
                structured=structured,
                author_name=sheet.title,
            )
        )
    return out


def parse(path: Path | None = None) -> ParseResult:
    cfg = source_config(SOURCE_ID)
    src_path = Path(path or cfg["path"])
    scope = cfg.get("scope", {})
    include = list(scope.get("sheets_include", []))
    exclude = set(scope.get("sheets_exclude", []))

    wb = load_workbook(src_path)
    eb = EvidenceBuilder(SOURCE_ID)
    result = ParseResult(source={}, stats={"date_parse_failures": 0})

    for name in wb.sheetnames:
        if name in exclude or (include and name not in include):
            continue
        sheet = Sheet(wb[name])
        anchors = _week_anchors(sheet)
        if not anchors:
            result.warnings.append(f"{name}: 주차 블록 앵커를 못 찾았다")
            continue
        # 최신 블록은 시트 맨 위 블록이다(아래로 갈수록 과거 주차).
        start, week = anchors[0]
        end = anchors[1][0] if len(anchors) > 1 else sheet.max_row + 1
        result.stats.setdefault("블록수", {})[name] = len(anchors)
        sections = _sections_in(sheet, start, end)
        for i, (row, _num, title) in enumerate(sections):
            sec_end = sections[i + 1][0] if i + 1 < len(sections) else end
            result.evidence += _parse_table(sheet, eb, week, row, title, sec_end)
    wb.close()

    # 500자를 넘는 칸은 잘라 버리지 않고 `#n` 조각으로 나눈다. 자료 수를 세기 전에 한다.
    result.evidence = split_overflow(result.evidence)

    result.source = build_source_record(
        SOURCE_ID,
        parser=PARSER,
        evidence_count=len(result.evidence),
        notes="최신 1개 주차 블록의 5.·6. 표만 사용. 일별 계획 표는 헤더 변형 7종이라 제외(sources.yaml scope).",
    )
    return result


if __name__ == "__main__":  # pragma: no cover
    from ingestion.parsers.xlsx_common import write_result

    r = parse()
    print(write_result(r), len(r.evidence), r.stats)
