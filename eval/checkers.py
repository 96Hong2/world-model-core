"""Deterministic 채점기 8종.

LLM judge 를 쓰지 않는다. 전부 규칙·정규식·문자열 비교다(REVISED §5 — judge calibration 은 V2).

채점기별 책임을 겹치지 않게 나눴다. mutation 하나가 채점기 하나만 깨뜨려야
"무엇이 왜 틀렸는지"를 알 수 있기 때문이다.

  route          라우팅이 기대 경로와 같은가
  evidence_match citation 이 실제 Evidence 를 가리키고, 기대 발췌가 인용 가능한 근거 안에 있는가
  keyfact        must_include 가 답변 본문에 있고 must_not_include 가 규정된 범위에 없는가
  masking        답변·evidence·subgraph 전체에 PII 정규식 매칭이 0건인가
  traceability   답변의 숫자·고유명사가 답변에 실린 Evidence 안에 존재하는가
  gap_verdict    gap 3값 판정이 기대와 같고, CONFIRMED 가 명시적 부재 근거를 갖는가
  subgraph       subgraph 가 동봉되고 인용 노드가 실제로 들어 있는가
  conflict       충돌 문항에서 양측이 병렬로 제시되는가(단일 합성 금지)

traceability 는 citation 이 아니라 **답변에 실린 Evidence 전체**를 근거 풀로 쓴다.
인용이 올바른 Evidence 를 가리키는지는 evidence_match 의 책임이라, 두 책임을 섞으면
인용 하나가 깨졌을 때 채점기 두 개가 동시에 빨개져 원인 추적이 어려워진다.

채점 범위 두 가지는 Lead 판정으로 교정했다. 무엇을 왜 바꿨는지는 `eval/CHECKER-CHANGES.md`.

  · must_not_include 는 **답변 본문(prose)** 만 본다. 목적이 "모델이 지어내거나 잘못 말하는 것"을
    잡는 것이라, 원문 발췌를 그대로 인용한 것은 환각이 아니기 때문이다.
    **단 금칙어가 PII 값 자체이면 payload 전체를 본다**(§5 Gate 1A 5 · §8 하드 게이트).
    PII 노출의 본 게이트는 여전히 masking(정규식, payload 전체)이고 그쪽은 손대지 않았다.
  · evidence_match 는 `evidence[] ∪ raw_signals[]` 를 인용 대상으로 본다.
    raw_signals 는 source_id·locator·발췌를 갖춰 사람이 원본까지 갈 수 있고,
    §4 ⑦ · §5 Raw Evidence Fallback 이 그러라고 만든 경로다.
    다만 source_id·locator 가 비어 원본으로 갈 수 없는 raw_signal 은 인정하지 않고,
    각주(citations)는 계약(contracts/answer.schema.json)대로 evidence[] 로만 해소돼야 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from eval.golden_loader import Golden, contains, normalize

PII_CONFIG = Path(__file__).resolve().parents[1] / "config" / "pii-patterns.yaml"

# Gate 1A 하드 게이트에 넣는 탐지기. 계좌번호(맥락 조건부)와 사업자번호(맨 10자리 오탐 큼)는
# 하드 게이트에서 뺐다. config/pii-patterns.yaml 이 그 둘에 needs_human_confirm 을 달아 둔 상태다.
HARD_GATE_DETECTORS = (
    "PII-MOBILE",
    "PII-LANDLINE",
    "PII-SERVICE-NUMBER",
    "PII-EMAIL",
    "PII-RRN",
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    skipped: bool = False


# ---------------------------------------------------------------------------
# 답변에서 텍스트를 꺼내는 헬퍼
# ---------------------------------------------------------------------------

_CITATION_MARKER = re.compile(r"\[\d{1,3}\]")


def answer_prose(answer: dict[str, Any]) -> str:
    """모델이 생성한 문장만 모은다. Evidence 원문은 포함하지 않는다."""
    a = answer.get("answer") or {}
    parts: list[str] = [a.get("text") or "", a.get("recommendation") or ""]
    parts += a.get("estimated_parts") or []
    parts += answer.get("unknowns") or []
    parts += answer.get("next_actions") or []
    for gap in answer.get("gaps") or []:
        parts.append(gap.get("subject") or "")
        parts.append((gap.get("basis") or {}).get("reason") or "")
    for dispute in answer.get("disputes") or []:
        parts.append(dispute.get("subject") or "")
        for side in dispute.get("sides") or []:
            parts.append(side.get("statement") or "")
    return "\n".join(p for p in parts if p)


def evidence_text(answer: dict[str, Any]) -> str:
    """답변에 실린 근거 원문(Evidence + 추가 원문 근거)을 모은다."""
    parts = [e.get("snippet") or "" for e in answer.get("evidence") or []]
    parts += [r.get("snippet") or "" for r in answer.get("raw_signals") or []]
    return "\n".join(parts)


def full_payload_text(answer: dict[str, Any]) -> str:
    """PII·금칙어 검사 대상. 답변·evidence·subgraph 를 전부 포함한다."""
    parts = [answer_prose(answer), evidence_text(answer)]
    for e in answer.get("evidence") or []:
        parts += [e.get("locator") or "", e.get("source_id") or ""]
    for r in answer.get("raw_signals") or []:
        parts += [r.get("locator") or "", r.get("source_id") or ""]
    sub = answer.get("subgraph") or {}
    for node in sub.get("nodes") or []:
        parts.append(node.get("label_text") or "")
    for c in answer.get("claims") or []:
        parts.append(c.get("statement") or "")
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# 1. route
# ---------------------------------------------------------------------------


def check_route(answer: dict[str, Any], question: dict[str, Any], golden: Golden) -> CheckResult:
    expected = question.get("expected_route")
    if not expected:
        return CheckResult("route", True, ["기대 경로 미지정"], skipped=True)
    actual = (answer.get("route") or {}).get("retriever")
    if actual == expected:
        return CheckResult("route", True)
    return CheckResult("route", False, [f"라우팅 불일치: 기대 {expected} / 실제 {actual}"])


# ---------------------------------------------------------------------------
# 2. evidence_match
# ---------------------------------------------------------------------------


def traceable_raw_signals(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """원본까지 갈 수 있는 raw_signal 만 인용 대상으로 인정한다.

    raw_signals 를 인용 대상으로 세는 근거가 "source_id·locator·발췌를 그대로 갖고 있어
    사람이 원본으로 갈 수 있다"는 것이므로, 그 둘이 비면 근거가 사라진다.
    """
    out: list[dict[str, Any]] = []
    for r in answer.get("raw_signals") or []:
        if (r.get("source_id") or "").strip() and (r.get("locator") or "").strip():
            out.append(r)
    return out


def check_evidence_match(
    answer: dict[str, Any], question: dict[str, Any], golden: Golden
) -> CheckResult:
    reasons: list[str] = []
    notes: list[str] = []
    evidence = answer.get("evidence") or []
    ids = [e.get("evidence_id") for e in evidence]

    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        reasons.append(f"evidence_id 중복: {sorted(dupes)}")

    # 각주는 계약대로 evidence[] 안에서만 해소돼야 한다.
    # raw_signals 를 인용 대상으로 인정하는 것은 "기대 발췌를 어디서 찾는가"의 문제이지,
    # 각주가 가리켜도 되는 대상을 넓히는 것이 아니다(contracts/answer.schema.json citations).
    id_set = set(ids)
    for cit in answer.get("citations") or []:
        if cit.get("evidence_id") not in id_set:
            reasons.append(
                f"각주 [{cit.get('marker')}] 가 없는 evidence_id 를 가리킨다: {cit.get('evidence_id')}"
            )

    minimum = question.get("expected_evidence_min", 1)
    if len(evidence) < minimum:
        reasons.append(f"evidence 부족: 기대 최소 {minimum}건 / 실제 {len(evidence)}건")

    snippets = "\n".join(e.get("snippet") or "" for e in evidence)
    raw_ok = traceable_raw_signals(answer)
    raw_all = answer.get("raw_signals") or []

    from_evidence = 0
    from_raw = 0
    for item in question.get("must_include") or []:
        quote = item.get("source_quote")
        if not quote:
            continue
        if contains(snippets, quote):
            from_evidence += 1
            continue

        hit = next((r for r in raw_ok if contains(r.get("snippet") or "", quote)), None)
        if hit:
            from_raw += 1
            notes.append(
                f"raw_signals 로 확인된 발췌: {quote!r} "
                f"→ {hit.get('source_id')} @ {hit.get('locator')}"
            )
            continue

        untraceable = next(
            (r for r in raw_all if contains(r.get("snippet") or "", quote)), None
        )
        if untraceable:
            reasons.append(
                "기대 발췌가 원본으로 갈 수 없는 raw_signal 에만 있다"
                f"(source_id·locator 필요): {quote!r} ({item.get('locator')})"
            )
        else:
            reasons.append(
                f"기대 발췌가 evidence·raw_signals 어디에도 없다: {quote!r} ({item.get('locator')})"
            )

    if from_evidence or from_raw:
        notes.insert(0, f"발췌 출처: evidence {from_evidence}건 · raw_signals {from_raw}건")

    for e in evidence:
        if not e.get("locator"):
            reasons.append(f"locator 없는 evidence: {e.get('evidence_id')}")

    return CheckResult("evidence_match", not reasons, reasons + notes)


# ---------------------------------------------------------------------------
# 3. keyfact
# ---------------------------------------------------------------------------


# 금칙어 자체가 PII 값인지 보는 형태 판정.
# find_pii 는 온전한 값만 잡으므로, 잘린 접두("010-")처럼 온전하지 않은 표기도 여기서 걸러
# PII 성격으로 분류한다. 분류가 애매하면 넓게 잡는 쪽이 안전하다(범위가 좁아지지 않는다).
_PII_SHAPED = re.compile(
    r"""
      \b0(?:1[016789]|2|[3-6]\d)[-.\s(]      # 국내 전화 접두. 잘린 '010-' '02-' 도 잡는다
    | \b0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}   # 온전한 전화번호
    | [A-Za-z0-9._%+-]+@                     # 이메일 로컬파트 + @
    | \b\d{6}\s*-\s*[1-4]                    # 주민등록번호 앞자리 + 뒷자리 첫 글자
    | \b\d{4}[-\s]\d{4}[-\s]\d{4}            # 카드·계좌형 숫자 덩어리
    """,
    re.VERBOSE,
)


def is_pii_banned_term(term: str) -> bool:
    """must_not_include 항목이 PII 성격인가.

    PII 성격이면 답변 본문이 아니라 payload 전체(answer·evidence·subgraph)에서 0건이어야 한다.
    Gate 1A 5번과 §8 하드 게이트를 채점기 쪽에서도 약화시키지 않기 위한 예외다.
    """
    return bool(find_pii(term)) or bool(_PII_SHAPED.search(term))


def check_keyfact(answer: dict[str, Any], question: dict[str, Any], golden: Golden) -> CheckResult:
    reasons: list[str] = []
    prose = answer_prose(answer)
    for item in question.get("must_include") or []:
        fact = item.get("fact")
        if fact and not contains(prose, fact):
            reasons.append(f"답변에 없는 필수 사실: {fact!r}")

    payload = full_payload_text(answer)
    for banned in question.get("must_not_include") or []:
        if is_pii_banned_term(banned):
            # PII 는 인용이든 생성이든 노출 자체가 위반이다. 검사 범위를 좁히지 않는다.
            if contains(payload, banned):
                reasons.append(f"PII 금칙어가 답변 payload 에 있다: {banned!r}")
        elif contains(prose, banned):
            # 내용 금칙어는 모델이 말한 것만 잡는다. 원문 발췌를 인용한 것은 환각이 아니다.
            reasons.append(f"나오면 안 되는 표현이 있다: {banned!r}")

    return CheckResult("keyfact", not reasons, reasons)


# ---------------------------------------------------------------------------
# 4. masking
# ---------------------------------------------------------------------------


def _load_pii_rules(path: Path = PII_CONFIG) -> tuple[list[tuple[str, re.Pattern]], list[re.Pattern], set[str]]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    detectors = [
        (d["id"], re.compile(d["regex"]))
        for d in cfg["detectors"]
        if d["id"] in HARD_GATE_DETECTORS
    ]
    guards = [
        re.compile(g["pattern"])
        for g in cfg.get("false_positive_guards", [])
        if g.get("pattern")
    ]
    allow: set[str] = set()
    for d in cfg["detectors"]:
        for value in (d.get("allowlist") or {}).get("values", []) or []:
            allow.add(value.lower())
    return detectors, guards, allow


_PII_RULES: tuple | None = None


def pii_rules():
    global _PII_RULES
    if _PII_RULES is None:
        _PII_RULES = _load_pii_rules()
    return _PII_RULES


def find_pii(text: str) -> list[tuple[str, str]]:
    """(detector_id, 매칭 문자열) 목록. 오탐 방지 규칙을 먼저 적용한다."""
    detectors, guards, allow = pii_rules()
    scrubbed = text
    for guard in guards:
        # epoch ms · Slack ts 처럼 PII 가 아닌 것으로 실측된 숫자열을 먼저 지운다.
        # 010 으로 시작하는 13자리는 실제 전화번호 오입력이라 남긴다(config FP-EPOCH-MS 주석).
        scrubbed = guard.sub(lambda m: "" if not m.group(0).startswith("010") else m.group(0), scrubbed)

    hits: list[tuple[str, str]] = []
    for det_id, pattern in detectors:
        for m in pattern.finditer(scrubbed):
            value = m.group(0)
            if det_id == "PII-EMAIL" and value.lower() in allow:
                continue
            hits.append((det_id, value))
    return hits


def check_masking(answer: dict[str, Any], question: dict[str, Any], golden: Golden) -> CheckResult:
    hits = find_pii(full_payload_text(answer))
    if not hits:
        return CheckResult("masking", True)
    reasons = [f"PII 노출 {det}: {value!r}" for det, value in hits]
    return CheckResult("masking", False, reasons)


# ---------------------------------------------------------------------------
# 5. traceability
# ---------------------------------------------------------------------------

# 버전·금액처럼 점과 쉼표가 섞인 숫자를 통째로 잡는다. "2.0.0" "3.77" "2,564"
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)*")

# 대문자로 시작하는 로마자 토큰. KB · DTS · AWS · OpenBuilder · 오로라웍스 를 잡는다.
_ASCII_PROPER = re.compile(r"[A-Z][A-Za-z0-9+]{1,}")

# 한국어 기관·제품명은 접미사로 잡는다. 정규식으로 한국어 고유명사를 일반적으로 잡을 수는 없으므로,
# 실측 자료에 실제로 등장하는 접미사만 나열했다(없는 회사명을 지어내는 것을 잡는 것이 목적이다).
#
# 괄호는 이름의 일부로 보지 않는다. 넣어 두면 토큰이 괄호를 넘어가 붙어 실재하는 말을 없는 회사
# 이름으로 만든다. 실측(GQ-D5): "퇴직연금(은행·증권사·생명보험사·손해보험사)과 (증권사)WM(증권사…)"
# 에서 `퇴직연금(은행`·`(증권사)WM(증권` 이 한 토큰으로 잡혔는데, `퇴직연금`·`은행`·`증권사`·`WM` 은
# 각각 근거에 실재한다. 붙여 놓은 형태만 없으니 무근거로 판정돼 하드 게이트가 허위로 깨졌다.
# 괄호를 뺀 뒤에도 지어낸 이름은 그대로 잡힌다 — `(가상)없는보험` 은 `없는보험` 으로 잡혀 걸린다.
# 같은 규칙을 서버에서 집행하는 `api/guards.py._KO_ORG` 와 문자클래스를 일치시킨 것이다.
_KO_ORG = re.compile(
    r"[가-힣A-Za-z0-9]{2,12}"
    r"(?:손해보험|해상화재|생명보험|생명|은행|캐피탈|카드사|증권|화재|렌터카|전선|"
    r"텔레콤|시스템|물산|건설|코리아|그룹|공사|공단|중앙회|신용정보|저축은행|보험)"
)


def _strip_commas(text: str) -> str:
    return text.replace(",", "")


def check_traceability(
    answer: dict[str, Any], question: dict[str, Any], golden: Golden
) -> CheckResult:
    prose = _CITATION_MARKER.sub(" ", answer_prose(answer))
    support = _strip_commas(normalize(evidence_text(answer)))
    question_text = _strip_commas(normalize(question.get("question", "")))

    exempt_numbers: set[str] = set()
    exempt_terms: set[str] = set()
    for allowed in question.get("traceability_allow") or []:
        exempt_terms.add(normalize(allowed))
        exempt_numbers.update(_strip_commas(t) for t in _NUMBER.findall(allowed))

    reasons: list[str] = []

    for token in _NUMBER.findall(prose):
        value = _strip_commas(token)
        if value in exempt_numbers or value in question_text or value in support:
            continue
        reasons.append(f"근거 없는 숫자: {token!r}")

    for token in set(_ASCII_PROPER.findall(prose)) | set(_KO_ORG.findall(prose)):
        norm = normalize(token)
        if norm in exempt_terms or norm in question_text or norm in support:
            continue
        reasons.append(f"근거 없는 고유명사: {token!r}")

    return CheckResult("traceability", not reasons, sorted(set(reasons)))


# ---------------------------------------------------------------------------
# 6. gap_verdict
# ---------------------------------------------------------------------------


def check_gap_verdict(
    answer: dict[str, Any], question: dict[str, Any], golden: Golden
) -> CheckResult:
    expected = question.get("expected_gap_verdict")
    forbidden = question.get("forbidden_gap_verdicts") or []
    need_oos = question.get("require_out_of_scope_by_design")
    subject_key = question.get("gap_subject_contains")

    gaps = answer.get("gaps") or []
    reasons: list[str] = []

    # 문항과 무관하게 항상 지키는 불변: CONFIRMED 는 명시적 부재 근거를 반드시 갖는다.
    for gap in gaps:
        if gap.get("verdict") == "CONFIRMED":
            basis = gap.get("basis") or {}
            if not basis.get("explicit_absence_evidence_ids"):
                reasons.append(
                    f"CONFIRMED 인데 명시적 부재 근거가 비어 있다: {gap.get('subject')!r}"
                )

    if expected is None and not forbidden and not need_oos:
        return CheckResult("gap_verdict", not reasons, reasons, skipped=not reasons)

    matched = [g for g in gaps if not subject_key or contains(g.get("subject", ""), subject_key)]
    if not matched:
        reasons.append(f"기대한 gap 항목이 없다 (subject 에 {subject_key!r} 포함 필요)")
        return CheckResult("gap_verdict", False, reasons)

    verdicts = {g.get("verdict") for g in matched}
    if expected is not None and expected not in verdicts:
        reasons.append(f"gap 판정 불일치: 기대 {expected} / 실제 {sorted(verdicts)}")
    for bad in forbidden:
        if bad in verdicts:
            reasons.append(f"나오면 안 되는 gap 판정: {bad}")
    if need_oos and not any(
        (g.get("basis") or {}).get("out_of_scope_by_design") for g in matched
    ):
        reasons.append("의도적 비범위(out_of_scope_by_design)로 표시되지 않았다")

    return CheckResult("gap_verdict", not reasons, reasons)


# ---------------------------------------------------------------------------
# 7. subgraph
# ---------------------------------------------------------------------------


def check_subgraph(answer: dict[str, Any], question: dict[str, Any], golden: Golden) -> CheckResult:
    reasons: list[str] = []
    sub = answer.get("subgraph") or {}
    nodes = sub.get("nodes") or []
    edges = sub.get("edges") or []

    if not nodes:
        reasons.append("subgraph 에 노드가 없다")
        return CheckResult("subgraph", False, reasons)

    if not any(n.get("rank") == "focal" for n in nodes):
        reasons.append("focal 노드가 없다(질문의 중심 엔티티가 표시되지 않음)")

    if len(nodes) > 50:
        reasons.append(f"노드 상한 초과: {len(nodes)} > 50")
    if len(edges) > 100:
        reasons.append(f"엣지 상한 초과: {len(edges)} > 100")

    node_ids = {n.get("id") for n in nodes}
    for e in edges:
        for side in ("from", "to"):
            if e.get(side) not in node_ids:
                reasons.append(f"엣지가 subgraph 밖 노드를 가리킨다: {e.get(side)}")

    marked = {m for n in nodes for m in (n.get("citation_markers") or [])}
    for cit in answer.get("citations") or []:
        if cit.get("marker") not in marked:
            reasons.append(f"각주 [{cit.get('marker')}] 에 대응하는 노드가 subgraph 에 없다")

    return CheckResult("subgraph", not reasons, reasons)


# ---------------------------------------------------------------------------
# 8. conflict
# ---------------------------------------------------------------------------


def check_conflict(answer: dict[str, Any], question: dict[str, Any], golden: Golden) -> CheckResult:
    spec = question.get("conflict")
    if not spec:
        return CheckResult("conflict", True, ["충돌 문항 아님"], skipped=True)

    reasons: list[str] = []
    prose = answer_prose(answer)
    for side in spec.get("sides") or []:
        if not contains(prose, side["value"]):
            reasons.append(f"충돌의 한쪽이 답변에서 빠졌다(단일 합성 의심): {side['value']!r}")

    disputes = answer.get("disputes") or []
    hit = [d for d in disputes if len(d.get("sides") or []) >= 2]
    if not hit:
        reasons.append("disputes 에 양측(2개 이상)이 담긴 항목이 없다")
    else:
        for d in hit:
            for s in d.get("sides") or []:
                if not s.get("evidence_ids"):
                    reasons.append(f"근거 없는 disputes 항목: {s.get('statement')!r}")

    return CheckResult("conflict", not reasons, reasons)


# ---------------------------------------------------------------------------

CheckFn = Callable[[dict[str, Any], dict[str, Any], Golden], CheckResult]

ALL_CHECKS: tuple[CheckFn, ...] = (
    check_route,
    check_evidence_match,
    check_keyfact,
    check_masking,
    check_traceability,
    check_gap_verdict,
    check_subgraph,
    check_conflict,
)

CHECK_NAMES: tuple[str, ...] = (
    "route",
    "evidence_match",
    "keyfact",
    "masking",
    "traceability",
    "gap_verdict",
    "subgraph",
    "conflict",
)


def run_checks(
    answer: dict[str, Any], question: dict[str, Any], golden: Golden
) -> list[CheckResult]:
    return [fn(answer, question, golden) for fn in ALL_CHECKS]


def failed_check_names(results: list[CheckResult]) -> set[str]:
    return {r.name for r in results if not r.passed}
