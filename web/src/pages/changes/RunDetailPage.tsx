// 실행 상세. 자료 → 발췌 → 관찰·주장 → 대상·관계 의 흐름이 보이게 한다.
//
// 왼쪽은 자료별 타임라인, 오른쪽은 이번에 새로 생긴 관계를 실제 관계도로. 항목을 누르면
// 서랍이 열려 그 항목의 속성·출처·근거·상태 판단 이유를 본다.

import { useCallback, useEffect, useState, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { ElementDetail, RunDetail } from "../../api/browse";
import { GraphPane } from "../../graph/GraphPane";
import { EvidenceRows } from "../../panel/EvidenceRows";
import { SourceViewer } from "../../panel/SourceViewer";
import { Fold } from "../../panel/Fold";
import { IconBack, IconClose } from "../../shell/icons";

/** 자료 → 발췌 → 지식 → 대상 흐름을 한 줄로 보여 준다. 파이프라인을 말로 설명하지 않는다. */
function FlowStrip() {
  const steps = ["자료", "발췌", "관찰 · 주장", "대상 · 관계"];
  return (
    <div className="row" style={{ flexWrap: "wrap", gap: "var(--sp-2)" }}>
      {steps.map((s, i) => (
        <span key={s} className="row" style={{ gap: "var(--sp-2)" }}>
          <span className="tag" data-tone="accent">
            {s}
          </span>
          {i < steps.length - 1 && <span className="meta">→</span>}
        </span>
      ))}
    </div>
  );
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const { browse } = useWm();

  const [data, setData] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [element, setElement] = useState<ElementDetail | null>(null);
  const [elementError, setElementError] = useState<string | null>(null);
  const [viewer, setViewer] = useState<{
    sourceId: string;
    locator: string;
  } | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setData(null);
    browse
      .run(runId, ctl.signal)
      .then(setData)
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse, runId]);

  // 두 항목을 빠르게 연속 클릭하면 먼저 누른 요청의 응답이 늦게 도착해
  // 나중 항목의 서랍을 덮는다. 이전 요청을 취소해 마지막 클릭만 남긴다.
  const elementCtl = useRef<AbortController | null>(null);
  const openElement = useCallback(
    (id: string, kind: "node" | "edge") => {
      elementCtl.current?.abort();
      const ctl = new AbortController();
      elementCtl.current = ctl;
      setElement(null);
      setElementError(null);
      browse
        .element(id, kind, ctl.signal)
        .then((payload) => {
          if (!ctl.signal.aborted) setElement(payload);
        })
        .catch((err) => {
          if (!ctl.signal.aborted) setElementError((err as Error).message);
        });
    },
    [browse],
  );

  useEffect(() => {
    if (!element && !elementError) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setElement(null);
      setElementError(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [element, elementError]);

  if (error) {
    return (
      <div className="screen">
        <div className="notice" data-tone="error">
          <div className="notice-title">이 실행 기록을 열지 못했습니다</div>
          <div className="notice-body">{error}</div>
          <Link to="/changes" className="btn" data-variant="outline">
            변경 목록으로
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="screen">
      <div className="screen-head">
        <Link to="/changes" className="btn" data-variant="quiet" data-size="sm">
          <IconBack size={14} />
          변경
        </Link>
        <h1 style={{ fontSize: "var(--fs-h2)" }} className="mono">
          {runId}
        </h1>
        <span className="spacer" />
        {data && (
          <span className="meta">
            노드 {data.node_count.toLocaleString()} · 관계{" "}
            {data.edge_count.toLocaleString()} · 자료 {data.source_count}
          </span>
        )}
      </div>

      <div className="browse-page scroll-y">
        <div className="browse-inner">
          {!data ? (
            <div className="skeleton-rows" style={{ padding: 0 }}>
              {[0, 1, 2, 3].map((i) => (
                <i key={i} />
              ))}
            </div>
          ) : (
            <>
              <div className="detail-head">
                <div className="detail-crumb">
                  <Link to="/changes">변경</Link>
                  <span>›</span>
                  <span>수집 실행</span>
                </div>
                <div className="detail-title">
                  <h1 className="mono">{data.run_id}</h1>
                </div>
                <p className="detail-lede">
                  {data.started_at?.replace("T", " ").replace("Z", "")} 부터{" "}
                  {data.ended_at?.replace("T", " ").replace("Z", "")} 까지 자료{" "}
                  {data.source_count}종을 읽어 노드{" "}
                  {data.node_count.toLocaleString()}개와 관계{" "}
                  {data.edge_count.toLocaleString()}개를 만들었습니다.
                </p>
                <div style={{ marginTop: "var(--sp-3)" }}>
                  <FlowStrip />
                </div>
                <div className="deltas" style={{ marginTop: "var(--sp-3)" }}>
                  {data.counts.map((c) => (
                    <span className="delta" data-kind="added" key={c.label}>
                      <span className="delta-sign">+</span>
                      {c.label_ko} <b>{c.count.toLocaleString()}</b>
                    </span>
                  ))}
                </div>
              </div>

              <div className="detail-grid">
                <div>
                  <section className="section-block">
                    <h2>자료가 무엇을 만들었나</h2>
                    <p className="meta">
                      지식이 많이 늘어난 자료를 위로 올렸습니다. <b>+</b> 는 이
                      자료에서만 나온 것, <b>~</b> 는 다른 자료와 함께
                      뒷받침하는 것입니다.
                    </p>
                    <div className="timeline">
                      {data.steps.map((s) => (
                        <div className="tl-item" key={s.source_id}>
                          <div className="tl-time">
                            {s.at?.replace("T", " ").replace("Z", "")}
                          </div>
                          <div className="tl-title">
                            <Link
                              to={`/library/${encodeURIComponent(s.source_id)}`}
                              style={{
                                textDecoration: "none",
                                color: "inherit",
                              }}
                            >
                              {s.source_title}
                            </Link>
                          </div>
                          <div className="meta">{s.source_type_ko}</div>
                          <div className="deltas">
                            {s.added.map((c) => (
                              <span
                                className="delta"
                                data-kind="added"
                                key={c.label}
                              >
                                <span className="delta-sign">+</span>
                                {c.label_ko} <b>{c.count.toLocaleString()}</b>
                              </span>
                            ))}
                            {s.touched.map((c) => (
                              <span
                                className="delta"
                                data-kind="touched"
                                key={c.label}
                              >
                                <span className="delta-sign">~</span>
                                {c.label_ko} <b>{c.count.toLocaleString()}</b>
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                </div>

                <div>
                  <section className="section-block">
                    <h2>새로 생긴 관계</h2>
                    <p className="meta" style={{ marginBottom: "var(--sp-2)" }}>
                      이번 실행이 만든 것 중 관계가 많이 붙은 것들입니다.
                    </p>
                    <div className="mini-board" data-tall="true">
                      <GraphPane
                        title="이번에 생긴 것"
                        subgraph={data.subgraph}
                        activeMarker={null}
                        selectedNodeId={selectedNodeId}
                        onSelectNode={(id) => {
                          setSelectedNodeId(id);
                          openElement(id, "node");
                        }}
                        onClearSelection={() => setSelectedNodeId(null)}
                        emptyTitle="그릴 관계가 없습니다"
                        emptyBody="이 실행은 관계를 새로 만들지 않았습니다."
                        animate={false}
                        coreLimit={12}
                        expandable={false}
                      />
                    </div>
                  </section>

                  <section className="section-block">
                    <h2>바뀐 항목</h2>
                    <p className="meta" style={{ marginBottom: "var(--sp-2)" }}>
                      누르면 그 항목의 속성·출처·근거와 상태를 그렇게 본 이유가
                      열립니다.
                    </p>
                    <div className="rows">
                      {data.changes.map((c) => (
                        <button
                          type="button"
                          key={`${c.kind}-${c.id}`}
                          className="rowitem"
                          onClick={() => openElement(c.id, c.kind)}
                        >
                          <span className="rowitem-top">
                            <span className="rowitem-type">
                              <span
                                className="delta-sign"
                                style={{ color: "var(--verified)" }}
                              >
                                +
                              </span>
                              {c.type_ko}
                            </span>
                            <span className="rowitem-name">
                              {c.kind === "edge"
                                ? `${c.from_label} → ${c.to_label}`
                                : c.label}
                            </span>
                          </span>
                          {c.kind === "edge" && (
                            <span className="meta-row" style={{ marginTop: 4 }}>
                              <span className="mono">{c.type}</span>
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  </section>
                </div>
              </div>

              <div className="limits">
                <h3>이 화면이 보여주지 못하는 것</h3>
                <ul>
                  {data.limits.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </div>
      </div>

      {(element || elementError) && (
        <>
          <div
            className="drawer-scrim"
            onClick={() => {
              setElement(null);
              setElementError(null);
            }}
            aria-hidden="true"
          />
          <aside
            className="drawer"
            role="dialog"
            aria-modal="true"
            aria-label="항목 상세"
          >
            <div className="drawer-head">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="drawer-kind">
                  {element ? `${element.type_ko} · ${element.type}` : "항목"}
                </div>
                <div className="drawer-title">
                  {element?.label ?? "열지 못했습니다"}
                </div>
              </div>
              <button
                type="button"
                className="drawer-close"
                aria-label="닫기 (Esc)"
                onClick={() => {
                  setElement(null);
                  setElementError(null);
                }}
              >
                <IconClose size={16} />
              </button>
            </div>

            <div className="drawer-body">
              {elementError ? (
                <p className="notice-body">{elementError}</p>
              ) : !element ? (
                <div className="skeleton-rows" style={{ padding: 0 }}>
                  {[0, 1, 2].map((i) => (
                    <i key={i} />
                  ))}
                </div>
              ) : (
                <>
                  {element.status_reason && (
                    <div className="drawer-block">
                      <h3>상태를 그렇게 본 이유</h3>
                      <p
                        className="notice-body"
                        style={{ fontSize: "var(--fs-sm)" }}
                      >
                        {element.status_reason}
                      </p>
                    </div>
                  )}

                  <div className="drawer-block">
                    <h3>언제</h3>
                    <dl className="props">
                      <dt>처음 들어옴</dt>
                      <dd className="mono">
                        {element.created_at ?? "기록 없음"}
                      </dd>
                      <dt>마지막 손댐</dt>
                      <dd className="mono">
                        {element.updated_at ?? "기록 없음"}
                      </dd>
                      <dt>식별자</dt>
                      <dd className="mono">{element.id}</dd>
                    </dl>
                  </div>

                  {element.subgraph.nodes.length > 0 && (
                    <div className="drawer-block">
                      <h3>주변 관계</h3>
                      <div className="mini-board">
                        <GraphPane
                          title="주변"
                          subgraph={element.subgraph}
                          activeMarker={null}
                          selectedNodeId={null}
                          onSelectNode={() => {}}
                          onClearSelection={() => {}}
                          emptyTitle="관계가 없습니다"
                          emptyBody=""
                          expandable={false}
                        />
                      </div>
                    </div>
                  )}

                  {element.evidence.length > 0 && (
                    <div className="drawer-block">
                      <h3>근거</h3>
                      <EvidenceRows
                        rows={element.evidence}
                        onOpenViewer={(sid, loc) =>
                          setViewer({ sourceId: sid, locator: loc })
                        }
                      />
                    </div>
                  )}

                  {element.sources.length > 0 && (
                    <div className="drawer-block">
                      <h3>출처 자료</h3>
                      <div className="rel-items">
                        {element.sources.map((s) => (
                          <Link
                            key={s.source_id}
                            to={`/library/${encodeURIComponent(s.source_id)}`}
                            className="rel-link"
                          >
                            {s.title}
                          </Link>
                        ))}
                      </div>
                    </div>
                  )}

                  {element.properties.length > 0 && (
                    <div className="drawer-block">
                      <Fold title="속성 전부" count={element.properties.length}>
                        <dl className="props">
                          {element.properties.map((p) => (
                            <div key={p.key} style={{ display: "contents" }}>
                              <dt>{p.key_ko}</dt>
                              <dd>{p.value}</dd>
                            </div>
                          ))}
                        </dl>
                      </Fold>
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="drawer-foot">
              <span className="drawer-hint">
                닫기: 오른쪽 위 × · Esc · 바깥 아무 곳
              </span>
            </div>
          </aside>
        </>
      )}

      {viewer && (
        <SourceViewer
          sourceId={viewer.sourceId}
          locator={viewer.locator}
          onClose={() => setViewer(null)}
        />
      )}
    </div>
  );
}
