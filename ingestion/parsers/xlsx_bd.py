"""BD Overview 파서 — `BD Overview.xlsx`.

7시트를 전부 소화한다. 시트마다 축이 달라 record_kind 를 나눈다.
  BM 정의 → bd_row · 시장사이즈 → bd_market · 진척도/기대효과 → bd_maturity
  전략매출/Guest통제 → bd_row(속성) · 멀티BM → bd_targets

시트 간 명칭이 신·구 2벌로 병존한다. 파서는 어느 쪽으로도 통일하지 않고 원문 그대로 둔다.
config/bd-seed.yaml 과 대조해 시드에 없는 이름이 나오면 **버리지 않고** Evidence 로 남기고 경고한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from ingestion.parsers.xlsx_common import (
    EvidenceBuilder,
    ParseResult,
    Sheet,
    bd_seed,
    build_source_record,
    compose_excerpt,
    find_col,
    header_map,
    join_excerpt,
    load_workbook,
    source_config,
    split_overflow,
)

SOURCE_ID = "src_bd_overview"
PARSER = "xlsx_bd_registry"

MATURITY_AXES = {
    "제안가치 준비": "제안가치_준비",
    "세일즈킷 완성": "세일즈킷_완성",
    "이관 여부": "이관_여부",
    "시장배분 여부": "시장배분_여부",
    "제품지원 여부": "제품지원_여부",
}
EFFECT_BLOCKS = [(2, range(3, 8), "관리"), (9, range(10, 15), "후보"), (16, range(17, 22), "제외")]
FOOTNOTE_PREFIX = "**"


def _flat(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip() or None


def _split_list(text: str | None) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"[,、]", text.replace("\n", ",")) if p.strip()]


def _seed_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for bd in bd_seed()["business_domains"]:
        idx[_flat(bd["name"])] = bd["id"]
        for alias in bd.get("aliases") or []:
            idx[_flat(alias)] = bd["id"]
    return idx


def parse(path: Path | None = None) -> ParseResult:
    cfg = source_config(SOURCE_ID)
    src_path = Path(path or cfg["path"])
    wb = load_workbook(src_path)
    eb = EvidenceBuilder(SOURCE_ID)
    result = ParseResult(source={}, stats={"date_parse_failures": 0})
    seed = _seed_index()
    seen_names: list[str] = []

    def note(name: str | None) -> str | None:
        if name:
            seen_names.append(name)
        return name

    # ------------------------------------------------------------------
    # BM 정의 — BusinessDomain 시드의 정본
    # ------------------------------------------------------------------
    ws = Sheet(wb["BM 정의"], (2, 3, 4))
    hdr = header_map(ws, 2, last_col=9)
    col = {k: find_col(hdr, k) for k in ("산업", "BD", "타겟기업", "BR", "Guest", "업무")}
    for row in range(3, ws.max_row + 1):
        if not ws.text(row, col["BD"]) or not ws.row_has_value(row, [col["BR"], col["Guest"], col["업무"]]):
            continue
        eb.reset()
        structured = {
            "record_kind": "bd_row",
            "sheet": "BM 정의",
            "row": row,
            "산업": _flat(eb.cell(ws, row, col["산업"])),
            "BD": note(_flat(eb.cell(ws, row, col["BD"]))),
            "타겟기업": _flat(eb.cell(ws, row, col["타겟기업"])),
            "BR": _flat(eb.cell(ws, row, col["BR"])),
            "Guest": _flat(eb.cell(ws, row, col["Guest"])),
            "업무": _flat(eb.cell(ws, row, col["업무"])),
        }
        excerpt = join_excerpt([
            structured["산업"], structured["BD"], f"타겟기업 {structured['타겟기업']}",
            f"BR {structured['BR']}", f"Guest {structured['Guest']}", structured["업무"],
        ])
        result.evidence.append(
            eb.build(locator=ws.locator(row, col["BD"]), excerpt=excerpt, unit="row", structured=structured)
        )

    # ------------------------------------------------------------------
    # 시장사이즈 — B열에 헤더 라벨이 없다
    # ------------------------------------------------------------------
    ws = Sheet(wb["시장사이즈"], (2,))
    for row in range(3, ws.max_row + 1):
        name = ws.text(row, 2)
        if not name:
            if ws.text(row, 3) or ws.text(row, 4):
                continue
            break
        if name.startswith(FOOTNOTE_PREFIX):
            continue
        eb.reset()
        structured = {
            "record_kind": "bd_market",
            "sheet": "시장사이즈",
            "row": row,
            "BD": note(_flat(eb.cell(ws, row, 2))),
            "타겟기업": _flat(eb.cell(ws, row, 3)),
            "시장사이즈": _flat(eb.cell(ws, row, 4)),
            "타겟기업_상세": _flat(eb.cell(ws, row, 5)),
        }
        excerpt = join_excerpt([
            structured["BD"], structured["타겟기업"], structured["시장사이즈"], structured["타겟기업_상세"],
        ])
        result.evidence.append(
            eb.build(locator=ws.locator(row, 2), excerpt=excerpt, unit="row", structured=structured)
        )
    result.evidence += _footnotes(ws, eb, "시장사이즈")

    # ------------------------------------------------------------------
    # 진척도 — maturity 5축 + 파트너
    # ------------------------------------------------------------------
    ws = Sheet(wb["진척도"])
    hdr = {_flat(k): v for k, v in header_map(ws, 2, last_col=9).items()}
    axis_cols = {key: find_col(hdr, label) for label, key in MATURITY_AXES.items()}
    # 발췌에는 원문 헤더 라벨('세일즈킷 완성')을 쓴다. structured 키('세일즈킷_완성')를 그대로
    # 적으면 원문에 없는 문자열이 되어 발췌 검색으로는 영영 안 잡힌다.
    axis_labels = {key: (ws.flat(2, c) or key) for key, c in axis_cols.items() if c}
    partner_col = find_col(hdr, "파트너 협업")
    partner_label = (ws.flat(2, partner_col) if partner_col else None) or "파트너 협업"
    for row in range(3, ws.max_row + 1):
        name = ws.text(row, 2)
        if not name:
            break
        if name.startswith(FOOTNOTE_PREFIX) or name in {"✔", "제외"}:
            continue
        eb.reset()
        maturity = {
            key: _flat(eb.cell(ws, row, c)) if c else None for key, c in axis_cols.items()
        }
        partners = _split_list(eb.cell(ws, row, partner_col)) if partner_col else []
        structured = {
            "record_kind": "bd_maturity",
            "sheet": "진척도",
            "row": row,
            "BD": note(_flat(eb.cell(ws, row, 2))),
            "maturity": maturity,
            "파트너": partners,
        }
        excerpt = compose_excerpt(
            [structured["BD"]],
            [(axis_labels.get(key, key), value) for key, value in maturity.items()]
            + [(partner_label, ", ".join(partners) if partners else None)],
        )
        result.evidence.append(
            eb.build(locator=ws.locator(row, 2), excerpt=excerpt, unit="row", structured=structured)
        )
    result.evidence += _footnotes(ws, eb, "진척도")
    result.evidence += _legend(ws, eb, "진척도")

    # ------------------------------------------------------------------
    # 기대효과 — bd_status 의 정본. 가로로 3개 표가 나란히 놓여 있다
    # ------------------------------------------------------------------
    ws = Sheet(wb["기대효과"])
    metric_row = 3
    for name_col, metric_cols, status in EFFECT_BLOCKS:
        metrics = {c: _flat(ws.text(metric_row, c)) for c in metric_cols}
        for row in range(metric_row + 1, ws.max_row + 1):
            name = ws.text(row, name_col)
            if not name:
                break
            if name.startswith(FOOTNOTE_PREFIX) or name in {"✔", "제외"}:
                continue
            eb.reset()
            effects = [
                metrics[c] for c in metric_cols if ws.text(row, c) and metrics.get(c)
            ]
            structured = {
                "record_kind": "bd_maturity",
                "sheet": "기대효과",
                "row": row,
                "BD": note(_flat(eb.cell(ws, row, name_col))),
                "bd_status": status,
                "기대효과": effects,
            }
            excerpt = join_excerpt([
                f"{status} BM", structured["BD"], ", ".join(effects) if effects else None
            ])
            result.evidence.append(
                eb.build(locator=ws.locator(row, name_col), excerpt=excerpt, unit="row", structured=structured)
            )
    result.evidence += _footnotes(ws, eb, "기대효과", name_col=2, skip_values={"✔"})
    result.evidence += _legend(ws, eb, "기대효과")

    # ------------------------------------------------------------------
    # 전략매출 / Guest통제 — 표가 아니라 문장이다
    # ------------------------------------------------------------------
    result.evidence += _sentence_sheet(
        Sheet(wb["전략매출"]), eb, "전략매출", "채널유형", note, suffix=" 채널"
    )
    result.evidence += _sentence_sheet(
        Sheet(wb["Guest통제"]), eb, "Guest통제", "guest_control", note, prefix="통제 "
    )

    # ------------------------------------------------------------------
    # 멀티BM — 산업 → BD 다대다
    # ------------------------------------------------------------------
    ws = Sheet(wb["멀티BM"])
    industry = None
    for row in range(1, ws.max_row + 1):
        b = ws.text(row, 2)
        c = ws.text(row, 3)
        if b and not c:
            industry = _flat(b)
            continue
        if not c or not industry:
            continue
        eb.reset()
        line = eb.cell(ws, row, 3) or ""
        segment, _, rest = line.partition(":")
        if not rest:
            segment, rest = None, line
        bds = [note(_flat(x)) for x in _split_list(rest)]
        structured = {
            "record_kind": "bd_targets",
            "sheet": "멀티BM",
            "row": row,
            "산업": industry,
            "구분": _flat(segment),
            "BD목록": bds,
        }
        result.evidence.append(
            eb.build(
                locator=ws.locator(row, 3),
                excerpt=join_excerpt([industry, _flat(line)]),
                unit="row",
                structured=structured,
            )
        )
    wb.close()

    unmatched = sorted({n for n in seen_names if n and n not in seed})
    result.stats["bd_seed_unmatched"] = unmatched
    result.stats["bd_names_seen"] = len(set(seen_names))
    for n in unmatched:
        result.warnings.append(f"config/bd-seed.yaml 에 없는 BD 표기: {n} (Evidence 는 그대로 남겼다)")

    # 500자를 넘는 칸은 잘라 버리지 않고 `#n` 조각으로 나눈다. 자료 수를 세기 전에 한다.
    result.evidence = split_overflow(result.evidence)

    result.source = build_source_record(
        SOURCE_ID,
        parser=PARSER,
        evidence_count=len(result.evidence),
        notes="7시트 전부. 신·구 명칭을 병합하지 않고 원문 그대로 둔다.",
    )
    return result


def _footnotes(
    ws: Sheet, eb: EvidenceBuilder, sheet_name: str, name_col: int = 2, skip_values=frozenset()
) -> list[dict]:
    """각주·범례는 신·구 명칭 2벌 문제의 근거 원문이라 반드시 남긴다."""
    out = []
    for row in range(1, ws.max_row + 1):
        v = ws.text(row, name_col)
        if not v or v in skip_values:
            continue
        if not v.startswith(FOOTNOTE_PREFIX):
            continue
        eb.reset()
        text = eb.cell(ws, row, name_col)
        out.append(
            eb.build(
                locator=ws.locator(row, name_col),
                excerpt=text or "",
                unit="row",
                structured={
                    "record_kind": "doc_section",
                    "sheet": f"{sheet_name}(각주)",
                    "row": row,
                    "섹션": "각주",
                },
            )
        )
    return out


def _legend(
    ws: Sheet, eb: EvidenceBuilder, sheet_name: str, name_col: int = 2, value_col: int = 3
) -> list[dict]:
    """표 아래 범례 행을 남긴다.

    진척도 r20·r21 은 같은 '✔' 에 '완료'와 '보완필요' 두 뜻을 달아 두었고,
    기대효과 r16·r17 은 '직접효과'·'간접효과' 를 구분한다. 표 순회는 이 행들을 건너뛰므로
    범례가 통째로 사라져 "✔ 가 무슨 뜻인가"를 답할 근거가 없어진다.
    """
    out = []
    for row in range(1, ws.max_row + 1):
        mark = ws.flat(row, name_col)
        meaning = ws.flat(row, value_col)
        if mark not in {"✔", "제외"} or not meaning:
            continue
        eb.reset()
        excerpt = compose_excerpt(
            [f"범례 {eb.literal(mark)} = {eb.literal(meaning)}"]
        )
        out.append(
            eb.build(
                locator=ws.locator(row, name_col),
                excerpt=excerpt,
                unit="row",
                structured={
                    "record_kind": "doc_section",
                    "sheet": f"{sheet_name}(범례)",
                    "row": row,
                    "섹션": "범례",
                    "표기": mark,
                    "뜻": meaning,
                },
            )
        )
    return out


def _sentence_sheet(
    ws: Sheet, eb: EvidenceBuilder, sheet_name: str, attr: str, note, *, prefix: str = "", suffix: str = ""
) -> list[dict]:
    out = []
    for row in range(1, ws.max_row + 1):
        line = ws.text(row, 2)
        if not line or ":" not in line:
            continue
        eb.reset()
        masked = eb.cell(ws, row, 2) or ""
        label, _, rest = masked.partition(":")
        label = _flat(label) or ""
        if prefix and label.startswith(prefix):
            label = label[len(prefix):]
        if suffix and label.endswith(suffix.strip()):
            label = label[: -len(suffix.strip())]
        bds = [note(_flat(x)) for x in _split_list(rest)]
        structured = {
            "record_kind": "bd_row",
            "sheet": sheet_name,
            "row": row,
            attr: _flat(label),
            "BD목록": bds,
        }
        out.append(
            eb.build(
                locator=ws.locator(row, 2),
                excerpt=_flat(masked) or "",
                unit="row",
                structured=structured,
            )
        )
    return out


if __name__ == "__main__":  # pragma: no cover
    from ingestion.parsers.xlsx_common import write_result

    r = parse()
    print(write_result(r), len(r.evidence), r.stats)
