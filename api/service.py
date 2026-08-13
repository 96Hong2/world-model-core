"""Answer 조립 (REVISED §6 A6).

    A5 RetrievalResult
      → 근거 선별(§ selection)
      → 합성(§ synthesis: Q-E·Q-M 은 S tier, Q-S 는 L tier)
      → 하드 게이트 3종(§ guards: PII · traceability · 무근거 숫자·고유명사)
      → Answer 계약 조립
      → query audit

게이트에 걸린 것은 조용히 통과시키지 않는다. 문장을 빼고, 무엇을 왜 뺐는지 `GuardReport`
에 남기고, audit 에 개수를 적는다.

원문 검색 발췌는 근거 후보 풀에 들어간다 — 그래프 순회로 닿았는지 여부는 그 발췌가 좋은
근거인지와 무관하고, 인용을 달았는데 evidence 목록에 없으면 사용자가 원본으로 넘어갈 길이
끊긴다. 그렇게 근거로 승격된 발췌는 `raw_signals` 에서 뺀다. 두 자리에 같은 것을 실으면
화면이 같은 발췌를 두 번 보여 주고, "추가 원문 근거" 섹션의 안내문("답변의 근거로 쓰지
않았습니다")이 거짓이 된다(answer.schema.json: "합성 인용과 섞지 않는다").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from graph.connection import ReadOnlyGraph, require_read_only
from retrieval import RetrievalService
from retrieval.access import AccessPolicy
from retrieval.citations import strip_uncited_sentences, verify_citations
from retrieval.store import fetch_evidence
from retrieval.text import content_terms, derive_gap_subject, term_overlap
from retrieval.types import EvidenceRef, RetrievalResult

from . import contradiction, graphview, strength
from .accounts import Account, default_account
from .anchoring import (
    MarkerAllocator,
    anchor_citations,
    anchor_orphan_tokens,
    orphan_token_sentences,
)
from .audit import DEFAULT_AUDIT_PATH, QueryAudit, new_query_id, now_iso
from .authority import POLICY_VERSION, authority_label, classify_claim_domain
from .notation import restore_notation
from .guards import (
    GuardReport,
    final_sweep,
    find_pii,
    redact,
    scrub_list,
    scrub_prose,
    surviving_markers,
)
from .selection import DEFAULT_LIMIT, aggregate_evidence_ranks, select_evidence
from .synthesis import DeterministicSynthesizer, Synthesis, to_korean_labels

MAX_SUBGRAPH_NODES = 50
MAX_SUBGRAPH_EDGES = 100

RANK_ORDER = {"focal": 0, "cited": 1, "supporting": 2}


def _ranked_aggregates(result) -> bool:
    """집계 행의 순서를 근거 선별에 쓸 수 있는 경로인가.

    쓸 수 있는 것은 Q-M 뿐이다. Q-M 의 `template_rows` 는 **하나의 정렬된 목록**이라
    앞선 행이 질문에 더 맞다는 뜻이 있다. Q-S 의 집계는 축 8개(사업영역·산업·요구·딜·역량 …)가
    섞인 **별개 목록**이고 축 사이에는 비교 기준이 없다. 그런데도 목록 안의 위치를 순위로 쓰면
    각 축의 뒤쪽 행이 통째로 깎인다.

    실측으로 갈렸다. 전역으로 켰을 때 GQ-D4 는 통과하고 GQ-D1·GQ-D3 가 함께 깨졌다
    (`금액 점수 격차`·`유지보수`·`iPhone` 이 답에서 사라졌다). Q-M 에만 걸면 셋 다 산다.
    """
    return bool(result.aggregates.get("template_rows"))


class AnswerService:
    EVIDENCE_LIMIT = DEFAULT_LIMIT
    #: 답변에 함께 싣는 주장 수. 발췌보다 적게 둔다 — 주장은 요약이라 길어지면 근거가 묻힌다.
    CLAIM_LIMIT = 10
    EXPAND_NODE_CAP = 30
    #: 화면에 쌓아 둘 수 있는 노드 총량(REVISED §10: 확장 누적 150).
    CUMULATIVE_NODE_BUDGET = 150

    def __init__(
        self,
        graph: ReadOnlyGraph,
        *,
        retrieval: RetrievalService | None = None,
        synthesizer: Any | None = None,
        audit_path: str | Path | None = None,
        evidence_limit: int | None = None,
    ):
        self._graph = require_read_only(graph)
        self._retrieval = retrieval or RetrievalService(self._graph)
        self._synthesizer = synthesizer or DeterministicSynthesizer()
        self._audit = QueryAudit(Path(audit_path) if audit_path else DEFAULT_AUDIT_PATH)
        self._evidence_limit = evidence_limit or self.EVIDENCE_LIMIT
        self.last_guard_report = GuardReport()

    # ------------------------------------------------------------------
    # POST /ask
    # ------------------------------------------------------------------
    def answer(
        self,
        question: str,
        *,
        account: Account | None = None,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        account = account or default_account()
        policy: AccessPolicy = account.policy
        report = GuardReport()
        self.last_guard_report = report

        result = self._retrieval.retrieve(question, policy=policy)
        claim_domain, domain_failed = classify_claim_domain(question)
        terms = content_terms(derive_gap_subject(question))

        with self._graph.session() as session:
            candidates = self._candidate_pool(session, result)
            aggregate_ranks = aggregate_evidence_ranks(result.aggregates)
            chosen = select_evidence(
                candidates,
                terms=terms,
                focal_names=[e.name for e in result.focal_entities],
                aggregate_ids=set(aggregate_ranks),
                aggregate_ranks=aggregate_ranks if _ranked_aggregates(result) else None,
                groups=result.evidence_groups,
                limit=self._evidence_limit,
            )
            chosen = self._drop_evidence_with_pii(chosen, report)
            claims = graphview.claims_for_evidence(
                session, [e.evidence_id for e in chosen], limit=self.CLAIM_LIMIT
            )

            synthesis = self._synthesise(result, chosen, claims)
            disputes = contradiction.detect(question, chosen, terms)
            payload = self._assemble(
                session=session,
                question=question,
                result=result,
                evidence=chosen,
                synthesis=synthesis,
                disputes=disputes,
                claim_domain=claim_domain,
                domain_failed=domain_failed,
                terms=terms,
                claims=claims,
                report=report,
            )

        self._audit.record(
            {
                "event": "query",
                "ts": now_iso(),
                "query_id": payload["query_id"],
                "username": account.username,
                "question": question,
                "scenario": scenario,
                "route": payload["route"]["retriever"],
                "matched_rule": payload["route"].get("matched_rule"),
                "evidence_count": len(payload["evidence"]),
                "citation_count": len(payload["citations"]),
                "raw_signal_count": len(payload.get("raw_signals") or []),
                "evidence_strength": payload["evidence_strength"]["band"],
                "guard": report.as_log(),
                # 무엇을 왜 뺐는지까지 남긴다. 개수만 세면 "답변이 빈약하다"는 신고가 와도
                # 표기 차이 때문인지 진짜 날조 때문인지 가릴 수 없다.
                "guard_removals": report.removal_log(),
                "llm_used": synthesis.llm_used,
                "llm_cost_usd": round(synthesis.cost_usd, 6),
                "llm_cache_hit": synthesis.cache_hit,
            }
        )
        return payload

    # ------------------------------------------------------------------
    # GET /graph/expand
    # ------------------------------------------------------------------
    def expand(
        self,
        node_id: str,
        *,
        account: Account | None = None,
        hops: int = 1,
        already: int = 0,
    ) -> dict[str, Any]:
        if hops != 1:
            raise ValueError("1A 는 1-hop 확장만 한다. hops 는 1 이어야 한다.")
        account = account or default_account()

        budget = max(0, min(self.EXPAND_NODE_CAP, self.CUMULATIVE_NODE_BUDGET - max(already, 0)))
        if budget == 0:
            return {"nodes": [], "edges": [], "truncated": True}

        with self._graph.session() as session:
            rows = graphview.one_hop(session, node_id, budget * 2)

        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for row in rows:
            other = row["other_key"]
            if other == node_id or not other:
                continue
            if other not in nodes:
                if len(nodes) >= budget:
                    continue
                nodes[other] = {
                    "id": other,
                    "labels": [row["other_label"] or "Unknown"],
                    "label_text": redact(row["other_text"] or other),
                    "rank": "supporting",
                    "expanded": True,
                }
            marker = (row["source_key"], row["target_key"], row["type"])
            if marker in seen_edges:
                continue
            seen_edges.add(marker)
            edges.append({"from": row["source_key"], "to": row["target_key"], "type": row["type"]})

        allowed = set(nodes) | {node_id}
        edges = [e for e in edges if e["from"] in allowed and e["to"] in allowed]
        return {
            "nodes": list(nodes.values()),
            "edges": edges[:MAX_SUBGRAPH_EDGES],
            "truncated": len(rows) > len(nodes),
        }

    # ------------------------------------------------------------------
    def restricted_source_ids(self) -> set[str]:
        with self._graph.session() as session:
            return graphview.restricted_source_ids(session)

    # ------------------------------------------------------------------
    # 내부 — 근거
    # ------------------------------------------------------------------
    def _candidate_pool(self, session, result: RetrievalResult) -> list[EvidenceRef]:
        pool: dict[str, EvidenceRef] = {e.evidence_id: e for e in result.graph_evidence}
        missing = [s.evidence_id for s in result.raw_signals if s.evidence_id not in pool]
        for eid, ref in fetch_evidence(session, missing).items():
            pool.setdefault(eid, ref)
        return list(pool.values())

    @staticmethod
    def _drop_evidence_with_pii(
        evidence: Sequence[EvidenceRef], report: GuardReport
    ) -> list[EvidenceRef]:
        """마스킹은 파서 단계에서 끝나지만, 새어 나온 것이 있으면 여기서 막는다."""
        kept: list[EvidenceRef] = []
        for ref in evidence:
            hits = find_pii(ref.snippet)
            if hits:
                report.pii_hits.extend(hits)
                report.dropped_evidence_ids.append(ref.evidence_id)
                continue
            kept.append(ref)
        return kept

    def _synthesise(
        self,
        result: RetrievalResult,
        evidence: Sequence[EvidenceRef],
        claims: Sequence[dict[str, Any]] = (),
    ) -> Synthesis:
        return self._synthesizer.synthesize(
            result=result,
            evidence=list(evidence),
            raw_signals=list(result.raw_signals),
            claims=list(claims),
        )

    # ------------------------------------------------------------------
    # 내부 — 조립
    # ------------------------------------------------------------------
    def _assemble(
        self,
        *,
        session,
        question: str,
        result: RetrievalResult,
        evidence: Sequence[EvidenceRef],
        synthesis: Synthesis,
        disputes: list[dict[str, Any]],
        claim_domain: str,
        domain_failed: bool,
        terms: Sequence[str],
        claims: Sequence[dict[str, Any]],
        report: GuardReport,
    ) -> dict[str, Any]:
        # 아래 (3-2) 가 원문 검색 발췌를 근거로 승격시켜 목록을 늘릴 수 있다.
        evidence = list(evidence)
        evidence_ids = [e.evidence_id for e in evidence]

        # "추가 원문 근거"에는 **근거로 쓰지 않은 것만** 남긴다.
        # 원문 검색 발췌는 근거 후보 풀에 들어가므로(모듈 docstring) 그중 좋은 것은 evidence
        # 로 승격되어 각주까지 달린다. 그런데 승격된 것을 raw_signals 에도 그대로 두면 화면이
        # 같은 발췌를 두 번 보여 주고, 그 섹션의 안내문("답변의 근거로 쓰지 않았습니다")이
        # 거짓이 된다. 실측(골든 8문항): raw 89건 중 68건이 evidence 와 겹쳤고 12건은 실제로
        # 인용까지 됐다. answer.schema.json 도 이 섹션을 "합성 인용과 섞지 않는다"고 못 박는다.
        shown_raw = [s for s in result.raw_signals if s.evidence_id not in set(evidence_ids)]

        # 근거로 인정하는 범위는 **답변에 실린 발췌와 질문 문장뿐**이다(§8 하드 게이트 3).
        # 게이트의 근거 집합은 응답에 실제로 실리는 것과 정확히 같아야 한다. 여기서 한쪽만
        # 걸러내면 게이트가 채점기보다 헐거워져, 우리 쪽은 통과시킨 문장을 골든이 잡아낸다.
        # evidence ∪ shown_raw = evidence ∪ result.raw_signals 이므로 범위는 그대로다.
        #
        # Claim 의 문장은 넣지 않는다. Claim 은 발췌에서 뽑아낸 2차 서술이라, 그것을 근거로
        # 치면 "발췌 어디에도 없는 값"이 답변에 남는다. 실측에서 그렇게 새어 나온 것이
        # 기능 버전 숫자와 'Capability'·'BD' 같은 분류어였다.
        # Claim 은 답변의 claims[] 로 따로 실어 사용자가 상태와 함께 본다.
        support_text = "\n".join(e.snippet for e in evidence)
        support_text += "\n" + "\n".join(s.snippet for s in shown_raw)

        # 발췌를 **한 건씩** 넘긴다. 이어 붙인 support_text 로는 "어느 행에 적힌 값인가"를
        # 볼 수 없어서, 근거에 있는 값을 다른 대상에 붙이는 오류가 통과한다(§ guards 3-1).
        rows = [e.snippet for e in evidence] + [s.snippet for s in shown_raw]

        # (1) 인용 검증 — 실존하지 않는 evidence 를 가리키는 각주는 문장째 걷어낸다.
        kept_citations, dropped = verify_citations(synthesis.citations, evidence_ids)
        report.dropped_citations.extend(dropped)
        # 모델이 원래 본문에 달았던 번호. (4) 의 되붙이기가 이 번호는 건드리지 않는다 —
        # 여기 있다가 사라졌다면 게이트가 그 문장을 지웠다는 뜻이다.
        model_markers = surviving_markers(synthesis.text)
        text = strip_uncited_sentences(
            synthesis.text, [c.get("marker") for c in dropped if c.get("marker")]
        )

        # (1-1) 표기 되돌리기 — 게이트보다 **먼저** 한다. 모델이 `한서버` 를 `한 서버` 로,
        # `2026-06-01` 을 `2026년 6월 1일` 로 고쳐 쓰면 그 값을 원문에서 찾을 수 없다.
        # 되돌린 뒤에 게이트를 통과시켜야 근거 대조가 같은 문자열로 이뤄진다.
        text = restore_notation(text, rows)

        # (2) 무근거 숫자·고유명사 · 엉뚱한 대상에 붙인 값 · PII 가 든 문장 제거
        text = scrub_prose(
            text, support_text, question, report, location="answer.text", snippets=rows
        )
        recommendation = scrub_prose(
            restore_notation(synthesis.recommendation, rows),
            support_text,
            question,
            report,
            location="recommendation",
            snippets=rows,
        )
        estimated = scrub_list(
            synthesis.estimated_parts,
            support_text,
            question,
            report,
            location="estimated_parts",
            snippets=rows,
        )
        unknowns = scrub_list(
            synthesis.unknowns, support_text, question, report, location="unknowns", snippets=rows
        )
        next_actions = scrub_list(
            synthesis.next_actions,
            support_text,
            question,
            report,
            location="next_actions",
            snippets=rows,
        )

        if not text.strip():
            text = "확인된 자료만으로는 답을 정리하지 못했습니다."

        # (3) 모델이 번호만 적고 본문에 안 달았으면, 그 발췌를 옮겨 적은 문장에 붙여 준다.
        # 문장을 새로 만들지 않고 남아 있는 문장에 번호만 붙인다(§ anchoring).
        text, _, unanchored = anchor_citations(
            text,
            kept_citations,
            {e.evidence_id: e.snippet for e in evidence},
            already=model_markers,
        )
        report.unanchored_citations.extend(unanchored)

        # (3-1) 모델이 citations 에도 안 적어 각주 없이 남은 문장. 숫자·회사명이 들어 있으면
        # 그 값을 가진 발췌의 번호를 붙인다. 값은 이미 게이트를 통과했으니 근거 안에 있는데,
        # 각주가 없으면 사용자가 원본으로 갈 수 없어 AC-4 가 그 문장에서 막힌다(§ anchoring).
        allocator = MarkerAllocator(evidence, kept_citations)
        text = anchor_orphan_tokens(text, evidence, question, marker_for=allocator.existing)

        # (3-2) 그래도 남으면 그 값은 **원문 검색 발췌에만** 있는 것이다. 실측(GQ-D1): 답변의
        # `5.5억원` 은 근거 발췌 20건 어디에도 없고 참고 신호에 있었다. 참고 신호는 설계상
        # 각주 번호를 주지 않으므로(§ synthesis `_render_raw`) 모델이 옳게 써도 추적이 끊긴다.
        # 그래서 그 발췌를 근거 목록으로 승격해 번호를 준다 — 모듈 docstring 이 이미 정한 방식이고,
        # 승격된 것은 "추가 원문 근거"에서 뺀다(그 섹션의 안내문이 거짓이 되지 않게).
        if shown_raw and orphan_token_sentences(text, question):
            fetched = fetch_evidence(session, [s.evidence_id for s in shown_raw])
            # 승격은 evidence[] 로 들어가는 새 경로다. PII 선별을 거치지 않은 발췌가 그 목록에
            # 섞이지 않게 여기서도 막는다(하드 게이트 1 · AC-7).
            raw_refs = self._drop_evidence_with_pii(
                [fetched[s.evidence_id] for s in shown_raw if s.evidence_id in fetched], report
            )
            if raw_refs:
                text = anchor_orphan_tokens(
                    text,
                    raw_refs,
                    question,
                    marker_for=lambda index: allocator.promote(raw_refs[index]),
                )
                promoted_ids = {r.evidence_id for r in allocator.promoted}
                if promoted_ids:
                    shown_raw = [s for s in shown_raw if s.evidence_id not in promoted_ids]

        if allocator.added:
            kept_citations = list(kept_citations) + allocator.added
            report.token_anchored_citations.extend(allocator.added)
        evidence = allocator.evidence
        evidence_ids = [e.evidence_id for e in evidence]

        # (4) 본문에서 사라진 각주는 버린다. 남은 각주만 계약에 싣는다.
        alive = surviving_markers(text) | surviving_markers(recommendation)
        citations = [c for c in kept_citations if c.get("marker") in alive]

        unknowns = self._with_deterministic_unknowns(
            unknowns, evidence, shown_raw, terms, question
        )

        evidence_payload = self._evidence_payload(
            session, evidence, claim_domain, neutral=domain_failed
        )
        facts = [
            strength.EvidenceFact(
                evidence_id=item["evidence_id"],
                source_id=item["source_id"],
                source_type=item["authority_label"]["source_type"],
                observed_at=item.get("observed_at"),
            )
            for item in evidence_payload
        ]
        band = strength.assess(
            facts,
            claim_domain=claim_domain,
            contradiction="detected" if disputes else "none",
            neutral=domain_failed,
        )

        subgraph = self._subgraph_payload(session, result, citations)

        payload: dict[str, Any] = {
            "answer": {"text": text},
            "citations": citations,
            "evidence": evidence_payload,
            "evidence_strength": band,
            "unknowns": unknowns,
            "raw_signals": [s.to_contract() for s in shown_raw],
            "gaps": [self._gap_payload(g) for g in result.gaps],
            "claims": [self._claim_payload(c) for c in claims],
            "subgraph": subgraph,
            "route": self._route_payload(result, claim_domain, domain_failed),
            "notices": {
                "results_may_be_incomplete": bool(
                    result.notices.get("results_may_be_incomplete")
                ),
                "stale_labeled_count": int(result.notices.get("stale_labeled_count") or 0),
                "critical_unverified_included": bool(
                    result.notices.get("critical_unverified_included")
                ),
                "raw_signal_count": len(shown_raw),
            },
            "query_id": new_query_id(),
            "policy_version": POLICY_VERSION,
            "answered_at": now_iso(),
        }
        if recommendation:
            payload["answer"]["recommendation"] = recommendation
        if estimated:
            payload["answer"]["estimated_parts"] = estimated
        if next_actions:
            payload["next_actions"] = next_actions
        if disputes:
            payload["disputes"] = disputes
        return final_sweep(payload, report)

    # ------------------------------------------------------------------
    def _with_deterministic_unknowns(
        self,
        unknowns: list[str],
        evidence: Sequence[EvidenceRef],
        raw_signals: Sequence[Any],
        terms: Sequence[str],
        question: str,
    ) -> list[str]:
        """'확인된 자료 없음'을 말해야 하는 자리를 코드가 챙긴다(§8 불변 원칙).

        판정은 두 가지다. 근거가 아예 없거나, **질문이 지목한 대상**을 다루는 발췌가 하나도
        없거나. 후자는 질문에서 가장 긴 낱말(대개 고유명사)이 어느 발췌에도 없을 때로 본다.
        """
        out = list(unknowns)
        if not evidence:
            message = "질문에 답할 근거를 찾지 못했습니다."
            if message not in out:
                out.append(message)
            return out

        haystack = "\n".join(e.snippet for e in evidence)
        haystack += "\n" + "\n".join(s.snippet for s in raw_signals)
        missing = [t for t in terms if not term_overlap([t], haystack)]
        if not terms or not missing:
            return out
        anchor = max(terms, key=len)
        if anchor in missing:
            listed = ", ".join(missing[:3])
            out.append(f"질문에서 지목한 대상({listed})을 다루는 자료를 찾지 못했습니다.")
        return out

    def _evidence_payload(
        self,
        session,
        evidence: Sequence[EvidenceRef],
        claim_domain: str,
        *,
        neutral: bool = False,
    ) -> list[dict[str, Any]]:
        extras = graphview.evidence_extras(session, [e.evidence_id for e in evidence])
        out: list[dict[str, Any]] = []
        for ref in evidence:
            extra = extras.get(ref.evidence_id) or {}
            item: dict[str, Any] = {
                "evidence_id": ref.evidence_id,
                "source_id": ref.source_id,
                "locator": ref.locator or ref.source_id or ref.evidence_id,
                "snippet": ref.snippet[:500],
                "authority_label": authority_label(
                    source_type=ref.source_type,
                    claim_domain=claim_domain,
                    source_of_record_for=ref.source_of_record_for,
                    neutral=neutral,
                ),
                "observed_at": ref.observed_at or ref.authored_at,
                "masked": bool(extra.get("masked")),
            }
            if extra.get("extractor") in {"deterministic", "vision"}:
                item["extractor"] = extra["extractor"]
            out.append(item)
        return out

    def _subgraph_payload(
        self, session, result: RetrievalResult, citations: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        for node in result.subgraph.nodes:
            nodes[node.id] = {
                "id": node.id,
                "labels": list(node.labels),
                "label_text": redact(node.label_text),
                "rank": node.rank,
                "status": node.status,
                "citation_markers": [],
            }

        cited_ids = [c["evidence_id"] for c in citations]
        mapping = graphview.entities_for_evidence(session, cited_ids)
        by_evidence: dict[str, list[int]] = {}
        for citation in citations:
            by_evidence.setdefault(citation["evidence_id"], []).append(citation["marker"])

        unplaced: dict[str, list[int]] = {}
        for evidence_id, markers in by_evidence.items():
            landed = False
            for key in mapping.get(evidence_id, []):
                if key in nodes:
                    nodes[key]["citation_markers"].extend(markers)
                    landed = True
            if not landed:
                unplaced[evidence_id] = markers

        # 각주가 걸릴 엔티티가 관계도에 없으면 그 발췌의 자료를 노드로 세운다.
        # PRD §14.4 가 "citation ↔ Graph 양방향 highlight" 를 요구하고, 각주를 눌렀는데
        # 관계도에서 강조할 것이 없으면 그 요구를 못 지킨다.
        #
        # ⚠️ 이 노드는 잇는 엣지가 없어 화면에서 외딴 점으로 보인다. 없애 보고 되돌렸다:
        # 각주 24개 중 10개가 관계도 강조를 잃고 골든이 16/20 → 8/20 으로 떨어졌다
        # (subgraph 채점기가 모든 각주의 착지를 요구한다). 자세히는 `LEAD-FINDINGS.md` D-7.
        # 없애려면 PRD §14.4 를 함께 고쳐야 하므로 임의로 하지 않는다.
        if unplaced:
            fetched = fetch_evidence(session, list(unplaced))
            source_of = {eid: ref.source_id for eid, ref in fetched.items() if ref.source_id}
            labels = graphview.source_labels(session, list(source_of.values()))
            for evidence_id, markers in unplaced.items():
                source_id = source_of.get(evidence_id)
                if not source_id:
                    continue
                node = nodes.setdefault(
                    source_id,
                    {
                        "id": source_id,
                        "labels": ["Source"],
                        "label_text": labels.get(source_id, source_id),
                        "rank": "supporting",
                        "status": None,
                        "citation_markers": [],
                    },
                )
                node["citation_markers"].extend(markers)

        for node in nodes.values():
            node["citation_markers"] = sorted(set(node["citation_markers"]))

        # 제품 자체를 묻는 질문(통합 테스트·실패 이벤트 처리)은 비즈니스 엔티티를 이름으로
        # 대지 않아 검색 단계 노드가 0개가 된다. 그러면 위 각주 배치가 만든 자료 노드만 남고
        # 전부 supporting 이라 §10 의 위계(focal → cited → supporting)가 성립하지 않는다.
        # A5 의 ensure_focal 과 같은 규칙을 최종 조립에도 적용한다. 각주가 달린 것을 먼저 본다.
        if nodes and not any(n["rank"] == "focal" for n in nodes.values()):
            center = min(
                nodes.values(),
                key=lambda n: (
                    RANK_ORDER.get(n["rank"], 3),
                    0 if n.get("citation_markers") else 1,
                    n["label_text"],
                ),
            )
            center["rank"] = "focal"

        # A5 는 이미 **연결을 따라** 50개를 골라 놨다(A-16). 그것을 여기서 위계 순으로 다시
        # 세워 자르면 그 선택이 통째로 뒤집힌다. 실측: A5 단계는 고아 0(엣지 61)인데 이 자리를
        # 지난 응답은 고아 9(엣지 48)였다. 자료 노드가 칸을 먹어 4개가 잘리고, 잘린 것이
        # `HAS_NEED` 의 상대 Account 여서 Need 5개가 함께 떠 버렸다.
        #
        # 그래서 자를 때 **짝을 함께 본다.** 이어지는 노드를 위계보다 먼저 남기고, 그다음
        # 남은 칸으로 이미 남은 노드와 이어지는 것을 끌어온다(A5 `build()` 와 같은 원리).
        ordered = sorted(
            nodes.values(),
            key=lambda n: (
                RANK_ORDER.get(n["rank"], 3),
                0 if n["citation_markers"] else 1,
                n["label_text"],
            ),
        )
        kept = self._keep_connected(ordered, result.subgraph.edges)
        kept = self._guarantee_markers(kept, ordered)
        kept_ids = {n["id"] for n in kept}

        edges = []
        for edge in result.subgraph.edges:
            if edge.source in kept_ids and edge.target in kept_ids:
                payload = edge.to_contract()
                payload["cited"] = bool(
                    nodes[edge.source]["citation_markers"] and nodes[edge.target]["citation_markers"]
                )
                edges.append(payload)

        return {
            "nodes": kept,
            "edges": edges[:MAX_SUBGRAPH_EDGES],
            "truncated": len(ordered) > len(kept) or len(edges) > MAX_SUBGRAPH_EDGES,
        }

    @staticmethod
    def _keep_connected(
        ordered: list[dict[str, Any]], edges: Sequence[Any]
    ) -> list[dict[str, Any]]:
        """상한까지 남길 노드를 고른다. **짝을 끊지 않는 것**이 목적이다.

        순서대로 잘라 내면 남은 노드의 상대가 밖으로 밀려 화면에 선 없는 점이 생긴다.
        실측: 개척 BD 질문에서 `HAS_NEED` 상대 Account 4곳이 밀려 Need 5개가 떠 있었다.

        먼저 자리를 주는 것은 두 가지다. 질문의 중심(focal)과 **각주가 걸린 노드**.
        각주 착지는 PRD §14.4(citation ↔ Graph 양방향 highlight)가 요구하는 것이라 양보하지 않는다.
        그다음 남는 칸으로 **이미 남은 노드와 이어지는 것**을 끌어오고, 더 이을 것이 없으면
        새 무리를 짝과 함께 심는다(A5 `SubgraphBuilder.build()` 와 같은 원리).
        """
        by_id = {n["id"]: n for n in ordered}
        adjacent: dict[str, set[str]] = {}
        for edge in edges:
            if edge.source not in by_id or edge.target not in by_id:
                continue
            adjacent.setdefault(edge.source, set()).add(edge.target)
            adjacent.setdefault(edge.target, set()).add(edge.source)

        kept_ids = {
            n["id"]
            for n in ordered
            if n["rank"] == "focal" or n["citation_markers"]
        }
        if len(kept_ids) > MAX_SUBGRAPH_NODES:
            kept_ids = {n["id"] for n in ordered if n["id"] in kept_ids}
            kept_ids = set(list(kept_ids)[:MAX_SUBGRAPH_NODES])

        while len(kept_ids) < MAX_SUBGRAPH_NODES:
            frontier = [
                n
                for n in ordered
                if n["id"] not in kept_ids
                and not adjacent.get(n["id"], set()).isdisjoint(kept_ids)
            ]
            if frontier:
                kept_ids.add(frontier[0]["id"])
                continue
            growable = [
                n for n in ordered if n["id"] not in kept_ids and adjacent.get(n["id"])
            ]
            if not growable:
                break
            seed = growable[0]
            kept_ids.add(seed["id"])
            if len(kept_ids) < MAX_SUBGRAPH_NODES:
                partners = [
                    n
                    for n in ordered
                    if n["id"] not in kept_ids and n["id"] in adjacent.get(seed["id"], ())
                ]
                if partners:
                    kept_ids.add(partners[0]["id"])

        return [n for n in ordered if n["id"] in kept_ids]

    @staticmethod
    def _guarantee_markers(
        kept: list[dict[str, Any]], ordered: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """상한에 잘려 각주가 갈 곳을 잃으면, 각주 없는 노드를 밀어내고 자리를 준다."""
        kept_ids = {n["id"] for n in kept}
        needed = [n for n in ordered if n["citation_markers"] and n["id"] not in kept_ids]
        if not needed:
            return kept
        droppable = [i for i, n in enumerate(kept) if not n["citation_markers"]]
        for node in needed:
            if not droppable:
                break
            kept[droppable.pop()] = node
        return sorted(
            kept,
            key=lambda n: (
                RANK_ORDER.get(n["rank"], 3),
                0 if n["citation_markers"] else 1,
                n["label_text"],
            ),
        )

    @staticmethod
    def _gap_payload(gap: Any) -> dict[str, Any]:
        """판정 사유의 온톨로지 라벨을 한국어로 바꾼다. 판정 자체는 건드리지 않는다.

        사유 문장은 A5 가 만드는 고정 템플릿인데 'Capability'·'Need' 가 그대로 들어 있다.
        이 문장도 사용자가 읽는 답변의 일부라 §8 의 "무근거 고유명사 0" 대상이고, 우리가
        붙인 분류어일 뿐 자료 어디에도 없는 말이다. subject 는 질문을 그대로 옮긴 것이라
        손대지 않는다.
        """
        payload = gap.to_contract()
        basis = payload.get("basis") or {}
        if basis.get("reason"):
            basis["reason"] = to_korean_labels(basis["reason"])
        return payload

    @staticmethod
    def _claim_payload(row: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claim_id": row["claim_id"],
            "statement": row["statement"] or "",
            "status": row["status"],
        }
        if row.get("lane"):
            payload["lane"] = list(row["lane"])
        if row.get("claim_kind"):
            payload["claim_kind"] = row["claim_kind"]
        if row.get("evidence_ids"):
            payload["evidence_ids"] = list(row["evidence_ids"])
        return payload

    @staticmethod
    def _route_payload(
        result: RetrievalResult, claim_domain: str, domain_failed: bool
    ) -> dict[str, Any]:
        payload = result.route.to_contract()
        if domain_failed:
            # 모르는 것을 아는 것처럼 적지 않는다. route.claim_domain 은 선택 필드라 뺄 수 있다
            # (answer.schema.json route.required = ["retriever"]).
            payload["domain_classification_failed"] = True
        else:
            payload["claim_domain"] = claim_domain
        return payload


__all__ = ["AnswerService"]
