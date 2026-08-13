"""③ 결정적 직행 적재.

`structured.record_kind` 가 있는 레코드는 LLM 없이 바로 노드·엣지가 된다.
여기서 만드는 모든 Claim 은 `verdict.VerdictEngine` 의 판정을 그대로 받고,
모든 비즈니스 엣지는 그 Claim 의 id 를 달고 나간다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .model import GraphBatch, NodeRef, sha
from .resolve import Resolver, clean_display, match_key, text_key
from .settings import Settings
from .verdict import CriticalityEngine, VerdictEngine

_SPACE = re.compile(r"\s+")
_AMOUNT_PLAIN = re.compile(r"^([0-9]{6,})$")
# 변경 이력을 한 칸에 담은 표기를 가른다: '4억 -> 3.44억'.
_AMOUNT_REVISION = re.compile(r"->|→|=>")
# 금액 뒤에 붙는 단서와 어림 표현. 값 자체가 아니라 곁말이다.
_AMOUNT_PAREN = re.compile(r"\([^)]*\)\s*$")
_AMOUNT_HEDGE = re.compile(r"(?:내외|정도|규모|수준)$")
_AMOUNT_TOKEN = re.compile(r"^(?:약|총)?([0-9][0-9,]*(?:\.[0-9]+)?)(억원|억|천만원|백만원|만원|원)$")
EOK = 100_000_000
_AMOUNT_UNIT_SCALE = {
    "억원": EOK,
    "억": EOK,
    "천만원": 10_000_000,
    "백만원": 1_000_000,
    "만원": 10_000,
    "원": 1,
}
# '원' 단위에만 걸는 최소 자릿수. 맨 숫자 규칙(6자리 이상)과 같은 기준이다.
_WON_MIN_DIGITS = 6


def flatten(text: Any) -> str:
    return _SPACE.sub(" ", str(text or "")).strip()


# ---------------------------------------------------------------------------
# 컨텍스트
# ---------------------------------------------------------------------------


@dataclass
class LoadContext:
    settings: Settings
    resolver: Resolver
    verdicts: VerdictEngine
    criticality: CriticalityEngine
    batch: GraphBatch
    source_type: dict[str, str] = field(default_factory=dict)
    source_ref: dict[str, NodeRef] = field(default_factory=dict)
    feature_capability_index: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    counters: Counter = field(default_factory=Counter)
    subject_values: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    subject_claims: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    claim_key_by_id: dict[str, str] = field(default_factory=dict)
    # LLM 이 taxonomy 항목으로 이어 준 Need. evidence_id → Need 노드.
    llm_need_mapped: dict[str, NodeRef] = field(default_factory=dict)
    # 문서·미팅 추출이 찾았지만 사전에 없던 니즈 표현. 2차 매핑을 기다린다.
    llm_need_pending: list[dict[str, Any]] = field(default_factory=list)
    # 딜별 금액 후보(인용 포함). 사람이 귀속을 눈으로 판정하는 자리다. 그래프에는 안 들어간다.
    amount_candidates: dict[Any, list[dict[str, Any]]] = field(default_factory=dict)

    def type_of(self, source_id: str) -> str:
        return self.source_type.get(source_id, "internal_analysis")


# ---------------------------------------------------------------------------
# 공용 emit
# ---------------------------------------------------------------------------


def add_evidence(ctx: LoadContext, record: dict[str, Any]) -> NodeRef:
    """② Evidence + FROM_SOURCE. 원문 본문은 넣지 않는다 — 발췌만 들어간다."""
    source_id = record["source_id"]
    natural_key = sha(source_id, record["locator"], record["excerpt_hash"])
    ref = ctx.batch.node(
        "Evidence",
        natural_key,
        evidence_id=record["evidence_id"],
        source_id=source_id,
        locator=record["locator"],
        snippet=record["excerpt"][:500],
        snippet_hash=record["excerpt_hash"],
        authored_at=record.get("authored_at"),
        masked=bool(record.get("pii_masked")),
        source_ids=[source_id],
    )
    source_ref = ctx.source_ref.get(source_id)
    if source_ref is not None:
        ctx.batch.knowledge_edge("FROM_SOURCE", ref, source_ref)
    return ref


def add_observation(
    ctx: LoadContext,
    *,
    record: dict[str, Any],
    evidence_ref: NodeRef,
    statement: str,
    mentions: Iterable[NodeRef] = (),
    event: NodeRef | None = None,
    extractor: str = "deterministic",
    criticality: Iterable[str] = (),
) -> NodeRef:
    statement = flatten(statement)[:900]
    natural_key = sha(record["evidence_id"], text_key(statement))
    ref = ctx.batch.node(
        "Observation",
        natural_key,
        observation_id="obs_" + natural_key[:24],
        statement=statement,
        evidence_ids=[record["evidence_id"]],
        extractor=extractor,
        criticality=list(criticality) or None,
        source_ids=[record["source_id"]],
        observed_at=record.get("authored_at"),
    )
    ctx.batch.knowledge_edge("DERIVED_FROM", ref, evidence_ref)
    if event is not None:
        ctx.batch.knowledge_edge("OBSERVED_IN", ref, event)
    for target in mentions:
        ctx.batch.knowledge_edge("MENTIONS", ref, target)
    ctx.counters["observations"] += 1
    return ref


def add_claim(
    ctx: LoadContext,
    *,
    record: dict[str, Any],
    evidence_ref: NodeRef,
    statement: str,
    claim_kind: str,
    subject_key: str,
    subject_value: str,
    about: NodeRef | None = None,
    fields: dict[str, Any] | None = None,
    criticality_text: str | None = None,
    extractor: str = "deterministic",
) -> tuple[str, NodeRef]:
    """⑧ 상태 판정 3단을 거쳐 Claim 을 만든다. 반환값은 (claim_id, 노드 참조)."""
    source_type = ctx.type_of(record["source_id"])
    statement = flatten(statement)[:900]
    text = criticality_text if criticality_text is not None else record["excerpt"]
    fired = ctx.criticality.evaluate(
        applies_to="Claim",
        text=f"{text}\n{statement}",
        fields={**(fields or {}), "subject_key": subject_key, "statement": statement,
                "observed_at": record.get("authored_at")},
    )
    lanes_from_rules = ctx.criticality.lanes_for(fired)
    critical_fired = [rid for rid in fired if "critical" in ctx.criticality.lanes_for([rid])]

    verdict = ctx.verdicts.decide(
        source_type=source_type,
        claim_kind=claim_kind,
        extractor=extractor,
        criticality_fired=critical_fired,
        transcription_flags=record.get("transcription_flags") or (),
    )
    lanes = list(verdict.lane)
    for lane in lanes_from_rules:
        if lane not in lanes:
            lanes.append(lane)

    natural_key = sha(subject_key, text_key(statement))
    claim_id = "clm_" + natural_key[:24]
    ref = ctx.batch.node(
        "Claim",
        natural_key,
        claim_id=claim_id,
        statement=statement,
        status=verdict.status,
        lane=lanes,
        claim_kind=claim_kind,
        claim_domain=verdict.claim_domain,
        evidence_ids=[record["evidence_id"]],
        source_ids=[record["source_id"]],
        observed_at=record.get("authored_at"),
    )
    ctx.batch.knowledge_edge("SUPPORTS", evidence_ref, ref)
    if about is not None:
        ctx.batch.knowledge_edge("ABOUT", ref, about)

    ctx.subject_values[subject_key].add(text_key(subject_value))
    ctx.subject_claims[subject_key].add(natural_key)
    ctx.claim_key_by_id[claim_id] = natural_key
    ctx.counters[f"claim:{verdict.status}"] += 1
    return claim_id, ref


def finalize_conflicts(ctx: LoadContext) -> int:
    """같은 subject-key 에 서로 다른 값이 확정되면 lane 에 conflict 를 단다(1A 는 측정만)."""
    marked = 0
    for subject_key, values in ctx.subject_values.items():
        if len(values) < 2:
            continue
        for claim_key in ctx.subject_claims[subject_key]:
            node = ctx.batch.find_node("Claim", claim_key)
            if node is None:
                continue
            lanes = list(node.props.get("lane") or [])
            if "conflict" not in lanes:
                lanes.append("conflict")
                node.props["lane"] = lanes
                marked += 1
    return marked


# ---------------------------------------------------------------------------
# 엔티티 헬퍼
# ---------------------------------------------------------------------------


def account_node(
    ctx: LoadContext, raw: str, *, source_id: str, kind: str | None = None, from_title: bool = False
) -> NodeRef | None:
    ref_info = ctx.resolver.resolve_account(raw, from_title=from_title)
    if ref_info is None or ref_info.excluded:
        return None
    props: dict[str, Any] = {
        "canonical_name": ref_info.canonical_name,
        "raw_names": [ref_info.raw_name],
        "source_ids": [source_id],
    }
    if kind:
        props["account_kind"] = kind
    return ctx.batch.node("Account", ref_info.canonical_name, **props)


def split_account_and_scope(ctx: LoadContext, raw: str) -> tuple[str, str]:
    """'다라카드 자동차' 는 Account 3개가 아니라 Account 1개 + Deal 2개다.

    분리는 접두가 alias 사전의 canonical 과 **정확히** 일치할 때만 한다(유사도 분리 금지).
    """
    value = flatten(raw)
    if " " not in value:
        return value, "기본"
    head, tail = value.rsplit(" ", 1)
    resolved = ctx.resolver.resolve_account(head)
    if resolved is not None and resolved.matched_alias:
        return head, tail
    return value, "기본"


def industry_node(ctx: LoadContext, name: str, source_id: str) -> NodeRef | None:
    name = flatten(name)
    if not name:
        return None
    return ctx.batch.node(
        "Industry", match_key(name) or name, name=name, source_ids=[source_id]
    )


# BD 레지스트리의 타겟기업 값은 한 셀에 여러 산업이 들어온다: '제조사, 물류' · '보험사/GA'.
_TARGET_SPLIT = re.compile(r"\s*[,/]\s*")
# 괄호는 두 방향으로 쓰인다. 앞에 오면 한정어('(손해)보험사' → 보험사),
# 뒤에 오면 일반 산업명을 담는다('가전(제조사)' → 제조사).
_LEADING_PAREN = re.compile(r"^[\(（]([^)）]+)[\)）]\s*(.+)$")
_TRAILING_PAREN = re.compile(r"^(.+?)\s*[\(（]([^)）]+)[\)）]$")
# 특정 산업이 아니라 범위를 말하는 값. 여기에 엣지를 걸면 근거 없는 엣지가 된다.
_TARGET_NOT_AN_INDUSTRY = frozenset(
    {"광범위", "전산업", "전업권", "공통", "미정", "없음", "해당없음", "na", "n/a", "-"}
)


def industry_names_from_target_company(raw: str) -> tuple[list[str], list[str]]:
    """타겟기업 셀을 Industry 이름 목록과 매핑 불가 목록으로 나눈다."""
    names: list[str] = []
    rejected: list[str] = []
    for part in _TARGET_SPLIT.split(flatten(raw)):
        part = part.strip()
        if not part:
            continue
        lead = _LEADING_PAREN.match(part)
        if lead:
            name = lead.group(2).strip()
        else:
            trail = _TRAILING_PAREN.match(part)
            if trail:
                inner = trail.group(2).strip()
                name = inner if inner.endswith("사") else trail.group(1).strip()
            else:
                name = part
        if not name:
            continue
        if match_key(name) in _TARGET_NOT_AN_INDUSTRY:
            rejected.append(part)
        elif name not in names:
            names.append(name)
    return names, rejected


def load_bd_target_industries(
    ctx: LoadContext,
    *,
    record: dict[str, Any],
    evidence_ref: NodeRef,
    bd_id: str,
    bd_ref: NodeRef,
    raw_target_company: str,
) -> None:
    """BD 레지스트리의 타겟기업 값에서 `(BusinessDomain)-[:TARGETS]->(Industry)` 를 만든다.

    멀티BM 시트(`bd_targets`)는 금융권만 담고 있어 비금융 BD 12개가 고아로 남았다.
    타겟기업은 20개 BD 전부에 들어 있으므로 이쪽에서도 같은 엣지를 만든다.
    산업으로 읽을 수 없는 값('광범위' 등)은 엣지를 만들지 않고 카운터에만 남긴다.
    """
    names, rejected = industry_names_from_target_company(raw_target_company)
    for unmapped in rejected:
        ctx.counters[f"bd_targets_unmapped_name:{unmapped}"] += 1
    if not names:
        return
    seed = ctx.settings.bd_index[bd_id]
    for name in names:
        industry_ref = industry_node(ctx, name, record["source_id"])
        if industry_ref is None:
            continue
        industry_name = ctx.batch.find_node(*industry_ref).props["name"]
        claim_id, _ = add_claim(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=f"'{seed['name']}' BD 는 {industry_name} 를 타겟 산업으로 삼는다.",
            claim_kind="bd_registry_fact",
            subject_key=f"bd.targets::{bd_id}::{industry_name}",
            subject_value=industry_name,
            about=bd_ref,
        )
        ctx.batch.business_edge("TARGETS", bd_ref, industry_ref, claim_ids=[claim_id])
        ctx.counters["bd_targets_from_target_company"] += 1


def bd_node(ctx: LoadContext, bd_id: str, source_id: str) -> NodeRef | None:
    seed = ctx.settings.bd_index.get(bd_id)
    if seed is None:
        return None
    props = {
        "bd_id": seed["id"],
        "name": seed["name"],
        "aliases": seed.get("aliases") or None,
        "industry_scope": seed["industry_scope"],
        "target_company": seed.get("target_company"),
        "br_role": seed.get("br_role"),
        "guest_role": seed.get("guest_role"),
        "work_desc": seed.get("work_desc"),
        "market_size_note": seed.get("market_size_note"),
        "target_company_detail": seed.get("target_company_detail"),
        "maturity": seed.get("maturity"),
        "channel_type": seed.get("channel_type"),
        "guest_control": seed.get("guest_control"),
        "bd_status": seed.get("bd_status"),
        "expected_effects": seed.get("expected_effects") or None,
        "partners": seed.get("partners") or None,
        "needs_human_confirm": seed.get("needs_human_confirm"),
        "confirm_reason": seed.get("confirm_reason"),
        "source_ids": [source_id],
    }
    return ctx.batch.node("BusinessDomain", seed["id"], **props)


def need_node(ctx: LoadContext, mapping, record: dict[str, Any]) -> NodeRef:
    if mapping.canonical and mapping.need_id:
        return ctx.batch.node(
            "Need",
            mapping.need_id,
            need_id=mapping.need_id,
            name=mapping.name,
            definition=(ctx.resolver.need_by_id(mapping.need_id) or {}).get("definition"),
            need_type=mapping.need_type,
            canonical=True,
            source_ids=[record["source_id"]],
        )
    # 사전에 안 걸린 표현은 신규 canonical 을 만들지 않는다. 원문만 붙여 따로 세워 둔다.
    natural_key = sha(record["evidence_id"], text_key(mapping.unmapped_raw))
    return ctx.batch.node(
        "Need",
        natural_key,
        need_type=mapping.need_type,
        canonical=False,
        unmapped_raw=mapping.unmapped_raw[:500],
        source_ids=[record["source_id"]],
    )


def capability_node(ctx: LoadContext, capability_id: str, source_id: str) -> NodeRef | None:
    seed = ctx.settings.capability_index.get(capability_id)
    if seed is None:
        return None
    return ctx.batch.node(
        "Capability",
        seed["id"],
        capability_id=seed["id"],
        name=seed["name"],
        definition=seed.get("definition"),
        addon=seed.get("addon"),
        license_gated=seed.get("license_gated"),
        source_ids=[source_id],
    )


def product_nodes(ctx: LoadContext, text: str, source_id: str) -> list[NodeRef]:
    refs = []
    for entry in ctx.resolver.products_in(text):
        refs.append(
            ctx.batch.node(
                "Product",
                match_key(entry["canonical"]),
                name=entry["canonical"],
                aliases=entry.get("variants") or None,
                generation=entry.get("generation"),
                source_ids=[source_id],
            )
        )
    return refs


def event_node(
    ctx: LoadContext,
    record: dict[str, Any],
    *,
    event_type: str,
    title: str,
    mode: str | None = None,
    next_actions: list[str] | None = None,
) -> NodeRef:
    title = flatten(title)[:300] or event_type
    natural_key = sha(record["source_id"], record["locator"], text_key(title))
    return ctx.batch.node(
        "Event",
        natural_key,
        event_type=event_type,
        title=title,
        mode=mode,
        next_actions=[flatten(a)[:300] for a in (next_actions or [])] or None,
        source_ids=[record["source_id"]],
        observed_at=record.get("authored_at"),
    )


def deal_node(
    ctx: LoadContext,
    *,
    account: str,
    scope: str,
    source_id: str,
    **props: Any,
) -> NodeRef:
    natural_key = f"{match_key(account)}|{match_key(scope) or 'base'}"
    return ctx.batch.node(
        "Deal",
        natural_key,
        account_canonical=account,
        deal_scope=scope,
        source_ids=[source_id],
        **props,
    )


def parse_amount(raw: Any) -> tuple[str | None, float | None]:
    """금액 원문과 정규화값. 애매하면 정규화하지 않는다(오판보다 미상이 낫다).

    단위를 생략한 맨 숫자('0.5'·'0.8')는 억 단위를 붙여 추정하지 않는다. 6자리 이상이면
    원으로 읽고 그보다 짧으면 미상으로 둔다. 정규화에 실패한 값은 조용히 사라지지 않고
    CR-AMOUNT-UNPARSEABLE 로 잡힌다.
    """
    text = flatten(raw)
    if not text or text in {"-", "0"}:
        return (text or None), None
    return text, _normalize_amount(text)


def _normalize_amount(text: str) -> float | None:
    # 한 칸에 변경 이력이 들어오면 마지막 값이 최신이다. 원문은 amount_raw 에 그대로 남는다.
    candidate = _AMOUNT_REVISION.split(text)[-1]
    candidate = _AMOUNT_PAREN.sub("", candidate).replace(" ", "").replace(" ", "")
    # 대화에서 오는 금액은 어림수다('1억 내외'). 어림 표현을 떼고 값을 읽는다.
    # 범위('2.5~3억')와 조건('5천만원 이하')은 여기서 떼지 않는다 — 어느 값인지 고를 근거가
    # 없어서 고르면 어느 자료에도 없는 숫자가 된다. 아래 정규식이 그대로 거부한다.
    candidate = _AMOUNT_HEDGE.sub("", candidate)
    match = _AMOUNT_TOKEN.match(candidate)
    if match:
        digits = match.group(1).replace(",", "")
        unit = match.group(2)
        if unit == "원" and len(digits.split(".")[0]) < _WON_MIN_DIGITS:
            return None
        return float(digits) * _AMOUNT_UNIT_SCALE[unit]
    match = _AMOUNT_PLAIN.match(candidate.replace(",", ""))
    if match:
        return float(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Feature → Capability 색인
# ---------------------------------------------------------------------------

_HINT = re.compile(r"기능맵!v2\.1\s*(화면|영역|상세분류|기능명)\s*((?:['‘][^'’]+['’][·,\s]*)+)")
_QUOTED = re.compile(r"['‘]([^'’]+)['’]")
_LEVELS = ("기능명", "상세분류", "영역", "화면")


def build_feature_capability_index(settings: Settings) -> dict[str, dict[str, list[str]]]:
    """capability-taxonomy 의 evidence_hint 에 적힌 기능맵 좌표로 색인을 만든다.

    taxonomy 는 사람이 확정한 A0 산출물이고 각 항목에 근거 인용이 붙어 있다. 여기서는
    그 인용에 적힌 화면·영역·상세분류·기능명만 읽고 추측을 더하지 않는다.
    """
    index: dict[str, dict[str, list[str]]] = {level: defaultdict(list) for level in _LEVELS}
    for capability in settings.capability_taxonomy["capabilities"]:
        for hint in capability.get("evidence_hint") or []:
            for level, quoted in _HINT.findall(hint):
                for name in _QUOTED.findall(quoted):
                    bucket = index[level][text_key(name)]
                    if capability["id"] not in bucket:
                        bucket.append(capability["id"])
    return {level: dict(values) for level, values in index.items()}


def capabilities_for_feature(
    index: dict[str, dict[str, list[str]]], structured: dict[str, Any]
) -> list[str]:
    """가장 구체적인 단계에서 걸린 것만 쓴다(기능명 > 상세분류 > 영역 > 화면)."""
    by_level = {
        "기능명": structured.get("기능명"),
        "상세분류": structured.get("상세분류"),
        "영역": structured.get("중분류"),
        "화면": structured.get("대분류"),
    }
    for level in _LEVELS:
        value = by_level.get(level)
        if not value:
            continue
        hit = index.get(level, {}).get(text_key(value))
        if hit:
            return list(hit)
    return []


# ---------------------------------------------------------------------------
# record_kind 별 처리
# ---------------------------------------------------------------------------


def load_feature_row(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    name = flatten(s.get("기능명"))
    if not name:
        ctx.counters["feature_row_without_name"] += 1
        return
    screen = flatten(s.get("대분류"))
    area = flatten(s.get("중분류"))
    sub_area = flatten(s.get("상세분류"))
    centers = s.get("센터구분") or []
    natural_key = "|".join(match_key(v) for v in (screen, area, sub_area, name))

    feature = ctx.batch.node(
        "Feature",
        natural_key,
        name=name,
        description=flatten(s.get("설명")) or None,
        screen=screen,
        area=area or None,
        sub_area=sub_area or None,
        livetalk_supported="LiveTalk" in centers,
        grouptalk_supported="GroupTalk" in centers,
        introduced_in=s.get("introduced_in") or "unknown",
        available_versions=s.get("available_versions") or None,
        note=flatten(s.get("비고")) or None,
        merged_description_rows=len(s.get("설명_병합행") or []) or None,
        source_ids=[record["source_id"]],
    )

    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=f"기능맵 {record['locator']} 에 '{name}' 기능이 화면 '{screen}' · 영역 '{area}' 로 기재되어 있다.",
        mentions=[feature],
    )

    introduced = s.get("introduced_in")
    if introduced and introduced != "unknown":
        add_claim(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=f"'{name}' 기능({screen})은 {introduced} 버전부터 제공된다.",
            claim_kind="product_spec",
            subject_key=f"feature.introduced_in::{natural_key}",
            subject_value=introduced,
            about=feature,
        )

    for capability_id in capabilities_for_feature(ctx.feature_capability_index, s):
        capability = capability_node(ctx, capability_id, record["source_id"])
        if capability is None:
            continue
        seed = ctx.settings.capability_index[capability_id]
        claim_id, _ = add_claim(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=(
                f"'{name}' 기능은 역량 '{seed['name']}' 을 구현한다"
                f"(capability taxonomy 의 기능맵 좌표 기준)."
            ),
            claim_kind="product_spec",
            subject_key=f"feature.implements::{natural_key}::{capability_id}",
            subject_value=capability_id,
            about=feature,
        )
        ctx.batch.business_edge("IMPLEMENTS", feature, capability, claim_ids=[claim_id])


def load_bd_row(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    bd_id = ctx.resolver.bd_by_name(flatten(s.get("BD")))
    if bd_id is None:
        ctx.counters["bd_row_unmapped"] += 1
        return
    bd = bd_node(ctx, bd_id, record["source_id"])
    if bd is None:
        return
    seed = ctx.settings.bd_index[bd_id]
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"BD Overview {record['locator']} 가 '{seed['name']}' 를 {s.get('산업')} 구분의 BD 로 두고 "
            f"타겟기업 {s.get('타겟기업')}, BR {s.get('BR')}, Guest {s.get('Guest')} 로 적었다."
        ),
        mentions=[bd],
    )
    add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"'{seed['name']}' BD 의 산업 구분은 {s.get('산업')}, 타겟기업은 {s.get('타겟기업')}, "
            f"BR 은 {s.get('BR')}, Guest 는 {s.get('Guest')} 다."
        ),
        claim_kind="bd_registry_fact",
        subject_key=f"bd.definition::{bd_id}",
        subject_value=f"{s.get('산업')}|{s.get('타겟기업')}|{s.get('BR')}|{s.get('Guest')}",
        about=bd,
    )
    load_bd_target_industries(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        bd_id=bd_id,
        bd_ref=bd,
        raw_target_company=flatten(seed.get("target_company")),
    )


def load_bd_market(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    bd_id = ctx.resolver.bd_by_name(flatten(s.get("BD")))
    if bd_id is None:
        ctx.counters["bd_market_unmapped"] += 1
        return
    bd = bd_node(ctx, bd_id, record["source_id"])
    if bd is None:
        return
    seed = ctx.settings.bd_index[bd_id]
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"BD Overview {record['locator']} 가 '{seed['name']}' 의 시장 사이즈를 "
            f"'{s.get('시장사이즈')}', 타겟기업 규모를 '{s.get('타겟기업')}' 로 적었다."
        ),
        mentions=[bd],
    )
    # 시장성 평가는 원문에 적혀 있어도 검증된 사실이 아니다 → CANDIDATE (BT-1).
    add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"'{seed['name']}' 의 시장 규모는 {s.get('시장사이즈')} 수준으로 평가된다."
        ),
        claim_kind="market_assessment",
        subject_key=f"bd.market_size::{bd_id}",
        subject_value=flatten(s.get("시장사이즈")),
        about=bd,
    )
    # BM 정의 시트에 행이 없는 BD 6개(제조·식자재유통·물류·렌털·프랜차이즈·플랫폼)는
    # 이 시트가 타겟기업을 적는 유일한 자리다.
    load_bd_target_industries(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        bd_id=bd_id,
        bd_ref=bd,
        raw_target_company=flatten(seed.get("target_company")),
    )


def load_bd_maturity(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    bd_id = ctx.resolver.bd_by_name(flatten(s.get("BD")))
    if bd_id is None:
        ctx.counters["bd_maturity_unmapped"] += 1
        return
    bd = bd_node(ctx, bd_id, record["source_id"])
    if bd is None:
        return
    seed = ctx.settings.bd_index[bd_id]
    maturity = s.get("maturity") or {}
    done = [axis for axis, value in maturity.items() if value == "✔"]
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"BD Overview 진척도 {record['locator']} 가 '{seed['name']}' 의 완료 축을 "
            f"{', '.join(done) or '없음'} 로, 파트너를 {', '.join(s.get('파트너') or []) or '없음'} 로 적었다."
        ),
        mentions=[bd],
    )
    add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"'{seed['name']}' BD 의 진척 상태는 {', '.join(done) or '완료 표기 없음'} 이다."
        ),
        claim_kind="bd_registry_fact",
        subject_key=f"bd.maturity::{bd_id}",
        subject_value="|".join(sorted(done)),
        about=bd,
    )


def load_bd_targets(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    locator = record["locator"]
    seed_edge = next(
        (
            edge
            for edge in ctx.settings.bd_seed["targets_edges"]
            if edge["locator"].endswith(locator)
        ),
        None,
    )
    if seed_edge is None:
        ctx.counters["bd_targets_unmapped"] += 1
        return

    industries = [part.strip() for part in flatten(s.get("산업")).split("/") if part.strip()]
    mentions: list[NodeRef] = []
    industry_refs = [industry_node(ctx, name, record["source_id"]) for name in industries]
    industry_refs = [ref for ref in industry_refs if ref]
    mentions.extend(industry_refs)

    bd_refs: list[tuple[str, NodeRef]] = []
    for bd_id in seed_edge.get("bd_ids") or []:
        ref = bd_node(ctx, bd_id, record["source_id"])
        if ref is not None:
            bd_refs.append((bd_id, ref))
            mentions.append(ref)
    for unmapped in seed_edge.get("unmapped") or []:
        ctx.counters[f"bd_targets_unmapped_name:{unmapped}"] += 1

    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"BD Overview 멀티BM {locator} 가 산업 '{s.get('산업')}' 에 "
            f"BD {', '.join(s.get('BD목록') or [])} 를 대응시켰다."
        ),
        mentions=mentions,
    )
    for bd_id, bd_ref in bd_refs:
        seed = ctx.settings.bd_index[bd_id]
        for industry_ref in industry_refs:
            industry_name = ctx.batch.find_node(*industry_ref).props["name"]
            claim_id, _ = add_claim(
                ctx,
                record=record,
                evidence_ref=evidence_ref,
                statement=f"'{seed['name']}' BD 는 {industry_name} 를 타겟 산업으로 삼는다.",
                claim_kind="bd_registry_fact",
                subject_key=f"bd.targets::{bd_id}::{industry_name}",
                subject_value=industry_name,
                about=bd_ref,
            )
            ctx.batch.business_edge("TARGETS", bd_ref, industry_ref, claim_ids=[claim_id])


def load_activity_row(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    account = account_node(ctx, flatten(s.get("고객사")), source_id=record["source_id"])
    contact_mode = flatten(s.get("접촉방식")) or None
    title = flatten(s.get("미팅내용")) or f"{flatten(s.get('고객사'))} 접촉"
    event = event_node(
        ctx,
        record,
        event_type="meeting" if contact_mode == "미팅" else "contact",
        title=title,
        mode=contact_mode,
    )
    mentions = [ref for ref in (account, event) if ref and ref[0] != "Event"]
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"활동일지 {record['locator']} 에 {s.get('일자')} {s.get('고객사')} 와의 "
            f"{contact_mode or '접촉'} 기록이 있다: {title}"
        ),
        mentions=mentions,
        event=event,
    )
    if account is None:
        return
    canonical = ctx.batch.find_node(*account).props["canonical_name"]
    add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"{s.get('일자')} 에 {canonical} 와 {contact_mode or '접촉'} 으로 만나 "
            f"'{title}' 을 진행했다."
        ),
        claim_kind="deal_fact",
        subject_key=f"activity::{canonical}::{record['locator']}",
        subject_value=title,
        about=account,
        fields={"account_canonical": canonical},
    )


def load_deal_row(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    raw_account = flatten(s.get("고객사"))
    if not raw_account:
        ctx.counters["deal_row_without_account"] += 1
        return
    account_name, scope = split_account_and_scope(ctx, raw_account)
    account = account_node(
        ctx,
        account_name,
        source_id=record["source_id"],
        kind="customer" if flatten(s.get("단계")) == "운영고객" else None,
    )
    if account is None:
        return
    canonical = ctx.batch.find_node(*account).props["canonical_name"]

    stage_system = {
        "사람별": "activity_person_sheet",
        "일일보고_영업진행": "daily_report_section5",
        "일일보고_계약": "daily_report_section5",
    }.get(flatten(s.get("스키마")), "activity_person_sheet")
    amount_raw, amount_krw = parse_amount(s.get("예상매출") or s.get("금액") or s.get("예상_매출_금액"))
    stage_value = flatten(s.get("단계")) or None
    current_issue = flatten(s.get("현재_이슈") or s.get("현재이슈")) or None
    outcome = (
        "lost"
        if stage_value in {"실패", "실패마감처리"}
        else "won"
        if stage_value in {"수주고객", "운영고객"}
        else "open"
    )
    probability = s.get("성공확률") or s.get("수주_가능성")
    try:
        probability = float(probability) if probability not in (None, "") else None
    except (TypeError, ValueError):
        probability = None

    deal = deal_node(
        ctx,
        account=canonical,
        scope=scope,
        source_id=record["source_id"],
        stage_system=stage_system,
        stage_value=stage_value,
        outcome=outcome,
        amount_raw=amount_raw,
        amount_krw=amount_krw,
        success_probability=probability,
        current_issue=current_issue,
        next_action=flatten(s.get("Next_Action") or s.get("next_action")) or None,
        support_needed=flatten(s.get("지원_필요") or s.get("내부지원사항")) or None,
        observed_at=record.get("authored_at"),
    )

    industry = industry_node(ctx, flatten(s.get("업권")), record["source_id"])
    mentions = [ref for ref in (account, deal, industry) if ref]
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"활동일지 {record['locator']} 가 {raw_account} 딜을 '{stage_value or '단계 미기재'}' 로 적었다."
            + (f" 예상매출 {amount_raw}." if amount_raw else "")
            + (f" 현재 이슈: {current_issue}." if current_issue else "")
        ),
        mentions=mentions,
    )

    crit_fields = {
        "account_canonical": canonical,
        "amount_krw": amount_krw,
        "amount_raw": amount_raw,
        "outcome": outcome,
        "stage_value": stage_value,
        "current_issue": current_issue,
    }
    deal_claim, _ = add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"{canonical}"
            + (f" ({scope})" if scope != "기본" else "")
            + f" 딜의 단계는 '{stage_value or '미기재'}' 다."
            + (f" 예상매출은 {amount_raw} 다." if amount_raw else "")
        ),
        claim_kind="deal_fact",
        subject_key=f"deal.stage::{canonical}::{scope}",
        subject_value=stage_value or "",
        about=deal,
        fields=crit_fields,
    )
    ctx.batch.business_edge("WITH_ACCOUNT", deal, account, claim_ids=[deal_claim])

    if industry is not None:
        industry_name = ctx.batch.find_node(*industry).props["name"]
        industry_claim, _ = add_claim(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=f"{canonical} 의 업권은 {industry_name} 다.",
            claim_kind="deal_fact",
            subject_key=f"account.industry::{canonical}",
            subject_value=industry_name,
            about=account,
            fields={"account_canonical": canonical},
        )
        ctx.batch.business_edge("BELONGS_TO", account, industry, claim_ids=[industry_claim])

    for product in product_nodes(ctx, record["excerpt"], record["source_id"]):
        product_name = ctx.batch.find_node(*product).props["name"]
        product_claim, _ = add_claim(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=f"{canonical} 딜의 대상 제품으로 {product_name} 가 기록되어 있다.",
            claim_kind="deal_fact",
            subject_key=f"deal.product::{canonical}::{scope}::{product_name}",
            subject_value=product_name,
            about=deal,
            fields={"account_canonical": canonical},
        )
        ctx.batch.business_edge("FOR_PRODUCT", deal, product, claim_ids=[product_claim])

    _link_domain(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        raw_domain=flatten(s.get("업무_도메인")),
        anchor=deal,
        anchor_label=f"{canonical} 딜",
        mapper=ctx.resolver.map_activity_domain,
        split_rules=ctx.settings.activity_split_rules,
    )


def load_weekly_row(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    raw_account = flatten(s.get("고객사"))
    if not raw_account:
        ctx.counters["weekly_row_without_account"] += 1
        return
    account_name, scope = split_account_and_scope(ctx, raw_account)
    account = account_node(ctx, account_name, source_id=record["source_id"])
    if account is None:
        return
    canonical = ctx.batch.find_node(*account).props["canonical_name"]
    amount_raw, amount_krw = parse_amount(s.get("금액"))
    stage_value = flatten(s.get("현재단계")) or None
    deal = deal_node(
        ctx,
        account=canonical,
        scope=scope,
        source_id=record["source_id"],
        stage_system="weekly_plan_section6",
        stage_value=stage_value,
        amount_raw=amount_raw,
        amount_krw=amount_krw,
    )
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"주간계획 {record['locator']} 가 {s.get('주차')} {s.get('표')} 에 "
            f"{raw_account} 를 '{stage_value or '단계 미기재'}' 로 올렸다."
        ),
        mentions=[account, deal],
    )
    claim_id, _ = add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"{s.get('주차')} 주간계획에서 {canonical} 는 '{stage_value or '단계 미기재'}' 단계다."
            + (f" 금액은 {amount_raw} 로 적혀 있다." if amount_raw else "")
        ),
        claim_kind="deal_fact",
        subject_key=f"weekly.stage::{canonical}::{s.get('주차')}",
        subject_value=stage_value or "",
        about=deal,
        fields={
            "account_canonical": canonical,
            "amount_krw": amount_krw,
            "amount_raw": amount_raw,
            "stage_value": stage_value,
        },
    )
    ctx.batch.business_edge("WITH_ACCOUNT", deal, account, claim_ids=[claim_id])
    _link_domain(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        raw_domain=flatten(s.get("업무도메인")),
        anchor=deal,
        anchor_label=f"{canonical} 딜",
        mapper=ctx.resolver.map_weekly_domain,
        split_rules=(),
    )


def _link_domain(
    ctx: LoadContext,
    *,
    record: dict[str, Any],
    evidence_ref: NodeRef,
    raw_domain: str,
    anchor: NodeRef,
    anchor_label: str,
    mapper,
    split_rules: Iterable[str],
) -> None:
    """업무 도메인 값을 BD 에 잇는다. 근거가 확정되지 않은 값은 잇지 않는다."""
    if not raw_domain:
        return
    parts = [raw_domain]
    for rule in split_rules:
        expanded: list[str] = []
        for part in parts:
            expanded.extend(piece.strip() for piece in part.split(rule) if piece.strip())
        parts = expanded or parts
    for part in parts:
        bd_ids, _entry = mapper(part)
        for bd_id in bd_ids:
            bd = bd_node(ctx, bd_id, record["source_id"])
            if bd is None:
                continue
            seed = ctx.settings.bd_index[bd_id]
            claim_id, _ = add_claim(
                ctx,
                record=record,
                evidence_ref=evidence_ref,
                statement=f"{anchor_label} 은 업무 도메인 '{part}' 로 기록되어 BD '{seed['name']}' 에 속한다.",
                claim_kind="bd_registry_fact",
                subject_key=f"in_domain::{anchor[1]}::{bd_id}",
                subject_value=bd_id,
                about=bd,
            )
            # 원본 도메인 문자열은 Claim statement 에 남는다. Edge 계약이 허용하지 않는
            # 속성을 엣지에 붙이지 않는다.
            ctx.batch.business_edge("IN_DOMAIN", anchor, bd, claim_ids=[claim_id])


def load_pain_row(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    raw = flatten(s.get("pain_원문")) or record["excerpt"]
    mapping = ctx.resolver.map_need(raw)
    need = need_node(ctx, mapping, record)

    accounts: list[NodeRef] = []
    for part in re.split(r"[·,/]", flatten(s.get("고객사"))):
        ref = account_node(ctx, part.strip(), source_id=record["source_id"], kind="customer")
        if ref is not None:
            accounts.append(ref)

    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"Pain 레지스트리 {record['locator']} 가 {s.get('고객사') or '고객사 미기재'} 의 "
            f"문제로 '{raw[:200]}' 를 적었다."
        ),
        mentions=[need, *accounts],
    )

    need_name = mapping.name or raw[:120]
    for account in accounts:
        canonical = ctx.batch.find_node(*account).props["canonical_name"]
        claim_id, _ = add_claim(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=f"{canonical} 는 '{need_name}' 문제를 제기했다.",
            claim_kind="customer_generalization",
            subject_key=f"account.need::{canonical}::{need[1]}",
            subject_value=need[1],
            about=need,
            fields={"account_canonical": canonical},
        )
        ctx.batch.business_edge("HAS_NEED", account, need, claim_ids=[claim_id])

    # 대응 역량은 taxonomy 의 addresses_needs 가 정본이다. 사전에 걸린 canonical Need 에만 붙인다.
    if mapping.canonical and mapping.need_id:
        for capability_id, capability_seed in ctx.settings.capability_index.items():
            if mapping.need_id not in (capability_seed.get("addresses_needs") or []):
                continue
            capability = capability_node(ctx, capability_id, record["source_id"])
            if capability is None:
                continue
            claim_id, _ = add_claim(
                ctx,
                record=record,
                evidence_ref=evidence_ref,
                statement=(
                    f"'{capability_seed['name']}' 역량이 '{need_name}' 문제를 해결한다"
                    f"(capability taxonomy 의 addresses_needs 기준)."
                ),
                claim_kind="customer_generalization",
                subject_key=f"need.addressed_by::{mapping.need_id}::{capability_id}",
                subject_value=capability_id,
                about=need,
            )
            ctx.batch.business_edge(
                "ADDRESSED_BY",
                need,
                capability,
                claim_ids=[claim_id],
                resolution_status="claimed",
            )


def load_lead_alert(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    fields = (record["structured"].get("fields") or {})
    company = flatten(fields.get("회사"))
    event = event_node(ctx, record, event_type="contact", title=f"신규 리드 인입: {company or '회사 미기재'}")
    account = account_node(ctx, company, source_id=record["source_id"], kind="lead") if company else None
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=(
            f"리드등록알림 {record['locator']} 에 {company or '회사 미기재'} 의 신규 리드가 기록되었다."
            + (f" 문의: {flatten(fields.get('문의'))[:200]}" if fields.get("문의") else "")
        ),
        mentions=[ref for ref in (account,) if ref],
        event=event,
    )
    if account is None:
        return
    canonical = ctx.batch.find_node(*account).props["canonical_name"]
    add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=f"{canonical} 에서 인바운드 리드가 인입되었다.",
        claim_kind="deal_fact",
        subject_key=f"lead::{canonical}::{record['locator']}",
        subject_value=canonical,
        about=account,
        fields={"account_canonical": canonical},
    )


def load_meeting_note(ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef) -> None:
    s = record["structured"]
    title = flatten(s.get("title")) or flatten(s.get("subject")) or "미팅 기록"
    event = event_node(
        ctx,
        record,
        event_type="meeting",
        title=title,
        mode=flatten(s.get("mode")) or None,
        next_actions=s.get("action_items") or None,
    )
    counterpart = flatten(s.get("counterpart"))
    account = (
        account_node(ctx, counterpart, source_id=record["source_id"], from_title=True)
        if counterpart
        else None
    )
    mentions = [ref for ref in (account,) if ref]
    add_observation(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=f"영업활동 공유 {record['locator']} 에 '{title}' 기록이 있다.",
        mentions=mentions,
        event=event,
    )
    if account is None:
        return
    canonical = ctx.batch.find_node(*account).props["canonical_name"]
    add_claim(
        ctx,
        record=record,
        evidence_ref=evidence_ref,
        statement=f"{canonical} 와 '{title}' 건으로 접촉했다.",
        claim_kind="deal_fact",
        subject_key=f"meeting::{canonical}::{record['locator']}",
        subject_value=title,
        about=account,
        fields={"account_canonical": canonical},
    )


HANDLERS = {
    "feature_row": load_feature_row,
    "bd_row": load_bd_row,
    "bd_market": load_bd_market,
    "bd_maturity": load_bd_maturity,
    "bd_targets": load_bd_targets,
    "activity_row": load_activity_row,
    "deal_row": load_deal_row,
    "weekly_row": load_weekly_row,
    "pain_row": load_pain_row,
    "lead_alert": load_lead_alert,
    "meeting_note": load_meeting_note,
    # code_asset · doc_section 은 Evidence 만 남긴다. Feature 연결은 근거가 있을 때만 한다.
    "code_asset": None,
    "doc_section": None,
}
