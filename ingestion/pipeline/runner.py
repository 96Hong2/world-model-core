"""①~⑨ 오케스트레이션.

`data/parsed/` 를 읽어 GraphBatch 를 만든다. Neo4j 쓰기는 writer 가 따로 한다.
빌드와 쓰기를 나눈 이유는 규칙 검증을 DB 없이도 돌릴 수 있어야 하기 때문이다.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .direct import (
    HANDLERS,
    LoadContext,
    add_claim,
    add_evidence,
    add_observation,
    build_feature_capability_index,
    capability_node,
    finalize_conflicts,
    flatten,
    need_node,
)
from .llm_stage import LLMBudget, LLMStage
from .model import GraphBatch, NodeRef, sha
from .resolve import Resolver
from .settings import PARSED_DIR, Settings
from .verdict import CriticalityEngine, VerdictEngine

DOC_STATUS_MAP = {
    "final": "approved",
    "draft": "draft",
    "wip": "draft",
    "deprecated": "superseded",
    None: "unknown",
}

# ⑤ 문서 LLM 추출 — 어떤 발췌를 후보 추출에 보내는가.
# `record_kind` 가 있는 레코드는 결정적 직행이 이미 처리했으니 대상이 아니다.
DOC_EXTRACT_KINDS = frozenset({"(none)", "doc_section"})

# 보낼 문서 계열. 영업·제품 판단의 근거가 되는 자료만 넣는다.
# 레포·포털 기술 문서(`product_doc`·`repo_doc`)와 Slack 은 Evidence·전문검색으로만 쓴다.
# 발췌 13,600건 전부를 보내면 예산 안에서 큰 소스가 작은 소스를 밀어낸다.
DOC_EXTRACT_SOURCE_TYPES = frozenset(
    {
        "proposal",
        "bd_openbook",
        "product_brochure",
        "user_manual",
        "architecture_spec",
        "internal_deck",
        "compliance_checklist",
    }
)

# 계열이 맞아도 보내지 않는 소스. PII 미검증 자료는 후보 추출 대상이 아니다(DECISIONS A-3).
DOC_EXTRACT_SKIP = frozenset({"src_doc_hana_ins_training"})

# 한 소스가 예산을 독식하지 않게 소스별 상한을 둔다(PRD 1A AC-6 과 같은 취지).
DOC_EXTRACT_PER_SOURCE = 40

# ⑦ 딜 금액 추출 — 후보 발췌를 고르는 자.
# 영업 시트의 금액 칸은 118행이 원본부터 비어 있고, 금액은 슬랙·문서 본문에만 적혀 있다.
_AMOUNT_IN_TEXT = re.compile(
    r"(?<![0-9.])[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:억원|억|천만원|백만원|만원|원)(?![0-9])"
)
# 이름이 짧으면 아무 문장에나 걸린다('AG'·'신한'). 세 글자부터 후보로 본다.
_AMOUNT_ACCOUNT_MIN_LEN = 3

# 금액 뒤에 붙으면 값이 아니라 조건이라는 신호. '한도' 는 앞에 와도 마찬가지다.
_AMOUNT_BOUND_WORDS = ("이하", "이상", "미만", "초과")
_AMOUNT_LIMIT_WORDS = ("한도", "품의 한도", "결재 한도")
# 조건 낱말이 이만큼 안에 붙어 있으면 그 금액의 조건으로 본다.
_AMOUNT_BOUND_WINDOW = 12


def amount_is_bound(quote: str, amount_raw: str) -> bool:
    """인용 안에서 이 금액이 값이 아니라 상한·하한인가.

    「부서장 품의는 5천만원(부가세 별도) 이하이며」의 5천만원은 결재 한도다. 모델이 상한
    표현을 떼고 숫자만 주면 정규화로는 못 걸러지므로 인용 문장을 본다.

    조건 낱말과 금액 사이에 **다른 숫자**가 있으면 그 숫자의 조건으로 본다
    (「299,000,000원, 선금 30% 이상」의 '이상' 은 선금 비율에 붙은 것이다).
    """
    text = flatten(quote)
    if any(word in text for word in _AMOUNT_LIMIT_WORDS):
        return True
    head = flatten(amount_raw)
    for candidate in (head, head.split(" ")[0]):
        at = text.rfind(candidate)
        if at < 0:
            continue
        tail = text[at + len(candidate) : at + len(candidate) + _AMOUNT_BOUND_WINDOW]
        for word in _AMOUNT_BOUND_WORDS:
            found = tail.find(word)
            if found >= 0 and not any(ch.isdigit() for ch in tail[:found]):
                return True
        return False
    return False


AMOUNT_KIND_LABEL = {
    "contract": "계약 금액",
    "proposal": "제안·견적 금액",
    "budget": "고객 사업 예산",
}
# 체결된 금액이 견적·예산보다 앞선다. 견적이 계약을 가리면 파이프라인 총액이 작아진다.
_AMOUNT_KIND_RANK = {"contract": 0, "proposal": 1, "budget": 2}


def pick_amount(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """딜 속성으로 쓸 후보 하나. 종류가 먼저고, 같은 종류면 최신·큰 금액 순이다.

    날짜·금액으로 먼저 줄을 세우고 종류로 `min` 을 잡는다. `min` 은 안정 정렬이라
    같은 종류 안에서는 앞 줄 순서가 그대로 남는다.
    """
    ordered = sorted(
        candidates, key=lambda c: (c["observed_at"], c["krw"]), reverse=True
    )
    return min(ordered, key=lambda c: _AMOUNT_KIND_RANK.get(c["kind"], 3))


def deal_amount_pool(ctx: LoadContext, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """금액 표현과 **딜이 있는 계정 이름**이 함께 있는 발췌만 고른다.

    둘 중 하나만 있는 발췌를 보내면 콜만 늘고 얻는 것이 없다. 실측(2026-08-12): 발췌
    17,616건 중 이 조건을 만족하는 것이 105건이라 배치 10건 기준 11콜이면 전부 훑는다.
    """
    deal_accounts = {
        node.props["account_canonical"] for node in ctx.batch.nodes_by_label("Deal")
    }
    names: dict[str, str] = {}
    for node in ctx.batch.nodes_by_label("Account"):
        canonical = node.props["canonical_name"]
        if canonical not in deal_accounts:
            continue
        for name in [canonical, *(node.props.get("raw_names") or [])]:
            if name and len(name) >= _AMOUNT_ACCOUNT_MIN_LEN:
                names[name] = canonical
    chosen: list[dict[str, Any]] = []
    for record in records:
        text = record.get("excerpt") or ""
        if not _AMOUNT_IN_TEXT.search(text):
            continue
        if not any(name in text for name in names):
            continue
        chosen.append(record)
    return chosen


def _document_extraction_pool(
    records_by_kind: dict[str, list[dict[str, Any]]],
    source_type: dict[str, str],
) -> list[dict[str, Any]]:
    """문서 발췌 LLM 추출 대상을 고른다. 소스를 돌아가며 뽑아 작은 소스가 밀리지 않게 한다."""
    by_source: dict[str, list[dict[str, Any]]] = {}
    for kind in DOC_EXTRACT_KINDS:
        for record in records_by_kind.get(kind) or []:
            sid = record["source_id"]
            if sid in DOC_EXTRACT_SKIP:
                continue
            if source_type.get(sid) not in DOC_EXTRACT_SOURCE_TYPES:
                continue
            bucket = by_source.setdefault(sid, [])
            if len(bucket) < DOC_EXTRACT_PER_SOURCE:
                bucket.append(record)

    pool: list[dict[str, Any]] = []
    for depth in range(DOC_EXTRACT_PER_SOURCE):
        for sid in sorted(by_source):
            records = by_source[sid]
            if depth < len(records):
                pool.append(records[depth])
    return pool


class SourceRegistrationError(ValueError):
    """등록할 수 없는 Source. sensitivity 가 없으면 무조건 거부한다(deny-by-default)."""


@dataclass
class PipelineOptions:
    only: Sequence[str] = ()
    use_llm: bool = False
    # 달러 상한은 sonnet 기준이다. 2026-08-12 에 S tier 를 haiku → sonnet 으로 올리면서 콜당
    # 단가가 4.2배가 됐고(실측 $0.016 → $0.0668), 상한의 목적이 「폭주 방지」라 같은 콜 수에서
    # 걸리도록 달러 값을 4배로 옮겼다. 호출 수 상한(max_calls)은 그대로다 — 진짜 방어선은 그쪽이다.
    budget_usd: float = 32.0
    max_calls: int = 400
    batch_size: int = 10
    parsed_dir: pathlib.Path = PARSED_DIR
    run_id: str = "ingest"
    # 매핑 단계 몫으로 떼어 둘 금액. 0 이면 추출이 예산을 다 쓸 수 있어 매핑이 굶는다.
    # sonnet 기준으로 매핑 1콜이 약 $0.23 이라(haiku 실측 $0.054 × 4.2) $6 이면 약 26콜이다.
    mapping_reserve_usd: float = 6.0


@dataclass
class BuildResult:
    batch: GraphBatch
    counters: Counter
    unmapped: dict[str, dict[str, int]]
    sources: list[str]
    llm_report: Any = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ① Source 등록
# ---------------------------------------------------------------------------


def register_source(record: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """파서가 낸 source.json 과 config/sources.yaml 을 맞춰 Source 노드 속성을 만든다.

    config 가 정본이다. config 에 없거나 sensitivity 가 비어 있으면 적재하지 않는다.
    """
    source_id = record.get("source_id")
    entry = settings.source_index.get(source_id)
    if entry is None:
        raise SourceRegistrationError(
            f"config/sources.yaml 에 없는 소스다: {source_id!r}. 등록 없이 적재하지 않는다."
        )
    sensitivity = entry.get("sensitivity")
    visibility = entry.get("visibility")
    if not sensitivity or not visibility:
        raise SourceRegistrationError(
            f"{source_id}: sensitivity/visibility 가 config 에 없다. deny-by-default 로 거부한다."
        )
    sor = entry.get("source_of_record_for") or record.get("source_of_record_for")
    if isinstance(sor, list):
        sor = " / ".join(sor)
    return {
        "source_id": source_id,
        "source_type": entry.get("source_type") or record.get("source_type"),
        "format": entry.get("format") or record.get("format"),
        "canonical_location": record.get("canonical_location") or entry.get("path"),
        "content_hash": record.get("content_hash"),
        "modified_at": _date_only(record.get("modified_at")),
        "origin": entry.get("origin") or record.get("origin"),
        "author_role": record.get("author_role"),
        "doc_status": DOC_STATUS_MAP.get(record.get("doc_status"), "unknown"),
        "visibility": visibility,
        "sensitivity": sensitivity,
        "pii_flag": bool(entry.get("pii_flag", record.get("pii_flag"))),
        "extractor": "vision" if entry.get("extractor") == "vision" else "deterministic",
        "source_status": "active",
        "source_of_record_for": sor,
        "source_ids": [source_id],
    }


def _date_only(value: Any) -> str | None:
    if not value:
        return None
    return str(value)


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------


class IngestPipeline:
    def __init__(self, options: PipelineOptions | None = None, settings: Settings | None = None):
        self.options = options or PipelineOptions()
        self.settings = settings or Settings.load()

    # -- 입력 ---------------------------------------------------------------
    def source_files(self) -> list[pathlib.Path]:
        wanted = set(self.options.only or ())
        files = sorted(self.options.parsed_dir.glob("*.source.json"))
        if wanted:
            files = [f for f in files if f.name[: -len(".source.json")] in wanted]
        return files

    # -- 실행 ---------------------------------------------------------------
    def build(self) -> BuildResult:
        ctx, records_by_kind, evidence_refs, notes, registered = self.load_deterministic()

        # ⑤ LLM (예산 안에서만)
        llm_report = None
        if self.options.use_llm:
            llm_report = self._run_llm(ctx, records_by_kind, evidence_refs, notes)

        # 상충 측정 (1A 는 lane 부여까지)
        ctx.counters["conflict_marked"] = finalize_conflicts(ctx)

        log = ctx.resolver.log
        unmapped = {
            "activity_domain": dict(log.unmapped_domains),
            "need": dict(log.unmapped_needs),
            "capability": dict(log.unmapped_capabilities),
            "account_new": dict(log.unknown_accounts),
            "account_excluded": dict(log.excluded_accounts),
            "counterpart_rejected": dict(log.rejected_counterparts),
        }
        ctx.counters["criticality_fires"] = sum(ctx.criticality.fire_counts.values())
        for rule_id, count in ctx.criticality.fire_counts.items():
            ctx.counters[f"criticality:{rule_id}"] = count

        return BuildResult(
            batch=ctx.batch,
            counters=ctx.counters,
            unmapped=unmapped,
            sources=registered,
            llm_report=llm_report,
            notes=notes,
        )

    def load_deterministic(self):
        """①~③ 결정적 구간만 돈다. LLM 단계 하나만 따로 돌리는 스크립트가 여기서 갈라진다.

        반환: (ctx, records_by_kind, evidence_refs, notes, registered)
        """
        resolver = Resolver(self.settings)
        ctx = LoadContext(
            settings=self.settings,
            resolver=resolver,
            verdicts=VerdictEngine(self.settings),
            criticality=CriticalityEngine(self.settings),
            batch=GraphBatch(),
            feature_capability_index=build_feature_capability_index(self.settings),
        )
        notes: list[str] = []
        registered: list[str] = []
        records_by_kind: dict[str, list[dict[str, Any]]] = {}
        evidence_refs: dict[str, NodeRef] = {}

        for path in self.source_files():
            source_record = json.loads(path.read_text(encoding="utf-8"))
            try:
                props = register_source(source_record, self.settings)
            except SourceRegistrationError as exc:
                notes.append(str(exc))
                ctx.counters["source_rejected"] += 1
                continue
            source_id = props["source_id"]
            natural_key = sha(props["canonical_location"])
            ctx.source_ref[source_id] = ctx.batch.node("Source", natural_key, **props)
            ctx.source_type[source_id] = props["source_type"]
            registered.append(source_id)

            jsonl = path.with_name(f"{source_id}.jsonl")
            if not jsonl.exists():
                notes.append(f"{source_id}: jsonl 이 없다 — Evidence 0건")
                continue

            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                evidence_ref = add_evidence(ctx, record)
                evidence_refs[record["evidence_id"]] = evidence_ref
                ctx.counters["evidence"] += 1
                structured = record.get("structured")
                kind = structured["record_kind"] if structured else "(none)"
                records_by_kind.setdefault(kind, []).append(record)

        # ③ 결정적 직행
        for kind, records in records_by_kind.items():
            handler = HANDLERS.get(kind)
            if handler is None:
                ctx.counters[f"evidence_only:{kind}"] += len(records)
                continue
            for record in records:
                handler(ctx, record, evidence_refs[record["evidence_id"]])
                ctx.counters[f"loaded:{kind}"] += 1

        return ctx, records_by_kind, evidence_refs, notes, registered

    # -- ⑤ LLM 단계 --------------------------------------------------------
    def _run_llm(
        self,
        ctx: LoadContext,
        records_by_kind: dict[str, list[dict[str, Any]]],
        evidence_refs: dict[str, NodeRef],
        notes: list[str],
    ):
        from llm.cache import LLMCache
        from llm.providers import ClaudeCLIProvider
        from llm.service import DEFAULT_CACHE_DIR, LLMService

        provider = ClaudeCLIProvider()
        service = LLMService(
            provider,
            cache=LLMCache(DEFAULT_CACHE_DIR),
            budget_usd=1e9,  # 실행 단위 상한은 LLMBudget 이 관리한다(원장 누적치와 분리).
        )
        budget = LLMBudget(
            max_usd=self.options.budget_usd,
            max_calls=self.options.max_calls,
            reserved_usd=self.options.mapping_reserve_usd,
        )
        lexicon = [
            entry["canonical"] for entry in self.settings.aliases["accounts"]
        ] + [entry["canonical"] for entry in self.settings.aliases.get("competitors") or []]
        stage = LLMStage(service, budget=budget, batch_size=self.options.batch_size, lexicon=lexicon)

        # ④ 2차: 사전에 안 걸린 Need/Capability 표현만 후보 제안을 받는다.
        self._map_unresolved_needs(ctx, records_by_kind, evidence_refs, stage)
        self._map_pain_capabilities(ctx, records_by_kind, evidence_refs, stage)

        # ⑤ 우선순위 1 — 제안서·Open Book·제품소개서·매뉴얼·아키텍처 정의서·Q&A덱 문서 발췌.
        # 미팅 기록보다 먼저 한다. 미팅 발췌가 2,770건이라 뒤에 두면 어떤 예산에서도 문서에 닿지 못한다
        # (실측: budget 2.5 USD 42콜을 미팅이 다 쓰고 문서 478건이 전부 미처리로 남았다).
        doc_records = _document_extraction_pool(records_by_kind, ctx.source_type)
        candidate_docs = sum(
            len(records_by_kind.get(kind) or []) for kind in DOC_EXTRACT_KINDS
        )
        skipped_docs = candidate_docs - len(doc_records)
        if skipped_docs > 0:
            notes.append(
                f"LLM 추출에서 제외한 문서 발췌 {skipped_docs}건 / 전체 {candidate_docs}건 "
                f"(대상 계열: {', '.join(sorted(DOC_EXTRACT_SOURCE_TYPES))} · "
                f"제외 소스: {', '.join(sorted(DOC_EXTRACT_SKIP))} · "
                f"소스별 상한 {DOC_EXTRACT_PER_SOURCE}건)"
            )
        pool_sources = sorted({record["source_id"] for record in doc_records})
        if pool_sources:
            notes.append(
                f"LLM 문서 추출 대상 {len(doc_records)}건 · 소스 {len(pool_sources)}개: "
                f"{', '.join(pool_sources)}"
            )
        self._extract_records(ctx, stage, doc_records, evidence_refs, extractor="document")

        # ⑤ 우선순위 2 — 미팅 기록(Slack). 남은 예산만 쓴다.
        self._extract_records(
            ctx,
            stage,
            records_by_kind.get("meeting_note") or [],
            evidence_refs,
            extractor="meeting",
        )

        # ⑥ 문서·미팅이 찾은 니즈 중 사전에 없던 것을 taxonomy 에 붙인다.
        # 두 추출이 다 끝난 뒤에 한 번만 돈다 — 같은 표현을 두 번 물어보지 않으려고.
        self._map_llm_needs(ctx, stage, evidence_refs)

        # ⑦ 자유 서술에 적힌 딜 금액. 금액 표현과 딜 계정이 함께 있는 발췌만 훑는다.
        amount_records = deal_amount_pool(
            ctx,
            [
                record
                for kind in ("meeting_note", *DOC_EXTRACT_KINDS)
                for record in (records_by_kind.get(kind) or [])
            ],
        )
        if amount_records:
            notes.append(f"LLM 딜 금액 추출 대상 {len(amount_records)}건")
        self._extract_deal_amounts(ctx, stage, amount_records, evidence_refs)

        for target, count in stage.report.skipped.items():
            notes.append(f"예산 상한으로 처리하지 못함: {target} 발췌 {count}건")
        return stage.report

    def _map_unresolved_needs(self, ctx, records_by_kind, evidence_refs, stage: LLMStage) -> None:
        pending: list[dict[str, Any]] = []
        by_raw: dict[str, list[dict[str, Any]]] = {}
        for record in records_by_kind.get("pain_row") or []:
            raw = flatten((record.get("structured") or {}).get("pain_원문")) or record["excerpt"]
            if ctx.resolver.map_need(raw).canonical:
                continue
            if raw not in by_raw:
                pending.append(
                    {"source_id": record["source_id"], "locator": record["locator"], "text": raw}
                )
            by_raw.setdefault(raw, []).append(record)
        if not pending:
            return
        catalog = [(need["id"], need["name"]) for need in self.settings.need_taxonomy["needs"]]
        proposals = stage.propose_mappings(
            kind="Need",
            excerpts=pending,
            allowed_ids=[cid for cid, _ in catalog],
            catalog=catalog,
        )
        ctx.counters["llm_need_mapping"] = len(proposals)
        self._apply_need_proposals(ctx, proposals, by_raw, evidence_refs)

    def _apply_need_proposals(
        self,
        ctx: LoadContext,
        proposals: dict[str, str],
        by_raw: dict[str, list[dict[str, Any]]],
        evidence_refs: dict[str, NodeRef],
    ) -> None:
        """제안받은 매핑을 실제로 잇는다. 제안만 받고 버리면 비용만 쓴 셈이다.

        canonical 은 taxonomy 에만 있다(제안은 enum 으로 묶여 있어 신규 id 가 올 수 없다).
        LLM 이 근거이므로 이 Claim 은 상태 규칙에 따라 CANDIDATE 로 내려간다.
        """
        from .direct import account_node
        from .resolve import NeedMapping

        for raw, need_id in proposals.items():
            seed = self.settings.need_index.get(need_id)
            if seed is None:
                continue
            mapping = NeedMapping(
                need_id=need_id,
                name=seed["name"],
                need_type=seed["need_type"],
                canonical=True,
                matched_expression=raw,
            )
            for record in by_raw.get(raw, []):
                need_ref = need_node(ctx, mapping, record)
                ctx.llm_need_mapped[record["evidence_id"]] = need_ref
                structured = record.get("structured") or {}
                for part in re.split(r"[·,/]", flatten(structured.get("고객사"))):
                    account_ref = account_node(
                        ctx, part.strip(), source_id=record["source_id"], kind="customer"
                    )
                    if account_ref is None:
                        continue
                    canonical = ctx.batch.find_node(*account_ref).props["canonical_name"]
                    claim_id, _ = add_claim(
                        ctx,
                        record=record,
                        evidence_ref=evidence_refs[record["evidence_id"]],
                        statement=(
                            f"{canonical} 가 적은 '{raw[:200]}' 는 '{seed['name']}' 문제에 해당한다."
                        ),
                        claim_kind="customer_generalization",
                        subject_key=f"account.need::{canonical}::{need_id}",
                        subject_value=need_id,
                        about=need_ref,
                        fields={"account_canonical": canonical},
                        extractor="llm",
                    )
                    ctx.batch.business_edge(
                        "HAS_NEED", account_ref, need_ref, claim_ids=[claim_id]
                    )
                    ctx.counters["llm_need_edges"] += 1

    def _map_llm_needs(self, ctx: LoadContext, stage: LLMStage, evidence_refs) -> None:
        """문서·미팅 추출이 찾았지만 사전에 없던 니즈에 2차 매핑 기회를 준다.

        Pain 대장 경로(`_map_unresolved_needs`)와 같은 장치다. 이쪽에만 없어서 니즈가 버려졌다.
        실측(캐시 전수): 추출 응답이 need_raw 를 625건 담아 왔는데 사전 적중이 0건이었고,
        그래서 406개 계정 중 4개(Pain 대장 고객)만 HAS_NEED 를 가졌다.

        계정이 함께 온 표현만 보낸다. HAS_NEED 를 만들 수 있는 자리이고, 계정 없이 온 표현은
        「반복 매출 확보」·「운영 조직 필요」처럼 우리 내부 과제인 경우가 많다(612종 중 280종).
        고객 니즈가 아닌 것을 Account 에 붙이면 그래프가 오염된다. 매핑 스키마의 `UNMAPPED` 가
        문지기라, 매핑기가 거절한 표현은 노드도 엣지도 만들지 않는다.
        """
        from .resolve import NeedMapping

        pending = [item for item in ctx.llm_need_pending if item["account"] is not None]
        if not pending:
            return

        by_raw: dict[str, list[dict[str, Any]]] = {}
        excerpts: list[dict[str, Any]] = []
        for item in pending:
            raw = item["raw"]
            if raw not in by_raw:
                excerpts.append(
                    {
                        "source_id": item["record"]["source_id"],
                        "locator": item["record"]["locator"],
                        "text": raw,
                    }
                )
            by_raw.setdefault(raw, []).append(item)

        catalog = [(need["id"], need["name"]) for need in self.settings.need_taxonomy["needs"]]
        proposals = stage.propose_mappings(
            kind="Need",
            excerpts=excerpts,
            allowed_ids=[cid for cid, _ in catalog],
            catalog=catalog,
        )
        ctx.counters["llm_doc_need_mapping"] = len(proposals)

        for raw, need_id in proposals.items():
            seed = self.settings.need_index.get(need_id)
            if seed is None:
                continue
            mapping = NeedMapping(
                need_id=need_id,
                name=seed["name"],
                need_type=seed["need_type"],
                canonical=True,
                matched_expression=raw,
            )
            for item in by_raw.get(raw, []):
                record = item["record"]
                evidence_ref = evidence_refs.get(record["evidence_id"])
                if evidence_ref is None:
                    continue
                need_ref = need_node(ctx, mapping, record)
                account_ref = item["account"]
                canonical = ctx.batch.find_node(*account_ref).props["canonical_name"]
                claim_id, _ = add_claim(
                    ctx,
                    record=record,
                    evidence_ref=evidence_ref,
                    statement=(
                        f"{canonical} 자료에 적힌 '{raw[:200]}' 는 '{seed['name']}' 문제에 해당한다."
                    ),
                    claim_kind="customer_generalization",
                    subject_key=f"account.need::{canonical}::{need_id}",
                    subject_value=need_id,
                    about=need_ref,
                    fields={"account_canonical": canonical},
                    extractor="llm",
                )
                ctx.batch.business_edge("HAS_NEED", account_ref, need_ref, claim_ids=[claim_id])
                ctx.counters["llm_doc_need_edges"] += 1

    def _map_pain_capabilities(self, ctx, records_by_kind, evidence_refs, stage: LLMStage) -> None:
        pending: list[dict[str, Any]] = []
        by_raw: dict[str, list[dict[str, Any]]] = {}
        for record in records_by_kind.get("pain_row") or []:
            raw = flatten((record.get("structured") or {}).get("대응_기능"))
            if not raw:
                continue
            if ctx.resolver.map_capability(raw).canonical:
                continue
            by_raw.setdefault(raw, []).append(record)
        for raw, records in by_raw.items():
            first = records[0]
            pending.append(
                {"source_id": first["source_id"], "locator": first["locator"], "text": raw}
            )
        if not pending:
            return
        catalog = [
            (item["id"], item["name"])
            for item in self.settings.capability_taxonomy["capabilities"]
        ]
        proposals = stage.propose_mappings(
            kind="Capability",
            excerpts=pending,
            allowed_ids=[cid for cid, _ in catalog],
            catalog=catalog,
        )
        ctx.counters["llm_capability_mapping"] = len(proposals)

        for raw, capability_id in proposals.items():
            for record in by_raw.get(raw, []):
                structured = record.get("structured") or {}
                need_mapping = ctx.resolver.map_need(
                    flatten(structured.get("pain_원문")) or record["excerpt"]
                )
                if need_mapping.canonical:
                    need_ref = need_node(ctx, need_mapping, record)
                    need_name = need_mapping.name
                else:
                    # 사전에는 없지만 LLM 이 taxonomy 항목으로 이어 준 Need 도 대상이다.
                    need_ref = ctx.llm_need_mapped.get(record["evidence_id"])
                    if need_ref is None:
                        continue
                    need_name = ctx.batch.find_node(*need_ref).props.get("name")
                if not need_name:
                    continue
                capability_ref = capability_node(ctx, capability_id, record["source_id"])
                if capability_ref is None:
                    continue
                seed = self.settings.capability_index[capability_id]
                claim_id, _ = add_claim(
                    ctx,
                    record=record,
                    evidence_ref=evidence_refs[record["evidence_id"]],
                    statement=(
                        f"Pain 레지스트리가 '{need_name}' 의 대응 기능으로 적은 "
                        f"'{raw}' 는 역량 '{seed['name']}' 에 해당한다."
                    ),
                    claim_kind="customer_generalization",
                    subject_key=f"pain.capability::{record['locator']}::{capability_id}",
                    subject_value=capability_id,
                    about=need_ref,
                    extractor="llm",
                )
                ctx.batch.business_edge(
                    "ADDRESSED_BY",
                    need_ref,
                    capability_ref,
                    claim_ids=[claim_id],
                    resolution_status="claimed",
                )

    def _extract_records(
        self,
        ctx: LoadContext,
        stage: LLMStage,
        records: Sequence[dict[str, Any]],
        evidence_refs: dict[str, NodeRef],
        *,
        extractor: str,
    ) -> None:
        if not records:
            return
        index = {
            (record["source_id"], record["locator"]): record for record in records
        }
        excerpts = [
            {
                "source_id": record["source_id"],
                "locator": record["locator"],
                "text": record["excerpt"],
            }
            for record in records
        ]
        if extractor == "meeting":
            items = stage.extract_meeting_candidates(excerpts)
        else:
            items = stage.extract_document_candidates(excerpts, purpose=extractor)

        for item in items:
            record = index.get((item.get("source_id"), item.get("locator")))
            if record is None:
                ctx.counters["llm_item_unmatched_locator"] += 1
                continue
            if extractor == "meeting":
                # 게이트는 항목만 보고 발췌를 못 본다. 인용 자체가 지어낸 것이면 이름·숫자가
                # 그 인용 안에 있으니 게이트를 통과한다. account 를 인용 대조에서 면제한
                # 대가로, 인용과 이름이 발췌에 실재하는지는 여기서 대조한다(딜 금액과 동일).
                excerpt = flatten(record["excerpt"])
                quote = flatten(item.get("evidence_quote"))
                if not quote or quote not in excerpt:
                    ctx.counters["llm_meeting_quote_not_in_excerpt"] += 1
                    continue
                account = flatten(item.get("account"))
                if account and account not in excerpt:
                    ctx.counters["llm_meeting_account_not_in_excerpt"] += 1
                    continue
            self._absorb_llm_item(ctx, record, evidence_refs[record["evidence_id"]], item)

    def _extract_deal_amounts(
        self,
        ctx: LoadContext,
        stage: LLMStage,
        records: Sequence[dict[str, Any]],
        evidence_refs: dict[str, NodeRef],
        exclude_locators: Iterable[str] = (),
    ) -> None:
        """자유 서술에 적힌 딜 금액을 이미 있는 딜에 붙인다.

        딜을 새로 만들지 않고, 시트 금액을 덮어쓰지도 않는다. 활동일지가 딜 금액의 정본이고
        (`config/claim-verdict-rules.yaml` 은 슬랙 딜 사실을 CANDIDATE 로 둔다), 슬랙 값이
        조용히 갈아치우면 파이프라인 총액이 근거 없이 움직인다.

        후보가 여럿이면 **먼저 온 것이 아니라 계약 > 제안·견적 > 예산, 같은 종류면 최신**을
        고른다. 순서에 맡겼더니 라이선스 견적 5천만원이 체결 계약 2.99억을 가렸다(실측).
        밀린 후보도 Claim 으로는 남는다 — 지우면 어긋남을 나중에 물을 수 없다.

        계정에 딜이 여럿이면(다라카드 자동차·채권) 어디에 붙일지 알 수 없어 건드리지 않는다.

        `exclude_locators` 는 사람이 「우리 딜보다 범위가 넓다」고 판정해 뺀 자리다. 규칙으로
        가릴 수 없는 판단이라(고객 프로그램 전체 예산인지 우리 몫인지는 원문에 없다) 사람이
        정한다. 뺀 후보도 근거 주장으로는 남는다 — 지우면 그 금액이 자료에 있었다는 사실이
        사라진다.
        """
        from .direct import account_node, parse_amount

        if not records:
            return
        index = {(record["source_id"], record["locator"]): record for record in records}
        excerpts = [
            {
                "source_id": record["source_id"],
                "locator": record["locator"],
                "text": record["excerpt"],
            }
            for record in records
        ]
        excluded = set(exclude_locators)
        accepted: dict[NodeRef, list[dict[str, Any]]] = {}
        deals_by_account: dict[str, list[NodeRef]] = {}
        for node in ctx.batch.nodes_by_label("Deal"):
            deals_by_account.setdefault(node.props["account_canonical"], []).append(
                ("Deal", node.natural_key)
            )

        for item in stage.extract_deal_amounts(excerpts):
            record = index.get((item.get("source_id"), item.get("locator")))
            if record is None:
                ctx.counters["llm_item_unmatched_locator"] += 1
                continue
            evidence_ref = evidence_refs.get(record["evidence_id"])
            if evidence_ref is None:
                continue
            # t2 게이트는 항목만 보고 발췌 원문을 못 본다. 인용 자체가 지어낸 것이면
            # 숫자·이름이 그 인용 안에 있으니 게이트를 통과한다. 발췌와 대조하는 것은 여기 몫이다.
            excerpt = flatten(record["excerpt"])
            quote = flatten(item.get("evidence_quote"))
            if not quote or quote not in excerpt:
                ctx.counters["llm_deal_amount_quote_not_in_excerpt"] += 1
                continue
            if not flatten(item.get("account")) or flatten(item["account"]) not in excerpt:
                ctx.counters["llm_deal_amount_account_not_in_excerpt"] += 1
                continue
            amount_raw, amount_krw = parse_amount(item.get("amount_raw"))
            if amount_krw is None:
                ctx.counters["llm_deal_amount_unparsed"] += 1
                continue
            if amount_is_bound(quote, amount_raw or ""):
                ctx.counters["llm_deal_amount_bound_not_value"] += 1
                continue
            account_ref = account_node(
                ctx, flatten(item.get("account")), source_id=record["source_id"], from_title=True
            )
            if account_ref is None:
                ctx.counters["llm_deal_amount_account_unresolved"] += 1
                continue
            canonical = ctx.batch.find_node(*account_ref).props["canonical_name"]
            deal_refs = deals_by_account.get(canonical) or []
            if len(deal_refs) != 1:
                ctx.counters[
                    "llm_deal_amount_no_deal" if not deal_refs else "llm_deal_amount_ambiguous"
                ] += 1
                continue
            deal_ref = deal_refs[0]
            deal = ctx.batch.find_node(*deal_ref)
            kind = flatten(item.get("amount_kind"))
            kind_label = AMOUNT_KIND_LABEL.get(kind, "금액")
            claim_id, _ = add_claim(
                ctx,
                record=record,
                evidence_ref=evidence_ref,
                statement=f"{canonical} 딜의 {kind_label}이 {amount_raw} 로 기록되어 있다.",
                claim_kind="deal_fact",
                subject_key=f"deal.amount::{canonical}::{deal.props.get('deal_scope') or '기본'}",
                subject_value=amount_raw or "",
                about=deal_ref,
                fields={
                    "account_canonical": canonical,
                    "amount_krw": amount_krw,
                    "amount_raw": amount_raw,
                },
                extractor="llm",
            )
            ctx.batch.business_edge(
                "WITH_ACCOUNT", deal_ref, account_ref, claim_ids=[claim_id]
            )
            if record["locator"] in excluded:
                ctx.counters["llm_deal_amount_scope_excluded"] += 1
                continue
            accepted.setdefault(deal_ref, []).append(
                {
                    "raw": amount_raw,
                    "krw": amount_krw,
                    "kind": kind,
                    "observed_at": record.get("authored_at") or "",
                    "source_id": record["source_id"],
                    "locator": record["locator"],
                    "quote": quote,
                }
            )

        # 사람이 인용을 읽고 판정할 수 있게 후보를 남긴다. 그래프에는 안 들어간다.
        ctx.amount_candidates = accepted

        # 딜 속성은 후보를 다 모은 뒤에 고른다. 하나씩 넣으면 먼저 온 것이 이긴다.
        for deal_ref, candidates in accepted.items():
            deal = ctx.batch.find_node(*deal_ref)
            if deal.props.get("amount_krw") is not None:
                ctx.counters["llm_deal_amount_kept_sheet"] += len(candidates)
                continue
            best = pick_amount(candidates)
            ctx.batch.node("Deal", deal_ref[1], source_ids=[best["source_id"]])
            # 짝을 함께 바꾼다. 원문 병합은 「빈 자리만 채운다」라서, 시트에 미기재 표시('0'·'-')가
            # 적혀 있으면 새 정규화값만 들어가고 원문은 '0' 으로 남아 짝이 어긋난다.
            # 계약서가 「amount_krw 는 amount_raw 없이 단독 인용 금지」로 정해 둔 자리다.
            if deal.props.get("amount_raw") not in (None, best["raw"]):
                ctx.counters["llm_deal_amount_placeholder_raw"] += 1
            deal.props["amount_raw"] = best["raw"]
            deal.props["amount_krw"] = best["krw"]
            ctx.counters["llm_deal_amount_set"] += 1
            ctx.counters["llm_deal_amount_runner_up"] += len(candidates) - 1

    def _absorb_llm_item(
        self, ctx: LoadContext, record: dict[str, Any], evidence_ref: NodeRef, item: dict[str, Any]
    ) -> None:
        signal = item.get("signal") or "need"
        statement = flatten(item.get("statement"))[:600]
        if not statement:
            return
        account_ref = None
        if item.get("account"):
            from .direct import account_node

            account_ref = account_node(
                ctx, flatten(item["account"]), source_id=record["source_id"], from_title=True
            )
        competitor_ref = None
        if item.get("competitor"):
            entry = ctx.resolver.resolve_competitor(flatten(item["competitor"]))
            if entry is not None:
                from .resolve import match_key

                competitor_ref = ctx.batch.node(
                    "Competitor",
                    match_key(entry["canonical"]),
                    name=entry["canonical"],
                    competitor_kind=entry["kind"],
                    source_ids=[record["source_id"]],
                )

        need_ref = None
        if item.get("need_raw"):
            need_raw = flatten(item["need_raw"])
            need_mapping = ctx.resolver.map_need(need_raw)
            if need_mapping.canonical:
                need_ref = need_node(ctx, need_mapping, record)
            else:
                # 사전에 없는 표현이라고 버리지 않는다. 2차 매핑 대기줄에 넣는다(_map_llm_needs).
                ctx.llm_need_pending.append(
                    {"raw": need_raw, "record": record, "account": account_ref}
                )

        mentions = [ref for ref in (account_ref, competitor_ref, need_ref) if ref]
        add_observation(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=statement,
            mentions=mentions,
            extractor="llm",
        )
        claim_kind = {
            "need": "customer_generalization",
            "competition": "interpretation",
            "deal_progress": "deal_fact",
            "risk": "interpretation",
            "next_action": "interpretation",
        }.get(signal, "interpretation")

        about = account_ref or need_ref or competitor_ref
        claim_id, _ = add_claim(
            ctx,
            record=record,
            evidence_ref=evidence_ref,
            statement=statement,
            claim_kind=claim_kind,
            subject_key=f"llm::{record['source_id']}::{record['locator']}::{signal}",
            subject_value=statement[:120],
            about=about,
            fields={
                "account_canonical": (
                    ctx.batch.find_node(*account_ref).props["canonical_name"]
                    if account_ref
                    else None
                )
            },
            extractor="llm",
        )
        ctx.counters["llm_claims"] += 1

        if account_ref is not None and need_ref is not None:
            ctx.batch.business_edge("HAS_NEED", account_ref, need_ref, claim_ids=[claim_id])
