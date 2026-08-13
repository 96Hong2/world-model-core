// 근거 상세 서랍. 근거 추적의 종착점이다.
//
// 무엇을 열든 마지막에는 네 가지가 한 화면에 있어야 한다:
//   발췌 · 어느 자료 · 원본 어디 · **원본으로 가는 버튼**
//
// 마지막 항목이 개선 전에 없었다. 파일 경로를 글자로만 보여 주고 끝났다. 지금은
// OriginBlock 이 자료 종류에 맞는 길(새 탭 · 원문 보기 · 경로 복사)을 고른다.

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { AnswerPayload, Evidence, RawSignal } from "../types/answer";
import type { RenderGraph, RenderNode } from "../graph/adapter";
import { EntityIcon } from "../graph/icons";
import { findSource, sourceTitle } from "../lib/sources";
import { readLocator } from "../lib/locator";
import {
  CLAIM_DOMAIN_KO,
  STATUS_KO,
  TIER_KO,
  sourceTypeKo,
} from "../lib/labels";
import { OriginBlock } from "./OriginBlock";
import type { KnowledgeSpot } from "./OriginBlock";
import { SourceViewer } from "./SourceViewer";
import { Fold } from "./Fold";
import { IconClose } from "../shell/icons";

export type DrawerTarget =
  | { kind: "evidence"; id: string }
  | { kind: "raw"; id: string }
  | { kind: "node"; id: string };

interface Props {
  target: DrawerTarget;
  answer: AnswerPayload;
  graph: RenderGraph;
  onClose: () => void;
  onOpen: (target: DrawerTarget) => void;
  /** 근거를 열면 답변 각주·그래프도 같이 그 번호로 맞춘다 */
  onFocusMarker: (marker: number | null) => void;
}

type OpenViewer = (sourceId: string, locator: string) => void;

function statusTone(status?: string | null): string {
  switch (status) {
    case "VERIFIED":
      return "verified";
    case "CRITICAL":
      return "critical";
    case "DISPUTED":
      return "conflict";
    default:
      return "unknown";
  }
}

/**
 * 발췌. 긴 것은 접는다.
 *
 * 실측에서 발췌 하나가 911px 이었다(서랍 본문은 822px). 발췌가 화면을 다 먹으면 바로 아래
 * 「얼마나 믿을 수 있나」가 스크롤 밖으로 밀려, 정작 물어본 것이 안 보인다.
 */
function Excerpt({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 420;
  return (
    <>
      <p className="excerpt" data-clamped={long && !open}>
        {text}
      </p>
      {long && (
        <button
          type="button"
          className="btn"
          data-variant="quiet"
          data-size="sm"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "발췌 접기" : `발췌 전체 보기 (${text.length}자)`}
        </button>
      )}
    </>
  );
}

/** 1~5등급을 사람 말로. 등급 숫자만으로는 좋은 쪽인지 나쁜 쪽인지 알 수 없다. */
const TIER_VERDICT: Record<number, { head: string; why: string }> = {
  1: {
    head: "믿을 만합니다",
    why: "이 주제를 정하는 정본 자료입니다. 다른 자료와 다르면 이쪽이 맞습니다.",
  },
  2: {
    head: "대체로 믿을 만합니다",
    why: "공식 문서이지만 이 주제의 정본은 아닙니다. 정본이 따로 있으면 그쪽이 우선입니다.",
  },
  3: {
    head: "참고할 만합니다",
    why: "사람이 남긴 기록입니다. 그때의 판단이라 지금과 다를 수 있습니다.",
  },
  4: {
    head: "조심해서 보세요",
    why: "대화나 메모에서 나온 말입니다. 확정된 사실로 쓰기 전에 확인이 필요합니다.",
  },
  5: {
    head: "확인이 필요합니다",
    why: "출처의 권위를 판단할 수 없는 자료입니다.",
  },
};

/**
 * 이 근거를 얼마나 믿을 수 있나.
 *
 * 예전에는 `T1` 배지와 칸 막대만 있었다. 맥락을 아는 사람에게는 충분했지만, 처음 보는
 * 사람은 T1 이 좋은 쪽인지 나쁜 쪽인지, 막대가 몇 칸이어야 좋은 것인지 알 수 없었다.
 * 그래서 **판정 한 문장을 맨 앞에 놓고**, 등급은 "5등급 중 1등급"처럼 분모까지 적는다.
 *
 * 등급 자체는 지우지 않는다(PRD §14.4 의 권위 등급이고 노드 크기에는 쓰지 않는다).
 */
function Authority({ ev }: { ev: Evidence }) {
  const a = ev.authority_label;
  const tier = Number(a.tier.replace("T", "")) || 5;
  const verdict = TIER_VERDICT[tier] ?? TIER_VERDICT[5];
  const tone = tier <= 2 ? "verified" : tier === 3 ? "accent" : "conflict";

  return (
    <div className="authority">
      <p className="trust-head" data-tone={tone}>
        {verdict.head}
      </p>
      <p className="trust-why">{verdict.why}</p>

      <dl className="trust-rows">
        <dt>자료 등급</dt>
        <dd>
          <span className="trust-grade">
            5등급 중 <b>{tier}등급</b>
          </span>
          <span className="trust-scale" aria-hidden="true">
            {[1, 2, 3, 4, 5].map((i) => (
              <i key={i} className={i <= 6 - tier ? "on" : ""} />
            ))}
          </span>
          <span className="meta">
            1등급이 가장 믿을 만합니다 · {TIER_KO[a.tier] ?? a.tier}
          </span>
        </dd>

        <dt>어떤 자료인가</dt>
        <dd>{sourceTypeKo(a.source_type)}</dd>

        <dt>무엇을 판단할 때 쓰나</dt>
        <dd>{CLAIM_DOMAIN_KO[a.claim_domain] ?? a.claim_domain}</dd>

        {a.source_of_record_for && (
          <>
            <dt>이 자료가 정본인 범위</dt>
            <dd>{a.source_of_record_for}</dd>
          </>
        )}

        <dt>글자를 어떻게 읽었나</dt>
        <dd>
          {ev.extractor === "vision"
            ? "이미지에서 읽어냈습니다. 글자를 잘못 읽었을 수 있습니다."
            : "파일에 적힌 글자를 그대로 옮겼습니다."}
        </dd>

        <dt>개인정보</dt>
        <dd>
          {ev.masked
            ? "이름·번호를 가린 뒤 저장했습니다."
            : "가릴 개인정보가 없었습니다."}
        </dd>

        {a.caveat && (
          <>
            <dt>주의할 점</dt>
            <dd>{a.caveat}</dd>
          </>
        )}
      </dl>

      <p className="authority-note">
        이 등급은 <b>자료를 얼마나 믿을지</b>에 대한 것입니다. 관계도에서 노드가
        얼마나 중요한지와는 다른 이야기라, 노드 크기에는 쓰지 않습니다.
      </p>
    </div>
  );
}

function EvidenceBody({
  ev,
  onOpenViewer,
  knowledge,
  onFocusNode,
}: {
  ev: Evidence;
  onOpenViewer: OpenViewer;
  knowledge?: KnowledgeSpot;
  onFocusNode?: (id: string) => void;
}) {
  return (
    <>
      <div className="drawer-block">
        <h3>발췌</h3>
        <Excerpt text={ev.snippet} />
      </div>

      {/* 믿을 수 있나를 위치보다 먼저 놓는다. 발췌를 읽은 다음 궁금한 것이 그것이다. */}
      <div className="drawer-block">
        <h3>이 근거를 얼마나 믿을 수 있나</h3>
        <Authority ev={ev} />
      </div>

      <div className="drawer-block">
        <h3>어디서 왔고 어디에 쓰였나</h3>
        <OriginBlock
          sourceId={ev.source_id}
          locator={ev.locator}
          sourceType={ev.authority_label.source_type}
          onOpenViewer={onOpenViewer}
          knowledge={knowledge}
          onFocusNode={onFocusNode}
        />
      </div>

      <div className="drawer-block">
        <Fold title="식별자와 시점">
          <dl className="props">
            <dt>근거 ID</dt>
            <dd className="mono">{ev.evidence_id}</dd>
            <dt>위치 표기</dt>
            <dd className="mono">{ev.locator}</dd>
            {ev.observed_at && (
              <>
                <dt>관측 시점</dt>
                <dd>{ev.observed_at}</dd>
              </>
            )}
            <dt>정책 버전</dt>
            <dd className="mono">{ev.authority_label.policy_version}</dd>
          </dl>
        </Fold>
      </div>
    </>
  );
}

function RawBody({
  signal,
  onOpenViewer,
}: {
  signal: RawSignal;
  onOpenViewer: OpenViewer;
}) {
  return (
    <>
      <div className="drawer-block">
        <p className="notice-hint">
          구조화된 지식에는 아직 반영되지 않은 원문 신호입니다. 답변의 인용
          근거로 쓰지 않았습니다.
        </p>
        <p className="excerpt" style={{ marginTop: "var(--sp-3)" }}>
          {signal.snippet}
        </p>
      </div>

      <div className="drawer-block">
        <h3>어디서 왔나</h3>
        <OriginBlock
          sourceId={signal.source_id}
          locator={signal.locator}
          onOpenViewer={onOpenViewer}
        />
      </div>

      <div className="drawer-block">
        <dl className="props">
          {signal.match_terms?.length ? (
            <>
              <dt>걸린 검색어</dt>
              <dd>{signal.match_terms.join(", ")}</dd>
            </>
          ) : null}
          <dt>그래프 반영</dt>
          <dd>{signal.in_graph ? "반영됨" : "아직 반영 안 됨"}</dd>
          <dt>근거 ID</dt>
          <dd className="mono">{signal.evidence_id}</dd>
        </dl>
      </div>
    </>
  );
}

function NodeBody({
  node,
  graph,
  answer,
  onOpen,
  onOpenViewer,
}: {
  node: RenderNode;
  graph: RenderGraph;
  answer: AnswerPayload;
  onOpen: (t: DrawerTarget) => void;
  onOpenViewer: OpenViewer;
}) {
  const labelOf = (id: string) =>
    graph.nodes.find((n) => n.id === id)?.label ?? id;
  const outgoing = graph.edges.filter((e) => e.source === node.id);
  const incoming = graph.edges.filter((e) => e.target === node.id);

  const cited = answer.citations.filter((c) => node.markers.includes(c.marker));
  const evidences = cited
    .map((c) => ({
      marker: c.marker,
      ev: answer.evidence.find((e) => e.evidence_id === c.evidence_id),
    }))
    .filter((x): x is { marker: number; ev: Evidence } => Boolean(x.ev));

  const claim = answer.claims?.find((c) => c.claim_id === node.id);
  const source =
    node.type === "Source"
      ? (findSource(node.id) ?? findSource(node.label))
      : undefined;

  return (
    <>
      {claim && (
        <div className="drawer-block">
          <h3>이 노드가 담고 있는 주장</h3>
          <p className="excerpt">{claim.statement}</p>
          <dl className="props" style={{ marginTop: "var(--sp-3)" }}>
            <dt>상태</dt>
            <dd>{STATUS_KO[claim.status]}</dd>
            {claim.claim_kind && (
              <>
                <dt>주장 종류</dt>
                <dd className="mono">{claim.claim_kind}</dd>
              </>
            )}
            {claim.contradicted_by?.length ? (
              <>
                <dt>어긋나는 주장</dt>
                <dd className="mono">{claim.contradicted_by.join(", ")}</dd>
              </>
            ) : null}
          </dl>
        </div>
      )}

      {source && (
        <div className="drawer-block">
          <h3>원본으로 가기</h3>
          <OriginBlock
            sourceId={source.source_id}
            sourceType={source.source_type}
            onOpenViewer={onOpenViewer}
          />
        </div>
      )}

      <div className="drawer-block">
        <h3>이어진 항목 {outgoing.length + incoming.length}개</h3>
        {outgoing.length + incoming.length === 0 ? (
          <p className="meta">
            이 관계도 안에서는 이어진 항목이 없습니다. 「관계 더 보기」 로
            펼치면 나올 수 있습니다.
          </p>
        ) : (
          <div className="stack" data-gap="2">
            {outgoing.map((e) => (
              <div
                className="row"
                key={`o-${e.id}`}
                style={{ fontSize: "var(--fs-sm)" }}
              >
                <span className="meta">이 항목</span>
                <code>{e.type}</code>
                <span>→ {labelOf(e.target)}</span>
                {e.cited && (
                  <span className="tag" data-tone="accent">
                    답변에 인용
                  </span>
                )}
              </div>
            ))}
            {incoming.map((e) => (
              <div
                className="row"
                key={`i-${e.id}`}
                style={{ fontSize: "var(--fs-sm)" }}
              >
                <span>{labelOf(e.source)}</span>
                <code>{e.type}</code>
                <span className="meta">→ 이 항목</span>
                {e.cited && (
                  <span className="tag" data-tone="accent">
                    답변에 인용
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="drawer-block">
        <h3>이 항목의 근거</h3>
        {evidences.length === 0 ? (
          <p className="meta">
            이 항목은 답변에 직접 인용되지 않았습니다. 맥락을 잇는 보조
            항목입니다.
          </p>
        ) : (
          <div className="ev-list">
            {evidences.map(({ marker, ev }) => (
              <button
                type="button"
                className="ev-row"
                key={ev.evidence_id}
                onClick={() => onOpen({ kind: "evidence", id: ev.evidence_id })}
              >
                <span className="ev-row-top">
                  <span className="ev-marker">{marker}</span>
                  <span className="ev-source">{sourceTitle(ev.source_id)}</span>
                </span>
                <span className="ev-snippet">{ev.snippet}</span>
                <span className="ev-where">
                  <code>{readLocator(ev.locator).human}</code>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

export function EvidenceDrawer({
  target,
  answer,
  graph,
  onClose,
  onOpen,
  onFocusMarker,
}: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [viewer, setViewer] = useState<{
    sourceId: string;
    locator: string;
  } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // 원문 보기가 열려 있으면 그것부터 닫는다.
      if (viewer) setViewer(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, viewer]);

  // 다른 근거로 넘어가면 맨 위부터 읽게 한다. 앞 화면의 스크롤이 남으면 발췌 첫 줄을 놓친다.
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [target.kind, target.id]);

  const openViewer: OpenViewer = (sourceId, locator) =>
    setViewer({ sourceId, locator });

  const evidenceList = answer.evidence;
  const rawList = answer.raw_signals ?? [];

  let title = "";
  let kindLabel = "";
  let body: ReactNode = null;
  let prev: DrawerTarget | null = null;
  let next: DrawerTarget | null = null;

  if (target.kind === "evidence") {
    const idx = evidenceList.findIndex((e) => e.evidence_id === target.id);
    const ev = evidenceList[idx];
    if (!ev) return null;
    const marker = answer.citations.find(
      (c) => c.evidence_id === ev.evidence_id,
    )?.marker;
    title = sourceTitle(ev.source_id);
    kindLabel = marker != null ? `답변 각주 ${marker} 의 근거` : "인용 근거";
    // 지식 월드 쪽 위치: 이 각주가 붙어 있는 노드. 관계도가 아니라 답변 payload 에서 찾는다.
    const knowledge: KnowledgeSpot = {
      marker,
      nodes:
        marker == null
          ? []
          : graph.nodes
              // 자료 노드는 뺀다. 자료 이름은 바로 아래 「자료 안의 위치」에 이미 있고,
              // 여기서 알고 싶은 것은 "이 근거가 무슨 지식을 만들었나" 다.
              .filter((n) => n.markers.includes(marker) && n.type !== "Source")
              .slice(0, 6)
              .map((n) => ({ id: n.id, text: n.label, label: n.typeKo })),
    };
    body = (
      <EvidenceBody
        ev={ev}
        onOpenViewer={openViewer}
        knowledge={knowledge}
        onFocusNode={(id) => onOpen({ kind: "node", id })}
      />
    );
    if (idx > 0)
      prev = { kind: "evidence", id: evidenceList[idx - 1].evidence_id };
    if (idx < evidenceList.length - 1)
      next = { kind: "evidence", id: evidenceList[idx + 1].evidence_id };
  } else if (target.kind === "raw") {
    const idx = rawList.findIndex((s) => s.evidence_id === target.id);
    const signal = rawList[idx];
    if (!signal) return null;
    title = sourceTitle(signal.source_id);
    kindLabel = "추가 원문 근거";
    body = <RawBody signal={signal} onOpenViewer={openViewer} />;
    if (idx > 0) prev = { kind: "raw", id: rawList[idx - 1].evidence_id };
    if (idx < rawList.length - 1)
      next = { kind: "raw", id: rawList[idx + 1].evidence_id };
  } else {
    const found = graph.nodes.find((n) => n.id === target.id);
    if (!found) return null;
    title = found.label;
    kindLabel = `${found.typeKo} · ${found.type}`;
    body = (
      <NodeBody
        node={found}
        graph={graph}
        answer={answer}
        onOpen={onOpen}
        onOpenViewer={openViewer}
      />
    );
  }

  const node =
    target.kind === "node"
      ? graph.nodes.find((n) => n.id === target.id)
      : undefined;

  const go = (t: DrawerTarget) => {
    onOpen(t);
    const m = answer.citations.find((c) => c.evidence_id === t.id)?.marker;
    onFocusMarker(m ?? null);
  };

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="drawer-head">
          <div style={{ minWidth: 0, flex: 1 }}>
            <div className="drawer-kind">{kindLabel}</div>
            <div className="drawer-title">
              {node && (
                <span
                  style={{
                    color: node.colorVar,
                    display: "inline-flex",
                    verticalAlign: "-2px",
                    marginRight: 6,
                  }}
                >
                  <EntityIcon type={node.type} size={16} />
                </span>
              )}
              {title}
            </div>
            {node && (
              <div className="row" style={{ marginTop: 6, gap: "var(--sp-2)" }}>
                {node.status && (
                  <span className="tag" data-tone={statusTone(node.status)}>
                    {STATUS_KO[node.status]}
                  </span>
                )}
                <span className="meta">
                  {node.rank === "focal"
                    ? "질문의 중심"
                    : node.rank === "cited"
                      ? "답변이 인용"
                      : "보조 맥락"}
                </span>
              </div>
            )}
          </div>
          <button
            type="button"
            className="drawer-close"
            onClick={onClose}
            aria-label="닫기 (Esc)"
            title="닫기 (Esc · 바깥 클릭도 됩니다)"
          >
            <IconClose size={16} />
          </button>
        </div>

        <div className="drawer-body scroll-y" ref={bodyRef}>
          {body}
        </div>

        <div className="drawer-foot">
          {(prev || next) && (
            <>
              <button
                type="button"
                className="btn"
                data-variant="outline"
                data-size="sm"
                disabled={!prev}
                onClick={() => prev && go(prev)}
              >
                ← 이전 근거
              </button>
              <button
                type="button"
                className="btn"
                data-variant="outline"
                data-size="sm"
                disabled={!next}
                onClick={() => next && go(next)}
              >
                다음 근거 →
              </button>
            </>
          )}
          <span className="spacer" />
          <span className="drawer-hint">닫기: × · Esc · 바깥 아무 곳</span>
        </div>
      </aside>

      {viewer && (
        <SourceViewer
          sourceId={viewer.sourceId}
          locator={viewer.locator}
          onClose={() => setViewer(null)}
        />
      )}
    </>
  );
}
