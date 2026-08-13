// 렌더러를 아는 유일한 파일이다(RENDERER-DECISION.md).
// React Flow(@xyflow/react)로 그리고 elkjs(layered · RIGHT)로 배치한다.
// 바깥에는 RenderGraph/FocusResult 만 오간다. React Flow 타입이 이 파일 밖으로 새면 안 된다.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getSmoothStepPath,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import type { Edge, EdgeProps, Node, NodeProps } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk-api";

import "@xyflow/react/dist/style.css";

import type {
  EntityType,
  FocusResult,
  RenderEdge,
  RenderGraph,
  RenderNode,
} from "./adapter";
import { EntityIcon } from "./icons";
import { IconReplay } from "../shell/icons";

type Theme = "light" | "dark";

/**
 * 화살촉(SVG marker)은 CSS 변수를 못 받아서 색을 실제 값으로 넘겨야 한다.
 * 값은 styles.css 의 같은 이름 변수와 맞춘다.
 */
const EDGE_COLOR: Record<Theme, Record<string, string>> = {
  light: {
    plain: "#aab2bd",
    cited: "#2f5bd7",
    critical: "#c0362c",
    disputed: "#d1720b",
    dim: "#dfe3e8",
  },
  dark: {
    plain: "#4f5a68",
    cited: "#7ea2ff",
    critical: "#ff8b80",
    disputed: "#f0a154",
    dim: "#242c37",
  },
};

interface NodeData extends Record<string, unknown> {
  node: RenderNode;
  focused: boolean;
  dimmed: boolean;
  selected: boolean;
  expandable: boolean;
  onExpand: (id: string) => void;
  /** 펼침 애니메이션에서 아직 나타날 차례가 아니면 false. */
  revealed: boolean;
}

interface EdgeData extends Record<string, unknown> {
  edge: RenderEdge;
  focused: boolean;
  dimmed: boolean;
  showLabel: boolean;
  color: string;
  revealed: boolean;
}

type EntityFlowNode = Node<NodeData, "entity">;
type RelFlowEdge = Edge<EdgeData, "rel">;

/* ------------------------------------------------------------------ 노드 */

function EntityNodeView({ data }: NodeProps<EntityFlowNode>) {
  const { node, focused, dimmed, selected, expandable, onExpand, revealed } =
    data;
  const classes = [
    "gnode",
    `rank-${node.rank}`,
    node.status ? `status-${node.status}` : "",
    node.expanded ? "expanded" : "",
    focused ? "hl" : "",
    selected ? "selected" : "",
    dimmed ? "dim" : "",
    revealed ? "" : "unrevealed",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      style={{ "--type-color": node.colorVar } as CSSProperties}
      title={`${node.typeKo}(${node.type}) · ${node.label}`}
      aria-hidden={revealed ? undefined : true}
    >
      <Handle type="target" position={Position.Left} className="gnode-handle" />
      <span className="icon">
        <EntityIcon type={node.type as EntityType} />
      </span>
      <span className="txt">
        <span className="type">
          {node.typeKo} · {node.type}
        </span>
        <span className="label">{node.label}</span>
      </span>
      {node.markers.length > 0 && (
        <span className="markers">
          {node.markers.slice(0, 4).map((m) => (
            <b key={m}>{m}</b>
          ))}
        </span>
      )}
      {node.status && <span className="status-dot" data-status={node.status} />}
      {expandable && (
        <button
          type="button"
          className="nodrag nopan expand-btn"
          title="이 노드에 이어진 항목 1단계 더 보기"
          aria-label={`${node.label} 1단계 더 보기`}
          onClick={(e) => {
            e.stopPropagation();
            onExpand(node.id);
          }}
        >
          +
        </button>
      )}
      <Handle
        type="source"
        position={Position.Right}
        className="gnode-handle"
      />
    </div>
  );
}

/* ------------------------------------------------------------------ 엣지 */

function RelEdgeView({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps<RelFlowEdge>) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 14,
  });

  if (!data) return <BaseEdge id={id} path={path} markerEnd={markerEnd} />;

  const { edge, focused, dimmed, showLabel, color, revealed } = data;
  const width =
    edge.stroke === "double" ? 5 : focused ? 2.4 : edge.cited ? 1.8 : 1.2;
  const dash = edge.stroke === "dashed" ? (focused ? "7 4" : "5 4") : undefined;
  // 펼침 애니메이션: 선이 노드보다 먼저 그려져야 무엇에서 무엇으로 이어지는지 읽힌다.
  const alpha = revealed ? (dimmed ? 0.16 : 1) : 0;

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: color,
          strokeWidth: width,
          strokeDasharray: dash,
          opacity: alpha,
          transition: "opacity 260ms ease",
        }}
      />
      {/* DISPUTED 이중선: 굵은 선 위에 배경색 선을 얹어 두 줄로 보이게 한다. */}
      {edge.stroke === "double" && (
        <path
          d={path}
          fill="none"
          stroke="var(--panel)"
          strokeWidth={1.7}
          style={{ opacity: alpha, transition: "opacity 260ms ease" }}
        />
      )}
      {showLabel && revealed && (
        <EdgeLabelRenderer>
          <div
            className={[
              "edge-label",
              edge.cited || focused ? "cited" : "",
              edge.emphasis === "disputed" ? "conflict" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            {edge.type}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { entity: EntityNodeView };
const edgeTypes = { rel: RelEdgeView };

/* ---------------------------------------------------------------- 펼침 순서 */

/**
 * 관계도를 한 번에 던지면 무엇이 무엇에서 나왔는지 읽히지 않는다. 그래서 **초점에서
 * 연결을 따라 한 겹씩 번져 나가는 순서로** 그린다. 선이 노드보다 먼저 그려진다.
 *
 * 장식이 아니다. 목적은 관계를 읽히게 하는 것이고, 1.8초 안에 멈추고 그 뒤에는
 * 아무것도 움직이지 않는다(PRD §14.4 의 "과도한 Animation 금지" 를 그렇게 지킨다).
 * OS·브라우저가 "움직임 최소화" 로 설정돼 있으면 애니메이션 없이 최종 상태로 바로 그린다.
 *
 * 시각표(요구 §8):
 *   0.0s 초점 · 0.3s 초점에서 나가는 선 · 0.5s 그 선이 닿는 노드
 *   0.9s 다음 겹 · 1.3s 그다음 · 1.5~1.7s 근거·자료 · 최종 1.7s
 *
 * 겹 번호를 세는 대신 **경과 시간(ms)** 으로 판정한다. 그래야 위 시각표를 그대로
 * 코드에 적을 수 있고, 겹 수가 달라져도 총 길이가 1.8초를 넘지 않는다.
 */
const REVEAL_DONE = Number.POSITIVE_INFINITY;
const REVEAL_MAX_STEPS = 4;
const REVEAL_TICK_MS = 60;
const REVEAL_TOTAL_MS = 1700;

/** 노드가 나타나는 시각. 초점은 0, 그다음부터 400ms 간격. */
function nodeRevealAt(depth: number): number {
  return depth === 0 ? 0 : 100 + depth * 400;
}

/** 선은 자기가 닿는 노드보다 200ms 먼저 그려진다. 무엇에서 무엇으로인지 그 순서로 읽힌다. */
function edgeRevealAt(depth: number): number {
  return Math.max(0, nodeRevealAt(depth) - 200);
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** 초점에서의 거리. 초점에 닿지 않는 무리는 그 무리에서 가장 앞선 노드를 씨앗으로 잡는다. */
function revealDepths(graph: RenderGraph): Map<string, number> {
  const neighbours = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (!neighbours.has(edge.source)) neighbours.set(edge.source, []);
    if (!neighbours.has(edge.target)) neighbours.set(edge.target, []);
    neighbours.get(edge.source)!.push(edge.target);
    neighbours.get(edge.target)!.push(edge.source);
  }

  const depth = new Map<string, number>();
  // 위계 순서대로 씨앗을 잡는다. focal 이 먼저, 그다음 인용된 것, 마지막이 배경.
  const order = [...graph.nodes].sort(
    (a, b) => RANK_STEP[a.rank] - RANK_STEP[b.rank],
  );
  for (const seed of order) {
    if (depth.has(seed.id)) continue;
    depth.set(seed.id, RANK_STEP[seed.rank]);
    const queue = [seed.id];
    while (queue.length > 0) {
      const id = queue.shift()!;
      const here = depth.get(id)!;
      for (const next of neighbours.get(id) ?? []) {
        if (depth.has(next)) continue;
        depth.set(next, Math.min(here + 1, REVEAL_MAX_STEPS));
        queue.push(next);
      }
    }
  }
  return depth;
}

/** 씨앗의 출발 겹. 배경 노드는 초점과 안 이어져 있어도 마지막에는 나타난다. */
const RANK_STEP: Record<string, number> = {
  focal: 0,
  cited: 1,
  supporting: 2,
};

/* ---------------------------------------------------------------- 레이아웃 */

const elk = new ELK();

/**
 * 배치는 캔버스 모양에 맞춰 조율한다.
 *
 * 개선 전에는 노드가 캔버스 가운데에 작게 뭉쳐 있었다(노드가 덮는 면적 8.5%,
 * 노드 묶음 범위 64.7%). 원인은 두 가지였다.
 *   1) 노드 50개를 한 번에 그려서 fitView 가 크게 축소했다 → coreSubgraph 로 18개부터 시작
 *   2) 겹 간격이 좁아 그림이 가로로만 길어져 캔버스 비율과 안 맞았다 → aspectRatio 를
 *      실제 캔버스 비율로 넘기고 겹 간격을 넓혔다
 */
function layoutOptions(aspect: number): Record<string, string> {
  return {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.layered.spacing.nodeNodeBetweenLayers": "108",
    "elk.spacing.nodeNode": "26",
    "elk.spacing.edgeNode": "26",
    "elk.layered.spacing.edgeNodeBetweenLayers": "26",
    "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    "elk.layered.cycleBreaking.strategy": "GREEDY",
    "elk.edgeRouting": "ORTHOGONAL",
    "elk.separateConnectedComponents": "true",
    "elk.spacing.componentComponent": "44",
    // 캔버스가 세로로 길면 그림도 세로로 접히게 한다. 빈 공간을 줄이는 가장 큰 지렛대다.
    "elk.aspectRatio": aspect.toFixed(2),
  };
}

type Positions = Record<string, { x: number; y: number }>;

async function layout(graph: RenderGraph, aspect: number): Promise<Positions> {
  const root: ElkNode = {
    id: "root",
    layoutOptions: layoutOptions(aspect),
    children: graph.nodes.map((n) => ({
      id: n.id,
      width: n.width,
      height: n.height,
    })),
    edges: graph.edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };
  const result = await elk.layout(root);
  const positions: Positions = {};
  for (const child of result.children ?? []) {
    positions[child.id] = { x: child.x ?? 0, y: child.y ?? 0 };
  }
  return positions;
}

/* ------------------------------------------------------------------ 캔버스 */

export interface GraphCanvasProps {
  graph: RenderGraph;
  focus: FocusResult;
  selectedNodeId: string | null;
  theme: Theme;
  /** 이 노드는 1-hop 확장 payload 가 준비돼 있다 */
  expandableIds: Set<string>;
  onSelectNode: (id: string) => void;
  onExpandNode: (id: string) => void;
  onClearSelection: () => void;
  /** 도면 왼쪽 아래 도구 줄에 덧붙일 버튼(전체화면·모두 보기 등). 캔버스 위에 얹힌다. */
  tools?: ReactNode;
  /** 펼침 애니메이션을 쓸까. 상세 화면에 끼우는 작은 도면에서는 끈다. */
  animate?: boolean;
}

function Canvas(props: GraphCanvasProps) {
  const { graph, focus, selectedNodeId, theme, expandableIds, animate = true } = props;
  const [positions, setPositions] = useState<Positions | null>(null);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  //: 펼침이 시작된 뒤 흐른 시간(ms). REVEAL_DONE 이면 전부 보인다.
  const [revealMs, setRevealMs] = useState<number>(REVEAL_DONE);
  const { fitView, setCenter, getViewport } = useReactFlow();
  const layoutKeyRef = useRef<string>("");

  // 캔버스 실제 크기. 배치를 이 비율에 맞춰야 빈 공간이 줄어든다.
  const canvasWidth = useStore((s) => s.width);
  const canvasHeight = useStore((s) => s.height);
  const aspect =
    canvasWidth > 0 && canvasHeight > 0
      ? Math.min(3, Math.max(0.5, canvasWidth / canvasHeight))
      : 1.4;

  // 노드 집합이 바뀔 때만 다시 배치한다(강조·선택은 배치를 흔들지 않는다).
  const layoutKey = useMemo(
    () => graph.nodes.map((n) => n.id).join("|") + "#" + graph.edges.length,
    [graph],
  );

  // 비율은 큰 변화만 반영한다. 창을 1px 씩 끌 때마다 배치를 다시 하면 그림이 계속 뛴다.
  const aspectBucket = Math.round(aspect * 4) / 4;

  useEffect(() => {
    let cancelled = false;
    layout(graph, aspectBucket)
      .then((next) => {
        if (!cancelled) setPositions(next);
      })
      .catch((err) => {
        console.error("elk 레이아웃 실패", err);
        if (!cancelled) setPositions({});
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey, aspectBucket]);

  useEffect(() => {
    if (!positions) return;
    // 배치가 실제로 달라졌으면 시야를 다시 맞춘다. 노드 집합(layoutKey)만 보면,
    // 캔버스 비율이 바뀌어 좌표가 통째로 달라졌을 때 옛 좌표에 맞춘 시야가 그대로 남아
    // 노드가 화면 밖으로 밀린다(실측: 왼쪽 끝 노드가 도면 바깥에서 잘렸다).
    const fitKey = `${layoutKey}@${aspectBucket}`;
    if (layoutKeyRef.current === fitKey) return;
    layoutKeyRef.current = fitKey;
    // 여백을 좁게 준다(0.16 → 0.08). 넓게 주면 캔버스가 그만큼 비어 보인다.
    const t = window.setTimeout(
      () => fitView({ padding: 0.06, duration: 0, maxZoom: 1.7 }),
      0,
    );
    return () => window.clearTimeout(t);
  }, [positions, layoutKey, aspectBucket, fitView]);

  const depths = useMemo(() => revealDepths(graph), [graph]);
  const maxDepth = useMemo(() => {
    let top = 0;
    for (const d of depths.values()) top = Math.max(top, d);
    return top;
  }, [depths]);

  // 노드 집합이 바뀌면 펼침을 처음부터 다시 재생한다.
  const [replayNonce, setReplayNonce] = useState(0);
  useEffect(() => {
    if (!positions) return;
    if (!animate || prefersReducedMotion() || maxDepth === 0) {
      setRevealMs(REVEAL_DONE);
      return;
    }
    setRevealMs(0);
    let elapsed = 0;
    const timer = window.setInterval(() => {
      elapsed += REVEAL_TICK_MS;
      if (elapsed >= REVEAL_TOTAL_MS) {
        window.clearInterval(timer);
        setRevealMs(REVEAL_DONE);
        return;
      }
      setRevealMs(elapsed);
    }, REVEAL_TICK_MS);
    return () => window.clearInterval(timer);
  }, [layoutKey, positions, maxDepth, replayNonce, animate]);

  const revealing = revealMs !== REVEAL_DONE;

  // 강조가 걸렸을 때의 시야 처리.
  //
  // 개선 전에는 강조된 노드로 fitView(maxZoom 1.15) 를 걸어 확대했다. 그러면 나머지 그림이
  // 캔버스 밖으로 밀려나 전체 모양을 잃었다(실측: 노드 묶음 범위가 캔버스의 1274% 가 됐다).
  // 지금은 **배율을 바꾸지 않는다.** 강조된 노드가 이미 보이면 아무것도 하지 않고,
  // 화면 밖에 있을 때만 배율을 유지한 채 최소한으로 옮긴다.
  const focusKey = useMemo(
    () => (focus.active ? [...focus.nodeIds].sort().join("|") : ""),
    [focus],
  );

  useEffect(() => {
    if (!positions) return;
    if (!focusKey) return;
    const ids = focusKey.split("|").filter((id) => positions[id]);
    if (ids.length === 0) return;

    const t = window.setTimeout(() => {
      const vp = getViewport();
      if (canvasWidth === 0 || canvasHeight === 0) return;

      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const id of ids) {
        const p = positions[id];
        const node = graph.nodes.find((n) => n.id === id);
        minX = Math.min(minX, p.x);
        minY = Math.min(minY, p.y);
        maxX = Math.max(maxX, p.x + (node?.width ?? 200));
        maxY = Math.max(maxY, p.y + (node?.height ?? 60));
      }

      // 화면 좌표로 옮겨 지금 보이는지 판정한다.
      const left = minX * vp.zoom + vp.x;
      const right = maxX * vp.zoom + vp.x;
      const top = minY * vp.zoom + vp.y;
      const bottom = maxY * vp.zoom + vp.y;
      const pad = 24;
      const visible =
        left >= pad &&
        top >= pad &&
        right <= canvasWidth - pad &&
        bottom <= canvasHeight - pad;
      if (visible) return;

      // 배율은 그대로 두고 가운데만 옮긴다. 전체 모양을 잃지 않는다.
      setCenter((minX + maxX) / 2, (minY + maxY) / 2, {
        zoom: vp.zoom,
        duration: 220,
      });
    }, 0);
    return () => window.clearTimeout(t);
  }, [
    focusKey,
    positions,
    graph.nodes,
    canvasWidth,
    canvasHeight,
    getViewport,
    setCenter,
  ]);

  const flowNodes: EntityFlowNode[] = useMemo(() => {
    if (!positions) return [];
    return graph.nodes.map((node) => ({
      id: node.id,
      type: "entity" as const,
      position: positions[node.id] ?? { x: 0, y: 0 },
      width: node.width,
      height: node.height,
      draggable: true,
      data: {
        node,
        focused: focus.nodeIds.has(node.id),
        dimmed: focus.active && !focus.nodeIds.has(node.id),
        selected: node.id === selectedNodeId,
        expandable: expandableIds.has(node.id),
        onExpand: props.onExpandNode,
        revealed: nodeRevealAt(depths.get(node.id) ?? 0) <= revealMs,
      },
    }));
  }, [
    graph,
    positions,
    focus,
    selectedNodeId,
    expandableIds,
    props.onExpandNode,
    depths,
    revealMs,
  ]);

  const flowEdges: RelFlowEdge[] = useMemo(() => {
    const palette = EDGE_COLOR[theme];
    return graph.edges.map((edge) => {
      const focused = focus.edgeIds.has(edge.id);
      const dimmed = focus.active && !focused;
      const color = dimmed
        ? palette.dim
        : focused
          ? palette.cited
          : edge.emphasis === "disputed"
            ? palette.disputed
            : edge.emphasis === "critical"
              ? palette.critical
              : edge.emphasis === "cited"
                ? palette.cited
                : palette.plain;
      return {
        id: edge.id,
        type: "rel" as const,
        source: edge.source,
        target: edge.target,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color,
        },
        data: {
          edge,
          focused,
          dimmed,
          // 인용 엣지·강조 엣지·마우스 올린 엣지만 라벨을 상시 노출한다.
          showLabel: edge.cited || focused || hoveredEdgeId === edge.id,
          color,
          // 선이 노드보다 **먼저** 나타난다. 늦게 오는 끝점보다 200ms 앞선다.
          revealed:
            edgeRevealAt(
              Math.max(
                depths.get(edge.source) ?? 0,
                depths.get(edge.target) ?? 0,
              ),
            ) <= revealMs,
        },
      };
    });
  }, [graph, focus, theme, hoveredEdgeId, depths, revealMs]);

  const onNodeClick = useCallback(
    (_e: React.MouseEvent, node: EntityFlowNode) => props.onSelectNode(node.id),
    [props],
  );

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodeClick={onNodeClick}
      onPaneClick={props.onClearSelection}
      onEdgeMouseEnter={(_e, edge) => setHoveredEdgeId(edge.id)}
      onEdgeMouseLeave={() => setHoveredEdgeId(null)}
      nodesConnectable={false}
      elementsSelectable={false}
      proOptions={{ hideAttribution: true }}
      minZoom={0.2}
      maxZoom={2}
      fitView
      fitViewOptions={{ padding: 0.06, maxZoom: 1.7 }}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={24}
        size={1}
        color="var(--board-grid)"
      />
      <Controls showInteractive={false} position="top-right" />
      <div className="board-tools">
        {!animate ? null : revealing ? (
          <button
            type="button"
            className="board-tool"
            onClick={() => setRevealMs(REVEAL_DONE)}
          >
            건너뛰기
          </button>
        ) : (
          <button
            type="button"
            className="board-tool"
            title="연결이 이어지는 순서를 처음부터 다시 보여줍니다"
            onClick={() => setReplayNonce((n) => n + 1)}
          >
            <IconReplay size={13} />
            다시 보기
          </button>
        )}
        {props.tools}
      </div>
    </ReactFlow>
  );
}

export function GraphCanvas(props: GraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
