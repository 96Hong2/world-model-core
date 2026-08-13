// 자료 상세. 미리보기 → 뽑아낸 지식 → 관련 대상 → 미니 관계도 → 메타 → 근거.
// 그리고 [원문 열기] 를 어디서든 눌러 볼 수 있게 머리줄에 둔다.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { SourceDetail } from "../../api/browse";
import { GraphPane } from "../../graph/GraphPane";
import { EvidenceRows } from "../../panel/EvidenceRows";
import { SourceViewer } from "../../panel/SourceViewer";
import { OriginBlock } from "../../panel/OriginBlock";
import { Fold } from "../../panel/Fold";
import { readLocator } from "../../lib/locator";
import { typeColorVar } from "../../graph/adapter";
import type { EntityType } from "../../graph/adapter";
import { IconAsk, IconBack, IconExternal } from "../../shell/icons";
import { SaveButton } from "../../panel/SaveButton";

const SLUG_OF: Record<string, string> = {
  BusinessDomain: "domain",
  Account: "account",
  Need: "need",
  Capability: "capability",
  Feature: "feature",
  Product: "product",
  Industry: "industry",
  Deal: "deal",
  Competitor: "competitor",
};

export function SourceDetailPage() {
  const { sourceId = "" } = useParams();
  const { browse } = useWm();
  const navigate = useNavigate();

  const [data, setData] = useState<SourceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewer, setViewer] = useState<{
    sourceId: string;
    locator: string;
  } | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setData(null);
    setError(null);
    browse
      .source(sourceId, ctl.signal)
      .then(setData)
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse, sourceId]);

  const openViewer = useCallback(
    (sid: string, locator: string) => setViewer({ sourceId: sid, locator }),
    [],
  );

  const goToNode = useCallback(
    (nodeId: string) => {
      const node = data?.subgraph.nodes.find((n) => n.id === nodeId);
      const label = node?.labels?.[0] ?? "";
      if (label === "Source") {
        setSelectedNodeId(nodeId);
        return;
      }
      const slug = SLUG_OF[label];
      if (slug) navigate(`/explore/${slug}/${encodeURIComponent(nodeId)}`);
      else setSelectedNodeId(nodeId);
    },
    [data, navigate],
  );

  if (error) {
    return (
      <div className="screen">
        <div className="notice" data-tone="error">
          <div className="notice-title">이 자료를 열지 못했습니다</div>
          <div className="notice-body">{error}</div>
          <Link to="/library" className="btn" data-variant="outline">
            서재로 돌아가기
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="screen">
      <div className="screen-head">
        <Link to="/library" className="btn" data-variant="quiet" data-size="sm">
          <IconBack size={14} />
          서재
        </Link>
        <h1 style={{ fontSize: "var(--fs-h2)" }}>{data?.title ?? sourceId}</h1>
        <SaveButton
          kind="source"
          label={data?.title ?? sourceId}
          to={`/library/${encodeURIComponent(sourceId)}`}
        />
        <span className="spacer" />
        {data && (
          <>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              data-size="sm"
              onClick={() => openViewer(sourceId, "")}
            >
              <IconExternal size={14} />
              원문 열기
            </button>
            <Link
              to={`/ask?q=${encodeURIComponent(`${data.title} 자료에는 무엇이 적혀 있는가?`)}`}
              className="btn"
              data-variant="outline"
              data-size="sm"
            >
              <IconAsk size={14} />이 자료에 대해 묻기
            </Link>
          </>
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
                  <Link to="/library">서재</Link>
                  <span>›</span>
                  <Link to={`/library?group=${encodeURIComponent(data.group)}`}>
                    {data.group}
                  </Link>
                </div>
                <div className="detail-title">
                  <h1>{data.title}</h1>
                  {data.sensitivity === "restricted" && (
                    <span className="tag" data-tone="critical">
                      열람 제한
                    </span>
                  )}
                </div>
                {data.description && (
                  <p className="detail-lede">
                    이 자료가 정본인 것: {data.description}
                  </p>
                )}
                <div className="meta-row">
                  <span>{data.source_type_ko}</span>
                  <span>발췌 {data.preview_total.toLocaleString()}건</span>
                  <span>
                    뽑아낸 지식 {data.entity_count.toLocaleString()}개
                  </span>
                  {data.ingested_at && <span>{data.ingested_at} 반영</span>}
                </div>
              </div>

              <div className="detail-grid">
                <div>
                  <section className="section-block">
                    <h2>원문 미리보기</h2>
                    <p className="meta" style={{ marginBottom: "var(--sp-2)" }}>
                      수집할 때 쪼개 둔 발췌를 위치 순서로 보여 줍니다. 전체는
                      위의 [원문 열기] 에서 봅니다.
                    </p>
                    <div className="preview">
                      {data.preview.slice(0, 12).map((p, i) => (
                        <div className="preview-row" key={`${p.locator}-${i}`}>
                          <span className="preview-where">
                            {readLocator(p.locator).human}
                          </span>
                          <span className="preview-text">{p.text}</span>
                        </div>
                      ))}
                    </div>
                    {data.preview_total > 12 && (
                      <button
                        type="button"
                        className="btn"
                        data-variant="outline"
                        data-size="sm"
                        style={{ marginTop: "var(--sp-3)" }}
                        onClick={() => openViewer(sourceId, "")}
                      >
                        나머지 {data.preview_total - 12}건 원문 보기에서 보기
                      </button>
                    )}
                  </section>

                  <section className="section-block">
                    <h2>원본으로 가기</h2>
                    <OriginBlock
                      sourceId={sourceId}
                      sourceType={data.source_type}
                      onOpenViewer={openViewer}
                    />
                  </section>

                  <Fold title="자료 메타데이터" count={data.metadata.length}>
                    <dl className="props">
                      {data.metadata.map((m) => (
                        <div key={m.key} style={{ display: "contents" }}>
                          <dt>{m.key_ko}</dt>
                          <dd>{m.value}</dd>
                        </div>
                      ))}
                    </dl>
                  </Fold>
                </div>

                <div>
                  <section className="section-block">
                    <h2>이 자료에서 뽑아낸 지식</h2>
                    <div
                      className="deltas"
                      style={{ marginTop: "var(--sp-2)" }}
                    >
                      {data.extracted.map((e) => (
                        <span className="delta" data-kind="added" key={e.label}>
                          <span className="delta-sign">+</span>
                          {e.label_ko} <b>{e.count.toLocaleString()}</b>
                        </span>
                      ))}
                    </div>
                  </section>

                  <section className="section-block">
                    <h2>이 자료가 만든 관계</h2>
                    <div className="mini-board">
                      <GraphPane
                        title="자료 → 지식"
                        subgraph={data.subgraph}
                        activeMarker={null}
                        selectedNodeId={selectedNodeId}
                        onSelectNode={goToNode}
                        onClearSelection={() => setSelectedNodeId(null)}
                        emptyTitle="아직 지식으로 이어지지 않았습니다"
                        emptyBody="발췌는 있지만 엔티티·관계로 뽑아내지 못한 자료입니다."
                        animate={false}
                        coreLimit={10}
                        expandable={false}
                      />
                    </div>
                  </section>

                  {data.entities.length > 0 && (
                    <section className="section-block">
                      <h2>여기서 나온 대상</h2>
                      <div className="rel-items">
                        {data.entities.slice(0, 40).map((e) => {
                          const slug = SLUG_OF[e.type];
                          return slug ? (
                            <Link
                              key={e.id}
                              to={`/explore/${slug}/${encodeURIComponent(e.id)}`}
                              className="rel-link"
                              style={{
                                ["--type-color" as string]: typeColorVar(
                                  e.type as EntityType,
                                ),
                              }}
                            >
                              <i />
                              {e.name}
                            </Link>
                          ) : (
                            <span key={e.id} className="rel-link">
                              {e.name}
                            </span>
                          );
                        })}
                      </div>
                      {data.entities.length > 40 && (
                        <p
                          className="meta"
                          style={{ marginTop: "var(--sp-2)" }}
                        >
                          {data.entities.length - 40}개 더 있습니다.
                        </p>
                      )}
                    </section>
                  )}

                  {data.claims.length > 0 && (
                    <Fold title="여기서 뽑아낸 주장" count={data.claims.length}>
                      <ul className="plain-list">
                        {data.claims.map((c) => (
                          <li key={c.claim_id}>{c.statement}</li>
                        ))}
                      </ul>
                    </Fold>
                  )}

                  <section className="section-block">
                    <h2>근거로 쓰인 발췌</h2>
                    <EvidenceRows
                      rows={data.evidence}
                      onOpenViewer={openViewer}
                      emptyBody="이 자료의 발췌가 아직 답변 근거로 쓰이지 않았습니다."
                    />
                  </section>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

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
