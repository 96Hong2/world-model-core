"""추가 골든 파일(eval/golden-arch.yaml)의 구성 검증.

정본 20문항용 tests/test_eval_harness.py 와 **같은 강도**로 잰다. 새 파일이 검증 없이
들어오면 기대값 날조를 막는 장치가 그 파일에는 없는 상태가 되기 때문이다.

핵심은 test_arch_quotes_exist_in_sources 다. 모든 source_quote 를 원본 파일에서 다시 찾는다.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from pathlib import Path

from eval.golden_loader import contains, read_source_text

ARCH_PATH = pathlib.Path(__file__).resolve().parents[1] / "eval" / "golden-arch.yaml"
VALID_ROUTES = {"Q-E", "Q-M", "Q-S"}
VALID_TYPES = {
    "strategic",
    "product_fact",
    "account_fact",
    "gap",
    "conflict",
    "rare_critical",
    "masking",
    "multi_hop",
}


@pytest.fixture(scope="module")
def arch() -> dict:
    return yaml.safe_load(ARCH_PATH.read_text(encoding="utf-8"))


def test_file_exists_and_parses(arch: dict) -> None:
    assert arch["questions"], "문항이 비어 있다"


def test_question_count_matches_declared(arch: dict) -> None:
    """golden.yaml 의 question_count 는 아무 테스트도 안 봐서 조용히 어긋났다.
    이 파일에서는 그러지 않게 여기서 잡는다."""
    assert arch["question_count"] == len(arch["questions"])


def test_ids_unique_and_prefixed(arch: dict) -> None:
    ids = [q["id"] for q in arch["questions"]]
    assert len(ids) == len(set(ids)), f"중복 id: {ids}"
    for qid in ids:
        assert qid.startswith(("GQ-T", "GQ-Q")), f"{qid}: 이 파일의 접두는 GQ-T·GQ-Q 다"


def test_ids_do_not_collide_with_main_golden(arch: dict) -> None:
    """정본과 id 가 겹치면 --golden 을 안 붙인 실행에서 헷갈린다."""
    main = yaml.safe_load(
        (ARCH_PATH.parent / "golden.yaml").read_text(encoding="utf-8")
    )
    main_ids = {q["id"] for q in main["questions"]}
    arch_ids = {q["id"] for q in arch["questions"]}
    assert not (main_ids & arch_ids), f"정본과 겹치는 id: {main_ids & arch_ids}"


def test_routes_and_types_are_valid(arch: dict) -> None:
    for q in arch["questions"]:
        assert q["expected_route"] in VALID_ROUTES, q["id"]
        assert q["type"] in VALID_TYPES, f"{q['id']}: {q['type']}"
        assert isinstance(q["expected_evidence_min"], int)
        assert q["expected_evidence_min"] >= 1, q["id"]


def test_every_question_is_grounded(arch: dict) -> None:
    """근거 없는 기대값을 막는다. locator·source_file 이 비면 사람이 원문에 갈 수 없다."""
    for q in arch["questions"]:
        assert q.get("notes", "").strip(), f"{q['id']}: notes 가 비었다"
        must = q.get("must_include") or []
        assert must, f"{q['id']}: 원문 인용이 하나도 없다"
        for item in must:
            assert item.get("fact", "").strip(), f"{q['id']}: fact 가 비었다"
            assert item.get("source_file", "").strip(), f"{q['id']}: source_file 이 비었다"
            assert item.get("locator", "").strip(), f"{q['id']}: locator 가 비었다"
            assert item.get("verify"), f"{q['id']}: verify 블록이 없다"
        assert q.get("must_not_include"), f"{q['id']}: must_not_include 가 비었다"


def test_arch_quotes_exist_in_sources(arch: dict) -> None:
    """기대값 날조 방지. 모든 source_quote 를 원본 파일에서 다시 찾는다.

    골든 정본의 같은 이름 테스트와 달리 저장소 안의 측정 문서는 없으면 skip 하지 않고
    실패시킨다. 조용히 꺼지면 장치가 무력해진다. 단 data/ 아래의 추출 산출물은
    자료를 안 받은 환경에 존재할 수 없으므로 그 항목만 건너뛴다.
    """

    class _Shim:
        """read_source_text 가 요구하는 expand() 만 제공한다."""

        def __init__(self, paths: dict[str, str]) -> None:
            self._paths = paths

        def expand(self, value: str) -> str:
            for key, root in self._paths.items():
                value = value.replace("{" + key + "}", root)
            return value

    shim = _Shim(arch["paths"])
    checked = 0
    for q in arch["questions"]:
        for item in q["must_include"]:
            quote = item.get("source_quote")
            if not quote:
                continue
            resolved = Path(shim.expand(item["source_file"]))
            if "data/" in str(resolved) and not resolved.exists():
                continue  # gitignore 된 추출 산출물. 저장소 파일이 아니라 강제할 수 없다
            text = read_source_text(shim, item["verify"], item["source_file"])
            assert contains(text, quote), (
                f"{q['id']}: source_quote 가 원문에 없다\n"
                f"  인용: {quote[:80]}\n  원문: {item['verify']}"
            )
            checked += 1
    assert checked >= 5, f"대조 건수가 {checked}건뿐이다. 인용이 빠졌는지 확인해라"


def test_measured_sources_are_registered(arch: dict) -> None:
    """골든이 가리키는 측정 문서가 sources.yaml 에 등록돼 있어야 한다.

    등록되지 않으면 그래프에 적재되지 않아 evidence 로 검색되지 않고,
    문항은 영원히 실패한다. 원인을 답변 품질로 오진하지 않게 여기서 잡는다.
    """
    sources = yaml.safe_load(
        (ARCH_PATH.parents[1] / "config" / "sources.yaml").read_text(encoding="utf-8")
    )
    registered = {s["path"] for s in sources["sources"] if "path" in s}
    shim_paths = arch["paths"]

    for q in arch["questions"]:
        for item in q["must_include"]:
            raw = item["source_file"]
            for key, root in shim_paths.items():
                raw = raw.replace("{" + key + "}", root)
            assert raw in registered, f"{q['id']}: sources.yaml 에 없는 원본 — {raw}"


def test_quantification_questions_forbid_fabricated_amounts(arch: dict) -> None:
    """GQ-Q 문항의 존재 이유가 금칙어다. 금액 금칙어가 빠지면 문항이 무력해진다."""
    for q in arch["questions"]:
        if not q["id"].startswith("GQ-Q"):
            continue
        banned = " ".join(q["must_not_include"])
        assert "억" in banned, f"{q['id']}: 금액 형태 금칙어가 없다"
