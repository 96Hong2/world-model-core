// 도면 한 판. Ask·Explore·Library·Changes 네 화면이 이걸 같이 쓴다.
//
// Graph 는 메뉴가 아니라 **상호작용 층**이라, 화면마다 다시 만들지 않고 이 컴포넌트에
// 제목과 subgraph 만 갈아 끼운다.
//
// 하는 일:
//   - 처음에는 핵심 관계 18개만 그리고, "모두 보기" 로 서버가 준 전부를 펼친다
//   - 1-hop 확장(노드의 + 버튼)
//   - 전체화면
//   - 얇은 범례 한 줄

import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import type { ExpandPayload, Subgraph } from "../types/answer";
import {
  CORE_NODES,
  MAX_NODES,
  canExpand,
  computeFocus,
  coreSubgraph,
  mergeExpansion,
  toRenderGraph,
} from "./adapter";
import { GraphCanvas } from "./GraphCanvas";
import { Legend } from "./Legend";
import { IconCollapse, IconExpand } from "../shell/icons";
import { useWm } from "../app/WmProvider";

/** 확장 결과가 0건이면 그 노드의 + 를 지우고 이유를 남긴다. */
type ExpandState = "loading" | "done" | "empty";

interface Props {
  title: string;
  /** 서버가 준 관계도 원본 */
  subgraph: Subgraph;
  /** 답변이 인용한 노드 id */
  citedIds?: string[];
  activeMarker: number | null;
  selectedNodeId: string | null;
  onSelectNode: (id: string) => void;
  onClearSelection: () => void;
  /** 아무것도 없을 때 적을 말 */
  emptyTitle: string;
  emptyBody: string;
  /** 1-hop 확장을 쓸 수 있나. Changes 화면처럼 확장이 뜻 없는 곳은 끈다. */
  expandable?: boolean;
  /**
   * 처음에 그릴 노드 수. 작은 도면(상세 화면에 끼우는 260~380px 상자)에서는 줄여야 한다.
   * 18개를 그 크기에 넣으면 fitView 가 크게 축소해 라벨이 점이 된다(실측: 자료 상세의
   * 미니 도면에서 노드가 알아볼 수 없는 크기로 나왔다).
   */
  coreLimit?: number;
  /** 펼침 애니메이션. 미니 도면에서는 끈다. */
  animate?: boolean;
}

export function GraphPane({
  title,
  subgraph,
  citedIds = [],
  activeMarker,
  selectedNodeId,
  onSelectNode,
  onClearSelection,
  emptyTitle,
  emptyBody,
  expandable = true,
  coreLimit = CORE_NODES,
  animate = true,
}: Props) {
  const { engine, theme } = useWm();
  const [showAll, setShowAll] = useState(false);
  const [full, setFull] = useState(false);
  const [expansions, setExpansions] = useState<ExpandPayload[]>([]);
  const [expandState, setExpandState] = useState<Record<string, ExpandState>>(
    {},
  );

  // 관계도가 갈리면(다른 질문·다른 대상) 펼침 상태를 처음으로 돌린다.
  const subgraphKey = useMemo(
    () => subgraph.nodes.map((n) => n.id).join("|"),
    [subgraph],
  );
  // 진행 중인 1-hop 확장 응답이 다른 관계도에 끼어들지 않게, 요청 시점의 키를 대조한다.
  // 취소 없이 두면 직전 질문에서 누른 확장 결과가 새 질문의 관계도에 선 없는 노드로 나타난다.
  const subgraphKeyRef = useRef(subgraphKey);
  useEffect(() => {
    subgraphKeyRef.current = subgraphKey;
    setShowAll(false);
    setExpansions([]);
    setExpandState({});
  }, [subgraphKey]);

  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFull(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [full]);

  const base = useMemo(
    () => (showAll ? subgraph : coreSubgraph(subgraph, coreLimit)),
    [subgraph, showAll, coreLimit],
  );

  const merged = useMemo(
    () => expansions.reduce<Subgraph>((acc, p) => mergeExpansion(acc, p), base),
    [base, expansions],
  );

  const graph = useMemo(
    () => toRenderGraph(merged, { citedIds }),
    [merged, citedIds],
  );

  const focus = useMemo(
    () => computeFocus(graph, { activeMarker, selectedNodeId }),
    [graph, activeMarker, selectedNodeId],
  );

  const expandableIds = useMemo(() => {
    if (!expandable) return new Set<string>();
    if (graph.nodes.length >= MAX_NODES) return new Set<string>();
    return new Set(
      graph.nodes
        .filter((n) => canExpand(n.type) && !expandState[n.id])
        .map((n) => n.id),
    );
  }, [expandable, graph, expandState]);

  const expandNode = useCallback(
    (id: string) => {
      const requestedFor = subgraphKeyRef.current;
      setExpandState((prev) => ({ ...prev, [id]: "loading" }));
      engine
        .expand(id, graph.nodes.length)
        .then((payload) => {
          // 응답이 도착했을 때 관계도가 이미 바뀌었으면 버린다.
          if (subgraphKeyRef.current !== requestedFor) return;
          const fresh = payload.nodes.length > 0;
          setExpandState((prev) => ({
            ...prev,
            [id]: fresh ? "done" : "empty",
          }));
          if (fresh) setExpansions((prev) => [...prev, payload]);
        })
        .catch(() => {
          if (subgraphKeyRef.current !== requestedFor) return;
          setExpandState((prev) => ({ ...prev, [id]: "empty" }));
        });
    },
    [engine, graph.nodes.length],
  );

  const hidden = subgraph.nodes.length - base.nodes.length;
  const expandedCount = expansions.length;

  return (
    <section className="board" data-full={full || undefined}>
      <div className="board-head">
        <h2 className="board-title">{title}</h2>
        <span className="board-stat">
          노드 {graph.nodes.length} · 연결 {graph.edges.length}
          {expandedCount > 0 && ` · 펼친 노드 ${expandedCount}`}
        </span>
        {graph.truncated && (
          <span className="tag" data-tone="unknown">
            상한에서 잘림
          </span>
        )}
        <span className="spacer" />
        <Legend />
        <button
          type="button"
          className="board-tool"
          data-icon-only
          aria-label={full ? "전체화면 접기 (Esc)" : "전체화면으로 펼치기"}
          title={full ? "전체화면 접기 (Esc)" : "전체화면으로 펼치기"}
          onClick={() => setFull((v) => !v)}
        >
          {full ? <IconCollapse size={14} /> : <IconExpand size={14} />}
        </button>
      </div>

      <div className="board-body graph-body">
        {graph.nodes.length === 0 ? (
          <div className="board-empty">
            <strong>{emptyTitle}</strong>
            <span>{emptyBody}</span>
          </div>
        ) : (
          <GraphCanvas
            graph={graph}
            focus={focus}
            selectedNodeId={selectedNodeId}
            theme={theme}
            expandableIds={expandableIds}
            onSelectNode={onSelectNode}
            onExpandNode={expandNode}
            onClearSelection={onClearSelection}
            animate={animate}
            tools={
              hidden > 0 ? (
                <button
                  type="button"
                  className="board-tool"
                  title={`이 답변이 쓴 관계 ${subgraph.nodes.length}개를 모두 그립니다`}
                  onClick={() => setShowAll(true)}
                >
                  관계 {hidden}개 더 보기
                </button>
              ) : showAll && subgraph.nodes.length > CORE_NODES ? (
                <button
                  type="button"
                  className="board-tool"
                  title="핵심 관계만 다시 보여줍니다"
                  onClick={() => setShowAll(false)}
                >
                  핵심만 보기
                </button>
              ) : null
            }
          />
        )}
      </div>
    </section>
  );
}
