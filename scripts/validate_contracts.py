#!/usr/bin/env python
"""A0 산출물 검증 — contracts/ 5종 + config/ 9종.

REVISED-MASTER-PLAN §6 A0 의 AC 를 기계 검사로 옮긴 것이다.
  - 스키마 self-validate + 예시 픽스처 통과
  - BD 20개 내외가 신구 명칭 매핑과 함께 YAML 로 존재
  - Answer 스키마에 confidence(숫자)·denied_source_count 부재
  - taxonomy 각 항목에 근거 자료 인용 존재(추측 항목 0)

실행:  .venv/bin/python scripts/validate_contracts.py
종료코드: 0 = 전부 통과, 1 = 실패 있음
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
CONFIG = ROOT / "config"

CONTRACT_FILES = [
    "ontology.schema.json",
    "answer.schema.json",
    "source_type.enum.json",
    "state.enum.json",
    "policy.schema.json",
]
CONFIG_FILES = [
    "bd-seed.yaml",
    "aliases.yaml",
    "need-taxonomy.yaml",
    "capability-taxonomy.yaml",
    "claim-verdict-rules.yaml",
    "criticality-rules.yaml",
    "pii-patterns.yaml",
    "tiers.yaml",
    "sources.yaml",
]

REQUIRED_SOURCE_GROUPS = {
    "featuremap",
    "sales_xlsx",
    "bd_registry",
    "documents",
    "slack",
    "repo_seed",
}
GAP_VERDICT_ENUM = ["CONFIRMED", "POSSIBLE", "UNKNOWN"]
FORBIDDEN_ANSWER_PROPS = {"confidence", "confidence_band", "denied_source_count"}

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    return ok


# --------------------------------------------------------------------------
# 로딩
# --------------------------------------------------------------------------
def load_contracts() -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    for fname in CONTRACT_FILES:
        path = CONTRACTS / fname
        if not path.exists():
            check(f"contracts/{fname} 존재", False, "파일 없음")
            continue
        try:
            loaded[fname] = json.loads(path.read_text(encoding="utf-8"))
            check(f"contracts/{fname} 유효 JSON", True)
        except Exception as exc:  # noqa: BLE001
            check(f"contracts/{fname} 유효 JSON", False, str(exc)[:200])
    return loaded


def load_configs() -> dict[str, object]:
    loaded: dict[str, object] = {}
    for fname in CONFIG_FILES:
        path = CONFIG / fname
        if not path.exists():
            check(f"config/{fname} 존재", False, "파일 없음")
            continue
        try:
            loaded[fname] = yaml.safe_load(path.read_text(encoding="utf-8"))
            check(f"config/{fname} 파싱", True)
        except Exception as exc:  # noqa: BLE001
            check(f"config/{fname} 파싱", False, str(exc)[:200])
    return loaded


def build_registry(contracts: dict[str, dict]) -> Registry:
    registry = Registry()
    for fname, doc in contracts.items():
        uri = doc.get("$id") or f"https://aurorasoft.internal/bwm/contracts/{fname}"
        registry = registry.with_resource(uri, Resource.from_contents(doc))
    return registry


# --------------------------------------------------------------------------
# 1. JSON Schema 자체 유효성
# --------------------------------------------------------------------------
def check_schemas(contracts: dict[str, dict]) -> None:
    for fname, doc in contracts.items():
        try:
            Draft202012Validator.check_schema(doc)
            check(f"contracts/{fname} 유효 JSON Schema (Draft 2020-12)", True)
        except Exception as exc:  # noqa: BLE001
            check(f"contracts/{fname} 유효 JSON Schema (Draft 2020-12)", False, str(exc)[:300])


# --------------------------------------------------------------------------
# 2. Answer 계약 금지 필드
# --------------------------------------------------------------------------
def walk_property_names(node: object, found: set[str]) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            found.update(props.keys())
        for key, value in node.items():
            if key == "x-forbidden-fields":  # 문서용 설명 블록은 제외
                continue
            walk_property_names(value, found)
    elif isinstance(node, list):
        for item in node:
            walk_property_names(item, found)


def check_answer_contract(contracts: dict[str, dict]) -> None:
    doc = contracts.get("answer.schema.json")
    if doc is None:
        check("answer.schema.json 금지 필드 부재", False, "스키마 미로딩")
        return

    names: set[str] = set()
    walk_property_names(doc, names)
    hits = sorted(FORBIDDEN_ANSWER_PROPS & names)
    check(
        "answer.schema.json 에 숫자형 confidence·denied_source_count 부재",
        not hits,
        f"발견된 금지 속성: {hits}" if hits else "confidence / confidence_band / denied_source_count 모두 없음",
    )

    check(
        "answer.schema.json 최상위 additionalProperties: false",
        doc.get("additionalProperties") is False,
        f"실제 값: {doc.get('additionalProperties')!r}",
    )

    # 방어 심층: not 가드가 세 필드를 모두 막는지
    guard = doc.get("not", {}).get("anyOf", [])
    guarded = {req for clause in guard for req in clause.get("required", [])}
    check(
        "answer.schema.json 에 금지 필드 not-가드 존재",
        FORBIDDEN_ANSWER_PROPS <= guarded,
        f"가드된 필드: {sorted(guarded)}",
    )

    gaps = doc.get("properties", {}).get("gaps", {}).get("items", {})
    verdict = gaps.get("properties", {}).get("verdict", {}).get("enum")
    check(
        "gaps[].verdict enum == CONFIRMED|POSSIBLE|UNKNOWN",
        verdict == GAP_VERDICT_ENUM,
        f"실제 값: {verdict!r}",
    )

    for field in ("evidence_strength", "unknowns", "raw_signals", "gaps", "subgraph", "citations", "evidence"):
        check(
            f"answer.schema.json 에 {field} 정의됨",
            field in doc.get("properties", {}),
            "",
        )

    ev_item = doc.get("properties", {}).get("evidence", {}).get("items", {})
    ev_required = set(ev_item.get("required", []))
    check(
        "evidence[] 필수 필드 = source_id·locator·snippet·authority_label",
        {"source_id", "locator", "snippet", "authority_label"} <= ev_required,
        f"실제 required: {sorted(ev_required)}",
    )
    snippet_max = ev_item.get("properties", {}).get("snippet", {}).get("maxLength")
    check("evidence[].snippet maxLength == 500", snippet_max == 500, f"실제 값: {snippet_max!r}")

    band = doc.get("properties", {}).get("evidence_strength", {}).get("properties", {}).get("band", {}).get("enum")
    check(
        "evidence_strength.band enum == HIGH|MEDIUM|LOW",
        band == ["HIGH", "MEDIUM", "LOW"],
        f"실제 값: {band!r}",
    )


# --------------------------------------------------------------------------
# 3. 온톨로지 15라벨 / 18관계
# --------------------------------------------------------------------------
def check_ontology(contracts: dict[str, dict]) -> None:
    doc = contracts.get("ontology.schema.json")
    if doc is None:
        check("ontology 15라벨·18관계", False, "스키마 미로딩")
        return

    labels = doc.get("x-labels", {})
    all_labels = list(labels.get("business", [])) + list(labels.get("knowledge", []))
    check("온톨로지 라벨 15개", len(all_labels) == 15, f"실제 {len(all_labels)}개: {all_labels}")

    defs = doc.get("$defs", {})
    missing = [lab for lab in all_labels if lab not in defs]
    check("모든 라벨에 $defs 정의 존재", not missing, f"누락: {missing}" if missing else "")

    rels = doc.get("x-relations", {})
    all_rels = list(rels.get("knowledge_backbone", [])) + list(rels.get("business", []))
    check("온톨로지 관계 18개", len(all_rels) == 18, f"실제 {len(all_rels)}개")

    k_enum = defs.get("edgeKnowledge", {}).get("properties", {}).get("type", {}).get("enum", [])
    b_enum = defs.get("edgeBusiness", {}).get("properties", {}).get("type", {}).get("enum", [])
    check(
        "edge enum 합계가 x-relations 와 일치",
        sorted(k_enum + b_enum) == sorted(all_rels),
        f"edge enum {len(k_enum) + len(b_enum)}개 vs x-relations {len(all_rels)}개",
    )

    claim_ids = defs.get("edgeBusiness", {}).get("properties", {}).get("claim_ids", {})
    check(
        "비즈니스 엣지 claim_ids 필수 + minItems 1",
        "claim_ids" in defs.get("edgeBusiness", {}).get("required", []) and claim_ids.get("minItems") == 1,
        "근거 없는 비즈니스 엣지는 생성 자체가 불가능해야 한다",
    )

    bd = defs.get("BusinessDomain", {}).get("properties", {})
    expected_bd_props = {
        "name", "industry_scope", "target_company", "br_role", "guest_role", "work_desc",
        "market_size_note", "maturity", "channel_type", "guest_control", "bd_status", "partners",
    }
    check(
        "BusinessDomain 이 §3 속성 전체를 담음",
        expected_bd_props <= set(bd.keys()),
        f"누락: {sorted(expected_bd_props - set(bd.keys()))}",
    )


# --------------------------------------------------------------------------
# 4. state / source_type enum
# --------------------------------------------------------------------------
def check_enums(contracts: dict[str, dict]) -> None:
    state = contracts.get("state.enum.json", {})
    status = state.get("$defs", {}).get("status", {}).get("enum", [])
    lane = state.get("$defs", {}).get("lane", {}).get("enum", [])
    check(
        "state.enum status 7종",
        status == ["VERIFIED", "CANDIDATE", "CRITICAL", "UNVERIFIED", "DISPUTED", "SUPERSEDED", "ARCHIVED"],
        f"실제: {status}",
    )
    check("state.enum lane 3종(default|critical|conflict)", lane == ["default", "critical", "conflict"], f"실제: {lane}")
    catalog = state.get("x-status-catalog", {})
    missing_1a = [s for s in status if "used_in_1a" not in catalog.get(s, {})]
    check("모든 status 에 1A 실사용 표시", not missing_1a, f"누락: {missing_1a}" if missing_1a else "")

    st = contracts.get("source_type.enum.json", {})
    values = st.get("enum", [])
    cat = st.get("x-catalog", {})
    check("source_type enum 비어있지 않음", len(values) > 0, f"{len(values)}종")
    missing_cat = [v for v in values if v not in cat]
    check("모든 source_type 에 카탈로그 항목", not missing_cat, f"누락: {missing_cat}" if missing_cat else "")
    missing_sor = [v for v in values if not cat.get(v, {}).get("source_of_record_for")]
    check(
        "모든 source_type 에 '무엇의 정본인가' 기술",
        not missing_sor,
        f"누락: {missing_sor}" if missing_sor else f"{len(values)}종 전부 기술됨",
    )


# --------------------------------------------------------------------------
# 5. BD 시드
# --------------------------------------------------------------------------
def check_bd_seed(configs: dict[str, object]) -> None:
    doc = configs.get("bd-seed.yaml")
    if not isinstance(doc, dict):
        check("bd-seed.yaml BD >= 15", False, "파일 미로딩")
        return
    bds = doc.get("business_domains", [])
    check("bd-seed BD 개수 >= 15", len(bds) >= 15, f"실제 {len(bds)}개")

    no_alias = [b.get("id") for b in bds if "aliases" not in b]
    check(
        "모든 BD 에 aliases 키 존재",
        not no_alias,
        f"누락: {no_alias}" if no_alias else f"{len(bds)}개 전부 존재 (빈 리스트 {sum(1 for b in bds if not b.get('aliases'))}개 — 관측된 표기 변형이 없는 BD)",
    )

    bad_type = [b.get("id") for b in bds if not isinstance(b.get("aliases"), list)]
    check("aliases 는 리스트 타입", not bad_type, f"타입 오류: {bad_type}" if bad_type else "")

    ids = [b.get("id") for b in bds]
    check("BD id 중복 없음", len(ids) == len(set(ids)), "")

    unconfirmed = [b.get("id") for b in bds if b.get("needs_human_confirm")]
    missing_reason = [b.get("id") for b in bds if b.get("needs_human_confirm") and not b.get("confirm_reason")]
    check(
        "needs_human_confirm 인 BD 는 사유 명시",
        not missing_reason,
        f"사유 누락: {missing_reason}" if missing_reason else f"미확정 {len(unconfirmed)}개 전부 사유 있음",
    )

    # 활동일지 업무 도메인 매핑이 8종 전부 다뤄지는지
    entries = doc.get("activity_log_domain_map", {}).get("entries", [])
    raws = {e.get("raw") for e in entries}
    expected = {"보험설계", "자동차금융", "상담", "협업", "상담&협업", "AI챗봇", "FAQ", "법인폰"}
    check(
        "활동일지 업무 도메인 8종 전부 매핑 표에 존재",
        expected <= raws,
        f"누락: {sorted(expected - raws)}" if not expected <= raws else "",
    )


# --------------------------------------------------------------------------
# 6. taxonomy evidence_hint
# --------------------------------------------------------------------------
def check_taxonomy(configs: dict[str, object]) -> None:
    # id 접두사는 ontology.schema.json 의 pattern 과 일치해야 한다
    # (Need.need_id = ^need\., Capability.capability_id = ^cap\.)
    for fname, key, label in (
        ("need-taxonomy.yaml", "needs", "need"),
        ("capability-taxonomy.yaml", "capabilities", "cap"),
    ):
        doc = configs.get(fname)
        if not isinstance(doc, dict):
            check(f"{fname} evidence_hint", False, "파일 미로딩")
            continue
        items = doc.get(key, [])
        check(f"{fname} 항목 수", len(items) > 0, f"{len(items)}개")

        missing = [i.get("id") for i in items if not i.get("evidence_hint")]
        check(
            f"{fname} 전 항목에 evidence_hint 존재",
            not missing,
            f"누락: {missing}" if missing else f"{len(items)}개 전부 존재",
        )
        empty = [i.get("id") for i in items if isinstance(i.get("evidence_hint"), list) and len(i["evidence_hint"]) == 0]
        check(f"{fname} evidence_hint 비어있지 않음", not empty, f"빈 항목: {empty}" if empty else "")

        no_id = [i for i in items if not str(i.get("id", "")).startswith(f"{label}.")]
        check(f"{fname} id 접두사 규칙({label}.)", not no_id, f"위반 {len(no_id)}건" if no_id else "")

        no_def = [i.get("id") for i in items if not i.get("definition")]
        check(f"{fname} 전 항목에 definition 존재", not no_def, f"누락: {no_def}" if no_def else "")

    needs = configs.get("need-taxonomy.yaml", {}).get("needs", [])
    check("need-taxonomy 항목 수 12~25", 12 <= len(needs) <= 25, f"실제 {len(needs)}개")
    no_raw = [n.get("id") for n in needs if not n.get("raw_expressions")]
    check("need 전 항목에 raw_expressions 존재", not no_raw, f"누락: {no_raw}" if no_raw else "")

    caps = configs.get("capability-taxonomy.yaml", {}).get("capabilities", [])
    check("capability-taxonomy 항목 수 15~30", 15 <= len(caps) <= 30, f"실제 {len(caps)}개")

    # Need ↔ Capability 참조 무결성
    need_ids = {n.get("id") for n in needs}
    dangling = sorted(
        {ref for cap in caps for ref in (cap.get("addresses_needs") or []) if ref not in need_ids}
    )
    check("capability.addresses_needs 가 실존 need id 를 가리킴", not dangling, f"미존재 참조: {dangling}" if dangling else "")


# --------------------------------------------------------------------------
# 7. aliases do_not_merge
# --------------------------------------------------------------------------
def check_aliases(configs: dict[str, object]) -> None:
    doc = configs.get("aliases.yaml")
    if not isinstance(doc, dict):
        check("aliases do_not_merge", False, "파일 미로딩")
        return

    variant_to_canonical: dict[str, str] = {}
    for acc in doc.get("accounts", []):
        canonical = acc.get("canonical")
        if not canonical:
            continue
        variant_to_canonical[canonical] = canonical
        for variant in acc.get("variants", []) or []:
            variant_to_canonical[variant] = canonical

    def resolve(name: str) -> str:
        return variant_to_canonical.get(name, name)

    pairs = doc.get("do_not_merge", [])
    check("do_not_merge 항목 존재", len(pairs) > 0, f"{len(pairs)}쌍")

    violations = []
    no_reason = []
    for entry in pairs:
        pair = entry.get("pair", [])
        if len(pair) != 2:
            violations.append((pair, "쌍이 2개가 아님"))
            continue
        left, right = resolve(pair[0]), resolve(pair[1])
        if left == right:
            violations.append((pair, f"둘 다 canonical '{left}' 로 해석됨"))
        if not entry.get("reason"):
            no_reason.append(pair)

    check(
        "do_not_merge 쌍이 서로 다른 canonical 로 해석됨",
        not violations,
        "; ".join(f"{p} -> {m}" for p, m in violations) if violations else f"{len(pairs)}쌍 전부 통과",
    )
    check("do_not_merge 전 항목에 reason 존재", not no_reason, f"누락: {no_reason}" if no_reason else "")

    # 오타 후보가 alias 로 자동 병합되지 않았는지 (B4 차단 케이스)
    queue = {q.get("candidate") for q in doc.get("review_queue_seed", [])}
    leaked = sorted(queue & set(variant_to_canonical.keys()))
    check(
        "review_queue_seed(오타 후보)가 accounts alias 로 새어들지 않음",
        not leaked,
        f"누출: {leaked}" if leaked else "늘봄캐피털 등 오타 후보는 자동 병합 경로에 없음",
    )

    # 마스터플랜이 명시한 차단 케이스가 실제로 들어 있는지
    required_pair = {"마바손해보험", "마바캐피탈"}
    present = any(set(e.get("pair", [])) == required_pair for e in pairs)
    check("마스터플랜 명시 차단 케이스(마바손해보험≠마바캐피탈) 등록됨", present, "")


# --------------------------------------------------------------------------
# 8. sources.yaml
# --------------------------------------------------------------------------
def check_sources(configs: dict[str, object]) -> None:
    doc = configs.get("sources.yaml")
    if not isinstance(doc, dict):
        check("sources.yaml 6종 커버", False, "파일 미로딩")
        return

    sources = doc.get("sources", [])
    groups = {s.get("source_group") for s in sources}
    missing = sorted(REQUIRED_SOURCE_GROUPS - groups)
    check(
        "sources.yaml 이 Gate 1A Source 6종을 전부 포함",
        not missing,
        f"누락: {missing}" if missing else f"등록 그룹 {len(groups)}종: {sorted(groups)}",
    )

    missing_paths = []
    for src in sources:
        for field in ("path", "transcript_path"):
            value = src.get(field)
            if value and not Path(value).exists():
                missing_paths.append(f"{src.get('id')}.{field} = {value}")
    check(
        "sources.yaml 의 모든 경로가 실제 존재",
        not missing_paths,
        "; ".join(missing_paths) if missing_paths else f"{len(sources)}개 소스 전부 존재",
    )

    required_fields = {"id", "source_group", "path", "source_type", "visibility", "sensitivity", "pii_flag", "parser"}
    incomplete = [
        s.get("id") for s in sources if not required_fields <= set(s.keys())
    ]
    check(
        "모든 소스에 필수 등록 필드(source_type·visibility·sensitivity·pii_flag·parser)",
        not incomplete,
        f"미비: {incomplete}" if incomplete else "",
    )

    no_sor = [s.get("id") for s in sources if not s.get("source_of_record_for")]
    check("모든 소스에 source_of_record_for 존재", not no_sor, f"누락: {no_sor}" if no_sor else "")

    ids = [s.get("id") for s in sources]
    check("source id 중복 없음", len(ids) == len(set(ids)), "")

    # source_type 이 contracts enum 안에 있는지
    enum_path = CONTRACTS / "source_type.enum.json"
    if enum_path.exists():
        allowed = set(json.loads(enum_path.read_text(encoding="utf-8")).get("enum", []))
        bad = sorted({s.get("source_type") for s in sources} - allowed)
        check("sources.yaml 의 source_type 이 전부 enum 안에 있음", not bad, f"미등록: {bad}" if bad else "")

    # vision 예외가 명시적으로 표시됐는지
    vision = [s for s in sources if s.get("extractor") == "vision"]
    bad_vision = [s.get("id") for s in vision if not s.get("transcript_path") or not s.get("exception_reason")]
    check(
        "vision 판독본 소스에 transcript_path + exception_reason 명시",
        not bad_vision,
        f"미비: {bad_vision}" if bad_vision else f"vision 소스 {len(vision)}건",
    )

    # PII 가 있는 소스는 restricted 인지
    leaky = [
        s.get("id") for s in sources
        if s.get("pii_flag") and s.get("sensitivity") != "restricted" and s.get("source_group") != "documents"
    ]
    check(
        "pii_flag 인 xlsx/slack 소스는 sensitivity=restricted",
        not leaky,
        f"위반: {leaky}" if leaky else "",
    )


# --------------------------------------------------------------------------
# 9. 예시 픽스처 (A0 AC: 스키마 self-validate + 예시 픽스처 통과)
# --------------------------------------------------------------------------
GRAPH_FIXTURE_OK = {
    "nodes": [
        {
            "label": "BusinessDomain",
            "id": "bd_001",
            "natural_key": "bd.insurance_planning_support",
            "source_ids": ["src_bd_overview"],
            "bd_id": "bd.insurance_planning_support",
            "name": "보험설계지원",
            "aliases": ["보험설계지원 (상세 자료)", "보험설계"],
            "industry_scope": "금융",
            "target_company": "보험사",
            "br_role": "설계매니저",
            "guest_role": "외부 모집사(GA) 설계사",
            "bd_status": "관리",
            "channel_type": "상시",
            "guest_control": "불가",
            "partners": ["파트너A", "파트너B"],
        },
        {
            "label": "Account",
            "id": "acc_001",
            "natural_key": "마바손해보험",
            "source_ids": ["src_pain_registry", "src_sales_activity_log"],
            "canonical_name": "마바손해보험",
            "raw_names": ["마바손해보험"],
            "account_kind": "customer",
        },
        {
            "label": "Need",
            "id": "need_001",
            "natural_key": "need.channel_control_absent",
            "source_ids": ["src_pain_registry"],
            "need_id": "need.channel_control_absent",
            "name": "개인 메신저 업무로 회사가 통제·가시화하지 못함",
            "need_type": "pain",
            "canonical": True,
        },
        {
            "label": "Claim",
            "id": "clm_001",
            "natural_key": "clm_001",
            "source_ids": ["src_pain_registry"],
            "claim_id": "clm_001",
            "statement": "마바손해보험은 본사가 외부 설계사 소통 경로를 통제·관리하지 못하는 문제를 제기했다.",
            "status": "VERIFIED",
            "lane": ["default"],
            "claim_kind": "deal_fact",
            "claim_domain": "customer_need",
            "evidence_ids": ["ev_001"],
        },
        {
            "label": "Evidence",
            "id": "ev_001",
            "natural_key": "sha256(src_pain_registry|pain!Pain Point 목록!E17|...)",
            "source_ids": ["src_pain_registry"],
            "evidence_id": "ev_001",
            "source_id": "src_pain_registry",
            "locator": "pain!Pain Point 목록!E17",
            "snippet": "• 본사가 외부 설계사와의 소통 경로를 통제·관리하지 못함",
            "masked": True,
        },
    ],
    "edges": [
        {
            "type": "HAS_NEED",
            "from": "acc_001",
            "to": "need_001",
            "from_label": "Account",
            "to_label": "Need",
            "claim_ids": ["clm_001"],
            "status": "VERIFIED",
        },
        {
            "type": "SUPPORTS",
            "from": "ev_001",
            "to": "clm_001",
            "from_label": "Evidence",
            "to_label": "Claim",
        },
    ],
}

GRAPH_FIXTURE_BAD_EDGE = {
    "nodes": [],
    "edges": [
        {"type": "HAS_NEED", "from": "acc_001", "to": "need_001"},  # claim_ids 없음 → 거부되어야 함
    ],
}

GRAPH_FIXTURE_BAD_LABEL = {
    "nodes": [
        {"label": "Persona", "id": "p1", "natural_key": "p1", "source_ids": ["s"], "name": "상담사"},
    ],
    "edges": [],
}

ANSWER_FIXTURE_OK = {
    "answer": {
        "text": "가나손해보험은 제안 단계이고 예상매출 2.85억, 성공확률 0.8입니다[1].",
        "estimated_parts": [],
    },
    "citations": [{"marker": 1, "evidence_id": "ev_kb_001"}],
    "evidence": [
        {
            "evidence_id": "ev_kb_001",
            "source_id": "src_sales_activity_log",
            "locator": "활동일지!일일보고(6월 30일)!5.영업 진행 현황!가나손해보험",
            "snippet": "가나손해보험 | 제안 | 2.85억 | 0.8 | 우선협상대상자 선정 핵심 걸림돌(금액 점수 격차) 확인",
            "authority_label": {
                "tier": "T1",
                "claim_domain": "deal_fact",
                "source_type": "sales_activity_log",
                "policy_version": "0.1.0",
            },
            "observed_at": "2026-06-30",
            "extractor": "deterministic",
            "masked": True,
        }
    ],
    "evidence_strength": {
        "band": "MEDIUM",
        "basis": {
            "independent_evidence": 1,
            "highest_authority": "sales_activity_log",
            "contradiction": "none",
            "recency": "current",
        },
    },
    "unknowns": ["제안 결과(수주/실주) 미기록"],
    "raw_signals": [],
    "gaps": [
        {
            "subject": "구독 사업에 필요한 멀티테넌트 구조",
            "verdict": "CONFIRMED",
            "basis": {
                "reason": "하늘IT 검토 문서가 '멀티테넌트 구조 개발 필요'라고 명시한다.",
                "explicit_absence_evidence_ids": ["ev_kyobo_001"],
            },
        }
    ],
    "subgraph": {
        "nodes": [{"id": "acc_kb", "labels": ["Account"], "label_text": "가나손해보험", "rank": "focal"}],
        "edges": [],
        "truncated": False,
    },
    "route": {"retriever": "Q-E", "llm_classifier_used": False, "claim_domain": "deal_fact"},
    "notices": {"results_may_be_incomplete": False, "critical_unverified_included": True},
}

ANSWER_FIXTURE_BAD_CONFIDENCE = dict(ANSWER_FIXTURE_OK)
ANSWER_FIXTURE_BAD_CONFIDENCE = {**ANSWER_FIXTURE_OK, "confidence": 0.87}

ANSWER_FIXTURE_BAD_VERDICT = json.loads(json.dumps(ANSWER_FIXTURE_OK, ensure_ascii=False))
ANSWER_FIXTURE_BAD_VERDICT["gaps"][0]["verdict"] = "LIKELY"


def check_fixtures(contracts: dict[str, dict], registry: Registry) -> None:
    onto = contracts.get("ontology.schema.json")
    ans = contracts.get("answer.schema.json")
    if onto is None or ans is None:
        check("예시 픽스처 검증", False, "스키마 미로딩")
        return

    onto_v = Draft202012Validator(onto, registry=registry)
    ans_v = Draft202012Validator(ans, registry=registry)

    for name, fixture, validator, should_pass in (
        ("정상 그래프 픽스처 통과", GRAPH_FIXTURE_OK, onto_v, True),
        ("claim_ids 없는 비즈니스 엣지 거부", GRAPH_FIXTURE_BAD_EDGE, onto_v, False),
        ("온톨로지 밖 라벨(Persona) 거부", GRAPH_FIXTURE_BAD_LABEL, onto_v, False),
        ("정상 Answer 픽스처 통과", ANSWER_FIXTURE_OK, ans_v, True),
        ("숫자 confidence 가 붙은 Answer 거부", ANSWER_FIXTURE_BAD_CONFIDENCE, ans_v, False),
        ("gaps.verdict=LIKELY 인 Answer 거부", ANSWER_FIXTURE_BAD_VERDICT, ans_v, False),
    ):
        errors = list(validator.iter_errors(fixture))
        ok = (not errors) if should_pass else bool(errors)
        detail = ""
        if should_pass and errors:
            detail = f"예상치 못한 오류: {errors[0].message[:180]}"
        elif not should_pass and not errors:
            detail = "거부되어야 하는데 통과했다"
        check(name, ok, detail)


# --------------------------------------------------------------------------
# 10. claim-verdict / criticality / pii / tiers 최소 계약
# --------------------------------------------------------------------------
def check_rule_configs(configs: dict[str, object], contracts: dict[str, dict]) -> None:
    allowed_types = set(contracts.get("source_type.enum.json", {}).get("enum", []))
    allowed_kinds = set(
        contracts.get("state.enum.json", {}).get("$defs", {}).get("claim_kind", {}).get("enum", [])
    )
    allowed_status = set(contracts.get("state.enum.json", {}).get("$defs", {}).get("status", {}).get("enum", []))
    allowed_domains = set(
        contracts.get("policy.schema.json", {}).get("$defs", {}).get("claimDomain", {}).get("enum", [])
    )

    cvr = configs.get("claim-verdict-rules.yaml")
    if isinstance(cvr, dict):
        rules = cvr.get("rules", [])
        check("claim-verdict-rules 규칙 존재", len(rules) > 0, f"{len(rules)}행")
        bad_type = sorted({r.get("source_type") for r in rules} - allowed_types)
        check("규칙표 source_type 이 enum 안에 있음", not bad_type, f"미등록: {bad_type}" if bad_type else "")
        bad_kind = sorted({r.get("claim_kind") for r in rules} - allowed_kinds)
        check("규칙표 claim_kind 가 enum 안에 있음", not bad_kind, f"미등록: {bad_kind}" if bad_kind else "")
        bad_status = sorted({r.get("default_status") for r in rules if r.get("default_status")} - allowed_status)
        check("규칙표 default_status 가 enum 안에 있음", not bad_status, f"미등록: {bad_status}" if bad_status else "")
        bad_domain = sorted({r.get("claim_domain") for r in rules if r.get("claim_domain")} - allowed_domains)
        check("규칙표 claim_domain 이 5종 안에 있음", not bad_domain, f"미등록: {bad_domain}" if bad_domain else "")
        no_reason = [
            f"{r.get('source_type')}×{r.get('claim_kind')}"
            for r in rules
            if r.get("verified_allowed") and not r.get("reason")
        ]
        check(
            "VERIFIED 허용 규칙에는 반드시 사유가 있음",
            not no_reason,
            f"사유 누락: {no_reason}" if no_reason else "",
        )
        # 마스터플랜이 예시로 못박은 판정
        target = [
            r for r in rules
            if r.get("source_type") == "bd_registry" and r.get("claim_kind") == "market_assessment"
        ]
        check(
            "bd_registry × market_assessment → Observation 확정 / Claim CANDIDATE",
            bool(target)
            and target[0].get("observation_confirmed") is True
            and target[0].get("verified_allowed") is False
            and target[0].get("default_status") == "CANDIDATE",
            f"실제: {target[0] if target else '규칙 없음'}",
        )
        check("blocking_tests 정의됨", len(cvr.get("blocking_tests", [])) >= 5, f"{len(cvr.get('blocking_tests', []))}건")

    crit = configs.get("criticality-rules.yaml")
    if isinstance(crit, dict):
        rules = crit.get("rules", [])
        check("criticality 규칙 존재", len(rules) > 0, f"{len(rules)}개")
        no_basis = [r.get("id") for r in rules if not r.get("basis")]
        check("모든 criticality 규칙에 실측 근거(basis)", not no_basis, f"누락: {no_basis}" if no_basis else "")
        required_ids = {"CR-HIGH-VALUE-DEAL", "CR-LOST-DEAL", "CR-REGULATION-SECURITY", "CR-STRATEGIC-ACCOUNT", "CR-COMPETITOR-REPLACEMENT", "CR-RARE-TECH"}
        have = {r.get("id") for r in rules}
        check(
            "지시 6종(고액딜·Lost·규제보안·전략계정·경쟁대체·1회등장 기술전제) 전부 존재",
            required_ids <= have,
            f"누락: {sorted(required_ids - have)}" if not required_ids <= have else "",
        )

    pii = configs.get("pii-patterns.yaml")
    if isinstance(pii, dict):
        det = {d.get("id"): d for d in pii.get("detectors", [])}
        for needed in ("PII-MOBILE", "PII-LANDLINE", "PII-EMAIL", "PII-RRN", "PII-ACCOUNT-NO"):
            check(f"pii-patterns 에 {needed} 존재", needed in det, "")
        no_mask = [k for k, v in det.items() if not v.get("mask_template")]
        check("모든 detector 에 마스킹 표기법", not no_mask, f"누락: {no_mask}" if no_mask else "")
        # 마스킹 결과가 자기 정규식에 다시 걸리지 않아야 한다
        import re

        relapse = []
        for d in pii.get("detectors", []):
            try:
                pat = re.compile(d["regex"])
            except Exception:  # noqa: BLE001
                relapse.append(f"{d.get('id')}: 정규식 컴파일 실패")
                continue
            for other in pii.get("detectors", []):
                if pat.search(str(other.get("mask_template", ""))):
                    relapse.append(f"{d.get('id')} 가 {other.get('id')} 마스킹 결과에 재매칭")
        check(
            "마스킹 결과가 어떤 detector 정규식에도 재매칭되지 않음 (Gate 1A 항목 5)",
            not relapse,
            "; ".join(relapse) if relapse else "",
        )

    tiers = configs.get("tiers.yaml")
    if isinstance(tiers, dict):
        t = tiers.get("tiers", {})
        check("tiers S = claude-sonnet-5 (하이쿠 금지)", t.get("S", {}).get("model") == "claude-sonnet-5", f"실제: {t.get('S', {}).get('model')!r}")
        check("tiers L = claude-opus-5", t.get("L", {}).get("model") == "claude-opus-5", f"실제: {t.get('L', {}).get('model')!r}")
        check("S tier 실측 단가 0.0668 USD/call 기록(sonnet)", t.get("S", {}).get("measured_cost_per_call_usd") == 0.0668, f"실제: {t.get('S', {}).get('measured_cost_per_call_usd')!r}")
        provs = tiers.get("providers", {})
        available = {p.get("id") for p in provs.get("available", [])}
        check("provider = claude_cli(기본)|anthropic|fixture", available == {"claude_cli", "anthropic", "fixture"}, f"실제: {sorted(available)}")
        check("기본 provider 는 claude_cli", provs.get("default") == "claude_cli", f"실제: {provs.get('default')!r}")


# --------------------------------------------------------------------------
def main() -> int:
    print("=" * 78)
    print("A0 계약·시드 검증  (contracts/ 5종 + config/ 9종)")
    print("=" * 78)

    contracts = load_contracts()
    configs = load_configs()
    check_schemas(contracts)
    registry = build_registry(contracts)

    check_answer_contract(contracts)
    check_ontology(contracts)
    check_enums(contracts)
    check_bd_seed(configs)
    check_taxonomy(configs)
    check_aliases(configs)
    check_sources(configs)
    check_rule_configs(configs, contracts)
    check_fixtures(contracts, registry)

    passed = sum(1 for ok, _, _ in results if ok)
    failed = [(name, detail) for ok, name, detail in results if not ok]

    for ok, name, detail in results:
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {name}"
        if detail:
            line += f"  — {detail}"
        print(line)

    print("-" * 78)
    print(f"통과 {passed} / 전체 {len(results)}   실패 {len(failed)}")
    if failed:
        print("\n실패 항목:")
        for name, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    print("전부 통과.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
