"""eval 하네스 셀프테스트.

여기서 지키려는 것은 두 가지다.

1. **기대값이 자료 원문에서 왔다는 것**
   golden.yaml 의 모든 source_quote 를 원본 파일(xlsx 셀 / 추출 txt / slack jsonl)에서 다시 찾아본다.
   시스템 출력을 베껴 넣으면 여기서 걸린다.

2. **채점기가 항상 초록불이 아니라는 것 (mutation 셀프테스트)**
   정상 픽스처는 전 채점기를 통과하고, 한 군데씩 망가뜨린 픽스처 11건은
   **각각 정확히 그 사유로만** 실패해야 한다. 실패 채점기 집합을 정확히 비교하므로
   채점기가 무뎌지면(항상 통과) 이 테스트가 먼저 빨개진다.

   뒤쪽 3건은 채점 범위 교정(`eval/CHECKER-CHANGES.md`) 이후에도 진짜 결함이 잡히는지를
   증명하려고 추가했다. 각각 "본문이 금칙어를 말함" · "PII 가 raw_signals 에만 있음" ·
   "원본으로 갈 수 없는 raw_signal 을 인용함" 이다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.checkers import (  # noqa: E402
    CHECK_NAMES,
    check_evidence_match,
    check_keyfact,
    failed_check_names,
    find_pii,
    is_pii_banned_term,
)
from eval.golden_loader import (  # noqa: E402
    iter_expected_quotes,
    load_golden,
    verify_quote,
)
from eval.runner import load_fixture, main as runner_main, score  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "eval" / "fixtures"
CONTRACTS = REPO / "contracts"
NORMAL = FIXTURES / "normal_answer.json"

# mutation 파일 → (대상 문항, 정확히 깨져야 하는 채점기 집합)
MUTATIONS = {
    "mut_broken_citation.json": ("GQ-D8", {"evidence_match"}),
    "mut_unsupported_number.json": ("GQ-D8", {"traceability"}),
    # PII 가 evidence 발췌에만 있는 경우다(본문에는 없다). 채점 범위 교정 뒤에도 masking 이 잡는다.
    "mut_pii_leak.json": ("GQ-K1", {"masking"}),
    "mut_gap_overclaim.json": ("GQ-G1", {"gap_verdict"}),
    "mut_conflict_collapsed.json": ("GQ-D7", {"conflict"}),
    "mut_missing_subgraph.json": ("GQ-D6", {"subgraph"}),
    "mut_wrong_route.json": ("GQ-G1", {"route"}),
    "mut_missing_keyfact.json": ("GQ-A2", {"keyfact"}),
    # --- 채점 범위 교정과 함께 추가한 3건 (CHECKER-CHANGES.md) ---
    # 답변 본문이 금칙어를 말한다 → 범위를 prose 로 좁혔어도 여전히 잡혀야 한다
    "mut_banned_in_prose.json": ("GQ-A2", {"keyfact"}),
    # PII 가 새로 인용 대상이 된 raw_signals 발췌에만 있다 → 하드 게이트는 그대로다
    "mut_pii_in_raw_signal.json": ("GQ-K1", {"masking"}),
    # 기대 발췌가 원본으로 갈 수 없는 raw_signal 에만 있다 → 인용으로 인정하지 않는다
    "mut_phantom_raw_signal.json": ("GQ-G1", {"evidence_match"}),
}

# PII 가 들어 있어도 되는 픽스처. 둘 다 가짜 번호(010-0000-0000)여야 한다.
PII_FIXTURES = {"mut_pii_leak.json", "mut_pii_in_raw_signal.json"}


@pytest.fixture(scope="module")
def golden():
    return load_golden()


# ---------------------------------------------------------------------------
# 1. golden.yaml 구성
# ---------------------------------------------------------------------------


def test_question_count_in_range(golden):
    """1A 범위는 15~20문항이다(REVISED §0 · §6 A8)."""
    assert 15 <= len(golden.questions) <= 20


def test_demo_and_gate_questions_present(golden):
    """§9 가 지정한 데모 5개 + 게이트 시연 3개는 반드시 있어야 한다."""
    ids = {q["id"] for q in golden.questions}
    required = {f"GQ-D{i}" for i in range(1, 9)}
    assert required <= ids, f"빠진 필수 문항: {sorted(required - ids)}"


def test_question_ids_unique(golden):
    ids = [q["id"] for q in golden.questions]
    assert len(ids) == len(set(ids))


def test_type_distribution(golden):
    """§9 말미가 요구한 유형이 전부 채워졌는지 본다."""
    counts: dict[str, int] = {}
    for q in golden.questions:
        counts[q["type"]] = counts.get(q["type"], 0) + 1

    assert counts.get("product_fact", 0) >= 3
    assert counts.get("multihop", 0) >= 2
    assert counts.get("conflict", 0) >= 2
    assert counts.get("gap", 0) >= 2, "CONFIRMED·POSSIBLE 을 각각 시험할 gap 문항이 2개 필요하다"
    assert counts.get("slack", 0) >= 1
    assert counts.get("masking", 0) >= 1
    assert counts.get("rare_critical", 0) >= 1
    assert counts.get("strategic", 0) >= 1
    assert counts.get("entity", 0) >= 3, "account/deal/BD 조회 문항"


def test_routes_are_valid(golden):
    for q in golden.questions:
        assert q["expected_route"] in {"Q-E", "Q-M", "Q-S"}, q["id"]


def test_gap_questions_cover_all_three_verdicts(golden):
    """gap 3값이 실제로 구분되는지 — CONFIRMED 와 POSSIBLE 이 둘 다 기대값에 있어야 한다."""
    verdicts = {
        q.get("expected_gap_verdict")
        for q in golden.questions
        if q.get("expected_gap_verdict")
    }
    assert "CONFIRMED" in verdicts
    assert "POSSIBLE" in verdicts


def test_every_question_is_grounded(golden):
    """근거 인용과 '왜 이 기대값인가'가 없는 문항을 허용하지 않는다."""
    for q in golden.questions:
        quotes = list(iter_expected_quotes(q))
        assert quotes, f"{q['id']}: 원문 인용이 하나도 없다"
        assert q.get("notes", "").strip(), f"{q['id']}: notes 가 비어 있다"
        for item in q.get("must_include") or []:
            assert item.get("locator"), f"{q['id']}: locator 없는 기대값"
            assert item.get("source_file"), f"{q['id']}: source_file 없는 기대값"


# ---------------------------------------------------------------------------
# 2. 기대값이 원문에서 왔는가 (날조 방지)
# ---------------------------------------------------------------------------


def _missing_source_roots(golden) -> list[str]:
    """대조에 필요한 자료 루트 중 없는 것. raw_probe·sources 는 gitignore 라 클론 직후엔 비어 있다."""
    return [name for name, path in golden.paths.items() if not Path(path).exists()]


def test_golden_quotes_exist_in_sources(golden):
    """모든 source_quote 를 원본에서 다시 찾는다. 이것이 기대값 날조 방지 장치다."""
    missing = _missing_source_roots(golden)
    if missing:
        pytest.skip(f"원본 자료 루트가 없어 원문 대조를 건너뛴다: {missing}")

    failures: list[str] = []
    checked = 0
    for q in golden.questions:
        for item in iter_expected_quotes(q):
            checked += 1
            ok, reason = verify_quote(golden, item)
            if not ok:
                failures.append(f"{q['id']}: {reason}")
    assert checked >= 40, f"대조한 인용이 너무 적다: {checked}"
    assert not failures, "\n".join(failures)


def test_golden_file_has_no_pii(golden):
    """eval 자산은 restricted 로 다루되, 애초에 PII 를 담지 않는다."""
    text = (REPO / "eval" / "golden.yaml").read_text(encoding="utf-8")
    assert find_pii(text) == []


# ---------------------------------------------------------------------------
# 3. 픽스처
# ---------------------------------------------------------------------------


def _all_fixture_paths() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def test_fixtures_have_no_real_pii():
    """masking mutation 만 예외이고, 그것도 가짜 번호(010-0000-0000)여야 한다."""
    for path in _all_fixture_paths():
        text = path.read_text(encoding="utf-8")
        hits = find_pii(text)
        if path.name in PII_FIXTURES:
            assert hits, "PII 노출을 검증하는 픽스처인데 정규식이 아무것도 못 잡았다"
            assert {v for _, v in hits} == {"010-0000-0000"}, (
                f"실제 개인정보로 보이는 값이 들어 있다: {hits}"
            )
        else:
            assert hits == [], f"{path.name} 에 PII 가 있다: {hits}"


def _answer_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    resources = []
    for path in CONTRACTS.glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(doc, default_specification=DRAFT202012)
        # $id 로도, 파일명 상대경로로도 찾을 수 있게 둘 다 등록한다.
        resources.append((doc.get("$id", path.name), resource))
        resources.append((path.name, resource))
    registry = Registry().with_resources(resources)
    schema = json.loads((CONTRACTS / "answer.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=registry)


@pytest.mark.parametrize("path", _all_fixture_paths(), ids=lambda p: p.name)
def test_fixture_matches_answer_contract(path):
    """픽스처가 A0 의 Answer 계약을 지키는지. 계약 밖 필드를 쓰면 여기서 걸린다."""
    validator = _answer_validator()
    for qid, answer in load_fixture(path):
        errors = sorted(validator.iter_errors(answer), key=lambda e: list(e.path))
        assert not errors, f"{path.name} / {qid}: " + "; ".join(
            f"{list(e.path)} {e.message}" for e in errors[:5]
        )


def test_forbidden_fields_absent_from_fixtures():
    """confidence(숫자)·denied_source_count 는 계약에서 금지된 필드다."""
    for path in _all_fixture_paths():
        for qid, answer in load_fixture(path):
            for banned in ("confidence", "confidence_band", "denied_source_count"):
                assert banned not in answer, f"{path.name}/{qid} 에 금지 필드 {banned}"


# ---------------------------------------------------------------------------
# 4. mutation 셀프테스트 — 채점기가 항상 초록불이 아님을 증명한다
# ---------------------------------------------------------------------------


def test_normal_fixture_passes_every_check(golden):
    outcomes = score(golden, load_fixture(NORMAL))
    assert len(outcomes) >= 6, "정상 픽스처가 채점기 전부를 지나가도록 충분한 문항을 담아야 한다"
    for oc in outcomes:
        assert oc.passed, f"{oc.question_id} 실패: " + "; ".join(
            f"{r.name}({'; '.join(r.reasons)})" for r in oc.results if not r.passed
        )


def test_normal_fixture_exercises_all_checkers(golden):
    """정상 픽스처가 8개 채점기를 실제로(skip 아닌 상태로) 한 번씩은 통과했는가."""
    exercised: set[str] = set()
    for oc in score(golden, load_fixture(NORMAL)):
        for r in oc.results:
            if not r.skipped:
                exercised.add(r.name)
    assert exercised == set(CHECK_NAMES), f"한 번도 돌지 않은 채점기: {sorted(set(CHECK_NAMES) - exercised)}"


@pytest.mark.parametrize("filename", sorted(MUTATIONS), ids=lambda n: n[4:-5])
def test_mutation_fails_for_exactly_its_reason(golden, filename):
    expected_qid, expected_failures = MUTATIONS[filename]
    pairs = load_fixture(FIXTURES / filename)
    assert [qid for qid, _ in pairs] == [expected_qid]

    outcomes = score(golden, pairs)
    (outcome,) = outcomes
    actual = failed_check_names(outcome.results)

    assert actual == expected_failures, (
        f"{filename}: 기대 {sorted(expected_failures)} / 실제 {sorted(actual)}\n"
        + "\n".join(
            f"  {r.name}: {'; '.join(r.reasons)}" for r in outcome.results if not r.passed
        )
    )


def test_every_checker_has_a_mutation():
    """채점기 8종 모두 최소 1건의 mutation 으로 실패가 증명돼야 한다."""
    covered: set[str] = set()
    for _, names in MUTATIONS.values():
        covered |= names
    assert covered == set(CHECK_NAMES), (
        f"실패가 증명되지 않은 채점기: {sorted(set(CHECK_NAMES) - covered)}"
    )


# ---------------------------------------------------------------------------
# 4-1. 채점 범위 교정이 의도한 것만 바꿨는가 (eval/CHECKER-CHANGES.md)
#
# 위 mutation 이 "여전히 잡는가"를 보장한다면, 여기서는 "무엇을 더 이상 잡지 않기로 했는가"와
# "그 완화가 어디까지인가"를 못 박는다. 두 방향을 같이 걸어야 범위 교정이 슬그머니
# 게이트 완화로 번지지 않는다.
# ---------------------------------------------------------------------------

_EV = {"evidence_id": "e1", "source_id": "src_x", "locator": "시트!A1"}


def _answer(prose: str = "", evidence=None, raw=None, citations=None) -> dict:
    return {
        "answer": {"text": prose},
        "citations": citations or [],
        "evidence": evidence or [],
        "raw_signals": raw or [],
    }


def test_content_banned_term_quoted_from_source_is_not_a_failure():
    """교정 1: 원문 발췌에 금칙어가 있다는 이유로 문항이 떨어지지 않는다.

    발췌를 그대로 인용한 것은 환각이 아니다(GQ-D8·GQ-A1 이 이 이유로 떨어졌다).
    """
    question = {"must_not_include": ["1.0.0부터"]}
    answer = _answer(
        prose="이 기능은 2.0.0부터 제공됩니다.",
        evidence=[{**_EV, "snippet": "다른 행: 대화하기 팝업창은 1.0.0부터 제공"}],
    )
    assert check_keyfact(answer, question, None).passed


def test_content_banned_term_in_prose_still_fails():
    """교정 1의 반대편. 모델이 직접 말하면 여전히 실패다."""
    question = {"must_not_include": ["1.0.0부터"]}
    answer = _answer(
        prose="이 기능은 1.0.0부터 제공됩니다.",
        evidence=[{**_EV, "snippet": "무관한 발췌"}],
    )
    result = check_keyfact(answer, question, None)
    assert not result.passed
    assert any("나오면 안 되는 표현" in r for r in result.reasons)


def test_pii_banned_term_is_checked_across_whole_payload():
    """교정 1의 예외. PII 성격 금칙어는 evidence 에만 있어도 실패다(§5 Gate 1A 5 · §8)."""
    question = {"must_not_include": ["010-0000-0000"]}
    answer = _answer(
        prose="연락처는 개인정보라 알려드리지 않습니다.",
        evidence=[{**_EV, "snippet": "고객사 담당자 | 연락처 → 010-0000-0000"}],
    )
    result = check_keyfact(answer, question, None)
    assert not result.passed
    assert any("PII 금칙어" in r for r in result.reasons)


@pytest.mark.parametrize(
    "term",
    ["010-0000-0000", "010-", "02-", "031-", "hong@example.com", "900101-1234567"],
)
def test_pii_shaped_terms_are_classified_as_pii(term):
    assert is_pii_banned_term(term)


def test_golden_banned_terms_are_all_content_natured(golden):
    """오늘 golden 의 must_not_include 는 전부 내용 성격이다.

    PII 값 자체를 금칙어로 적는 문항이 새로 생기면 이 테스트가 빨개진다.
    그때 분류가 맞는지 사람이 한 번 보고 이 목록을 갱신하라는 뜻이다.
    """
    pii_natured = [
        (q["id"], t)
        for q in golden.questions
        for t in (q.get("must_not_include") or [])
        if is_pii_banned_term(t)
    ]
    assert pii_natured == [], f"PII 성격으로 분류된 금칙어: {pii_natured}"


def test_evidence_match_accepts_traceable_raw_signal():
    """교정 2: raw_signals 도 인용 대상이다. 단 어느 쪽에서 왔는지 결과에 남는다."""
    question = {
        "expected_evidence_min": 1,
        "must_include": [
            {"fact": "통제 불가", "source_quote": "통제 불가 : 보험설계지원", "locator": "Guest통제!B2"}
        ],
    }
    answer = _answer(
        evidence=[{**_EV, "snippet": "무관한 발췌"}],
        raw=[
            {
                "evidence_id": "rs1",
                "source_id": "src_bd_overview",
                "locator": "Guest통제!B2",
                "snippet": "통제 불가 : 보험설계지원, 자동차금융, 퇴직연금",
            }
        ],
    )
    result = check_evidence_match(answer, question, None)
    assert result.passed
    assert any("발췌 출처: evidence 0건 · raw_signals 1건" in r for r in result.reasons)
    assert any("Guest통제!B2" in r and "raw_signals 로 확인된 발췌" in r for r in result.reasons)


def test_evidence_match_rejects_untraceable_raw_signal():
    """교정 2의 한계선. 원본으로 갈 수 없는 raw_signal 은 인용으로 세지 않는다."""
    question = {
        "expected_evidence_min": 1,
        "must_include": [
            {"fact": "통제 불가", "source_quote": "통제 불가 : 보험설계지원", "locator": "Guest통제!B2"}
        ],
    }
    answer = _answer(
        evidence=[{**_EV, "snippet": "무관한 발췌"}],
        raw=[{"evidence_id": "", "source_id": "", "locator": "", "snippet": "통제 불가 : 보험설계지원"}],
    )
    result = check_evidence_match(answer, question, None)
    assert not result.passed
    assert any("원본으로 갈 수 없는 raw_signal" in r for r in result.reasons)


def test_citation_must_still_resolve_into_evidence():
    """교정 2가 각주까지 넓히지는 않았다. 계약상 각주는 evidence[] 로만 해소된다."""
    answer = _answer(
        evidence=[{**_EV, "snippet": "발췌"}],
        raw=[
            {
                "evidence_id": "rs1",
                "source_id": "src_x",
                "locator": "시트!B2",
                "snippet": "추가 원문 근거",
            }
        ],
        citations=[{"marker": 1, "evidence_id": "rs1"}],
    )
    result = check_evidence_match(answer, {"expected_evidence_min": 1}, None)
    assert not result.passed
    assert any("없는 evidence_id" in r for r in result.reasons)


def test_masking_checker_scope_is_unchanged(golden):
    """하드 게이트인 masking 은 payload 전체를 그대로 본다. 교정 대상이 아니었다."""
    source = (REPO / "eval" / "checkers.py").read_text(encoding="utf-8")
    assert "def check_masking" in source
    assert "find_pii(full_payload_text(answer))" in source


# ---------------------------------------------------------------------------
# 5. runner 계약
# ---------------------------------------------------------------------------


def test_runner_exit_code_zero_on_normal(capsys):
    assert runner_main(["--fixture", str(NORMAL), "--quiet"]) == 0


@pytest.mark.parametrize("filename", sorted(MUTATIONS), ids=lambda n: n[4:-5])
def test_runner_exit_code_one_on_mutation(capsys, filename):
    assert runner_main(["--fixture", str(FIXTURES / filename), "--quiet"]) == 1


def test_runner_exit_code_two_on_missing_file(capsys):
    assert runner_main(["--fixture", str(FIXTURES / "does_not_exist.json")]) == 2


def test_runner_save_roundtrips_into_compare_checkers(tmp_path, capsys):
    """--save 한 묶음을 두 채점기 비교 도구가 그대로 읽어야 한다.

    한 번 받은 payload 로 채점기를 두 벌 돌리려고 만든 경로다. 형식이 어긋나면
    답변을 두 번 생성하게 되고, 그러면 채점기 차이와 답변 변동이 섞여 비교가 무의미해진다.
    """
    from eval.compare_checkers import load_pairs

    out = tmp_path / "snap.json"
    assert runner_main(["--fixture", str(NORMAL), "--quiet", "--save", str(out)]) == 0

    saved = load_pairs(out)
    assert [qid for qid, _ in saved] == [qid for qid, _ in load_fixture(NORMAL)]


def test_runner_reports_two_when_some_answers_could_not_be_fetched(capsys):
    """일부 문항을 못 받았으면 받은 것이 다 통과해도 0 을 돌리지 않는다.

    엔드포인트가 죽어 절반만 받은 채로 "전 문항 통과"가 되면 게이트가 무력해진다.
    """
    code = runner_main(
        ["--answer-endpoint", "http://127.0.0.1:9/ask", "--quiet", "--only", "GQ-D8"]
    )
    assert code == 2
    assert "답변 못 받음 GQ-D8" in capsys.readouterr().err


def test_runner_module_entrypoint_runs():
    """python -m eval.runner --fixture ... 가 실제로 도는지."""
    proc = subprocess.run(
        [sys.executable, "-m", "eval.runner", "--fixture", "eval/fixtures/normal_answer.json"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "통과 6" in proc.stdout


# ---------------------------------------------------------------------------
# 6. 채점기는 deterministic 이어야 한다 (LLM judge 금지 — 1B 사항)
# ---------------------------------------------------------------------------


def test_checkers_have_no_llm_or_network_dependency():
    banned = ("anthropic", "openai", "requests", "httpx", "from llm", "import llm")
    source = (REPO / "eval" / "checkers.py").read_text(encoding="utf-8")
    for token in banned:
        assert token not in source, f"채점기에 {token} 이 들어갔다 (LLM judge 는 1B)"


def test_checkers_are_stable_across_runs(golden):
    pairs = load_fixture(NORMAL)
    first = [
        (oc.question_id, tuple(sorted(failed_check_names(oc.results))))
        for oc in score(golden, pairs)
    ]
    second = [
        (oc.question_id, tuple(sorted(failed_check_names(oc.results))))
        for oc in score(golden, pairs)
    ]
    assert first == second


# ---------------------------------------------------------------------------
# 7. 고유명사 토큰화는 서버 가드와 어긋나서는 안 된다
#
# 같은 하드 게이트(무근거 숫자·고유명사 0건)를 `api/guards.py` 가 응답 시점에,
# `eval/checkers.py` 가 채점 시점에 각자 집행한다. 한쪽만 고쳐지면 서버가 살려 둔
# 문장을 채점기가 위반으로 신고해 게이트가 허위로 깨진다(R8 에서 실제로 발생).
# ---------------------------------------------------------------------------


def test_proper_noun_tokenizer_matches_the_server_guard():
    """두 곳의 _KO_ORG 문자클래스가 갈라지면 여기서 걸린다."""
    from api.guards import _KO_ORG as server_pattern
    from eval.checkers import _KO_ORG as checker_pattern

    assert checker_pattern.pattern == server_pattern.pattern, (
        "eval/checkers.py 와 api/guards.py 의 고유명사 정규식이 다르다. "
        "한쪽만 고치면 하드 게이트가 서로 다른 답을 낸다."
    )


def test_parenthesised_text_does_not_manufacture_phantom_proper_nouns(golden):
    """괄호를 넘어 붙은 토큰은 위반이 아니고, 지어낸 이름은 여전히 위반이다."""
    from eval.checkers import check_traceability

    question = {"question": "어떤 Capability가 여러 산업으로 확장될 가능성이 높은가?"}
    supported = {
        "answer": {"text": "퇴직연금(은행·증권사)과 (증권사)WM(증권사·은행)을 함께 봅니다."},
        "evidence": [{"snippet": "퇴직연금 은행 증권사 WM 대상"}],
    }
    assert check_traceability(supported, question, golden).passed, (
        "낱말이 모두 근거에 있는데 괄호 때문에 위반으로 잡혔다"
    )

    fabricated = {
        "answer": {"text": "(가상)없는보험과 허구캐피탈을 공략하십시오."},
        "evidence": [{"snippet": "퇴직연금 은행 증권사 WM 대상"}],
    }
    result = check_traceability(fabricated, question, golden)
    assert not result.passed, "지어낸 회사명이 통과했다 — 게이트가 무뎌졌다"
    assert any("없는보험" in r for r in result.reasons)
    assert any("허구캐피탈" in r for r in result.reasons)
