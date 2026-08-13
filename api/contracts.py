"""Answer 계약 검증기.

`contracts/answer.schema.json` 이 정본이다. 여기서는 읽어서 검증기만 만든다.
상대 $ref(`policy.schema.json#/$defs/claimDomain` 등)를 풀려면 계약 파일 전체를
레지스트리에 올려야 한다. $id 와 파일명 둘 다로 등록해 어느 표기로 참조해도 풀린다.
"""

from __future__ import annotations

import functools
import json
import pathlib
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "contracts"
ANSWER_SCHEMA = CONTRACTS_DIR / "answer.schema.json"


@functools.lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for path in sorted(CONTRACTS_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(doc, default_specification=DRAFT202012)
        resources.append((doc.get("$id", path.name), resource))
        resources.append((path.name, resource))
    return Registry().with_resources(resources)


@functools.lru_cache(maxsize=1)
def answer_validator() -> Draft202012Validator:
    schema = json.loads(ANSWER_SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=_registry())


def validate_answer(answer: dict[str, Any]) -> list[str]:
    """계약 위반 목록. 빈 리스트면 통과."""
    validator = answer_validator()
    return [
        f"{list(error.path)} {error.message}"
        for error in sorted(validator.iter_errors(answer), key=lambda e: list(e.path))
    ]


@functools.lru_cache(maxsize=1)
def source_type_catalog() -> dict[str, dict[str, Any]]:
    """`source_type.enum.json` 의 x-catalog. '이 자료가 무엇의 정본인가'가 여기에 있다."""
    doc = json.loads((CONTRACTS_DIR / "source_type.enum.json").read_text(encoding="utf-8"))
    return doc.get("x-catalog") or {}


@functools.lru_cache(maxsize=1)
def source_types() -> tuple[str, ...]:
    doc = json.loads((CONTRACTS_DIR / "source_type.enum.json").read_text(encoding="utf-8"))
    return tuple(doc.get("enum") or ())
