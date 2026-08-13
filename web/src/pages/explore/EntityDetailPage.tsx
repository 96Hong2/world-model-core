// 대상 상세. 이름 → 무엇인지 → 관계도 → 우리가 아는 것 → 근거 → 자료 → [이 대상에 대해 묻기].
//
// 관계도는 Ask 와 같은 컴포넌트(GraphPane)를 쓴다. Graph 는 화면마다 다시 만드는 것이
// 아니라 네 화면을 관통하는 층이다.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { EntityDetail } from "../../api/browse";
import { GraphPane } from "../../graph/GraphPane";
import { EvidenceRows } from "../../panel/EvidenceRows";
import { SourceViewer } from "../../panel/SourceViewer";
import { Fold } from "../../panel/Fold";
import { EntityIcon } from "../../graph/icons";
import { TYPE_KO, typeColorVar } from "../../graph/adapter";
import type { EntityType } from "../../graph/adapter";
import { STATUS_KO } from "../../lib/labels";
import type { Status } from "../../types/answer";
import { IconAsk, IconBack } from "../../shell/icons";
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

export function EntityDetailPage() {
  const { type = "account", id = "" } = useParams();
  const { browse } = useWm();
  const navigate = useNavigate();

  const [data, setData] = useState<EntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [viewer, setViewer] = useState<{
    sourceId: string;
    locator: string;
  } | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setData(null);
    setError(null);
    setSelectedNodeId(null);
    browse
      .entity(id, ctl.signal)
      .then(setData)
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse, id]);

  const openViewer = useCallback(
    (sourceId: string, locator: string) => setViewer({ sourceId, locator }),
    [],
  );

  // 관계도에서 노드를 누르면 그 대상 상세로 옮겨 간다. 관계를 따라 걸어 다니게 하는 것이
  // 이 화면의 목적이다.
  const goToNode = useCallback(
    (nodeId: string) => {
      if (nodeId === id) {
        setSelectedNodeId(nodeId);
        return;
      }
      const node = data?.subgraph.nodes.find((n) => n.id === nodeId);
      const slug = SLUG_OF[node?.labels?.[0] ?? ""];
      if (slug) navigate(`/explore/${slug}/${encodeURIComponent(nodeId)}`);
      else setSelectedNodeId(nodeId);
    },
    [data, id, navigate],
  );

  if (error) {
    return (
      <div className="screen">
        <div className="notice" data-tone="error">
          <div className="notice-title">이 대상을 찾지 못했습니다</div>
          <div className="notice-body">{error}</div>
          <Link to={`/explore/${type}`} className="btn" data-variant="outline">
            목록으로 돌아가기
          </Link>
        </div>
      </div>
    );
  }

  const entityType = (data?.type ?? "Unknown") as EntityType;

  return (
    <div className="screen">
      <div className="screen-head">
        <Link
          to={`/explore/${type}`}
          className="btn"
          data-variant="quiet"
          data-size="sm"
        >
          <IconBack size={14} />
          {TYPE_KO[entityType] ?? "목록"}
        </Link>
        <h1 style={{ fontSize: "var(--fs-h2)" }}>{data?.name ?? id}</h1>
        <SaveButton
          kind="entity"
          label={data?.name ?? id}
          to={`/explore/${type}/${encodeURIComponent(id)}`}
        />
        <span className="spacer" />
        {data && (
          <Link
            to={`/ask?q=${encodeURIComponent(`${data.name}에 대해 우리가 아는 것과 그 근거는 무엇인가?`)}`}
            className="btn"
            data-variant="primary"
            data-size="sm"
          >
            <IconAsk size={14} />이 대상에 대해 묻기
          </Link>
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
                  <Link to="/explore">둘러보기</Link>
                  <span>›</span>
                  <Link to={`/explore/${type}`}>{data.type_ko}</Link>
                </div>
                <div
                  className="detail-title"
                  style={{
                    ["--type-color" as string]: typeColorVar(entityType),
                  }}
                >
                  <span
                    style={{ color: typeColorVar(entityType), display: "flex" }}
                  >
                    <EntityIcon type={entityType} size={22} />
                  </span>
                  <h1>{data.name}</h1>
                  {data.status && (
                    <span className="tag" data-tone={statusTone(data.status)}>
                      {STATUS_KO[data.status as Status] ?? data.status}
                    </span>
                  )}
                </div>
                {data.summary && <p className="detail-lede">{data.summary}</p>}
                <div className="meta-row">
                  <span>{data.type_ko}</span>
                  <span>근거 {data.evidence.length}건</span>
                  <span>주장 {data.claims.length}건</span>
                  <span>자료 {data.sources.length}종</span>
                </div>
              </div>

              <div className="detail-grid">
                <div>
                  <section className="section-block">
                    <h2>이어진 관계</h2>
                    <p className="meta" style={{ marginBottom: "var(--sp-2)" }}>
                      노드를 누르면 그 대상으로 옮겨 갑니다. + 를 누르면 한 단계
                      더 펼칩니다.
                    </p>
                    <div className="mini-board" data-tall="true">
                      <GraphPane
                        title="주변 관계"
                        subgraph={data.subgraph}
                        activeMarker={null}
                        selectedNodeId={selectedNodeId}
                        onSelectNode={goToNode}
                        onClearSelection={() => setSelectedNodeId(null)}
                        emptyTitle="이어진 관계가 없습니다"
                        emptyBody="이 대상은 아직 다른 것과 연결되지 않았습니다."
                        animate={false}
                        coreLimit={12}
                      />
                    </div>
                  </section>

                  {data.related.length > 0 && (
                    <section className="section-block">
                      <h2>관계별로 보기</h2>
                      <div className="panel">
                        {data.related.map((g) => (
                          <div className="rel-group" key={g.type}>
                            <div className="rel-name">
                              {g.type_ko}{" "}
                              <span className="meta">
                                {g.type} · {g.items.length}개
                              </span>
                            </div>
                            <div className="rel-items">
                              {g.items.map((it) => {
                                const slug = SLUG_OF[it.type];
                                const label = (
                                  <>
                                    <i />
                                    {it.name}
                                  </>
                                );
                                return slug ? (
                                  <Link
                                    key={it.id}
                                    to={`/explore/${slug}/${encodeURIComponent(it.id)}`}
                                    className="rel-link"
                                    style={{
                                      ["--type-color" as string]: typeColorVar(
                                        it.type as EntityType,
                                      ),
                                    }}
                                  >
                                    {label}
                                  </Link>
                                ) : (
                                  <span key={it.id} className="rel-link">
                                    {label}
                                  </span>
                                );
                              })}
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                </div>

                <div>
                  <section className="section-block">
                    <h2>우리가 아는 것</h2>
                    <p className="meta" style={{ marginBottom: "var(--sp-2)" }}>
                      자료에서 뽑아낸 주장입니다. 놓치면 안 되는 것과 어긋나는
                      것을 위로 올립니다.
                    </p>
                    {data.claims.length === 0 ? (
                      <p className="meta">
                        이 대상에 대해 뽑아낸 주장이 없습니다. 자료에 이름만
                        나온 상태입니다.
                      </p>
                    ) : (
                      <div className="panel">
                        <ul className="plain-list">
                          {data.claims.slice(0, 8).map((c) => (
                            <li key={c.claim_id}>
                              {c.lane.includes("critical") && (
                                <span
                                  className="tag"
                                  data-tone="critical"
                                  style={{ marginRight: "var(--sp-2)" }}
                                >
                                  놓치면 안 됨
                                </span>
                              )}
                              {c.lane.includes("conflict") && (
                                <span
                                  className="tag"
                                  data-tone="conflict"
                                  style={{ marginRight: "var(--sp-2)" }}
                                >
                                  어긋남
                                </span>
                              )}
                              {c.statement}
                            </li>
                          ))}
                        </ul>
                        {data.claims.length > 8 && (
                          <Fold
                            title="나머지 주장 보기"
                            count={data.claims.length - 8}
                          >
                            <ul className="plain-list">
                              {data.claims.slice(8).map((c) => (
                                <li key={c.claim_id}>{c.statement}</li>
                              ))}
                            </ul>
                          </Fold>
                        )}
                      </div>
                    )}
                  </section>

                  <section className="section-block">
                    <h2>근거</h2>
                    <p className="meta" style={{ marginBottom: "var(--sp-2)" }}>
                      누르면 원본으로 가는 길이 펼쳐집니다.
                    </p>
                    <EvidenceRows
                      rows={data.evidence}
                      onOpenViewer={openViewer}
                    />
                  </section>

                  {data.sources.length > 0 && (
                    <section className="section-block">
                      <h2>이 대상이 나온 자료</h2>
                      <div className="rel-items">
                        {data.sources.map((s) => (
                          <Link
                            key={s.source_id}
                            to={`/library/${encodeURIComponent(s.source_id)}`}
                            className="rel-link"
                          >
                            {s.title}
                          </Link>
                        ))}
                      </div>
                    </section>
                  )}

                  {data.properties.length > 0 && (
                    <Fold
                      title="기록된 속성 전부"
                      count={data.properties.length}
                    >
                      <dl className="props">
                        {data.properties.map((p) => (
                          <div key={p.key} style={{ display: "contents" }}>
                            <dt>{p.key_ko}</dt>
                            <dd>{p.value}</dd>
                          </div>
                        ))}
                      </dl>
                    </Fold>
                  )}
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
