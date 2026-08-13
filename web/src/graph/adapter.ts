// Answer 계약의 subgraph 를 화면이 쓰는 모양(RenderNode/RenderEdge)으로 바꾼다.
//
// 이 파일은 렌더러를 모른다. React Flow · elkjs 를 import 하는 곳은 GraphCanvas.tsx 하나뿐이고,
// 그 바깥은 전부 여기서 나온 값만 쓴다(RENDERER-DECISION.md 「어댑터 경계」).
//
// 시각 위계 규칙(Master Plan §10): focal > cited > supporting.
// Authority(자료 권위)는 여기 입력에 아예 들어오지 않는다. 노드 크기·명도에 authority 를
// 섞으면 "중요한 Entity"와 "믿을 만한 근거"가 화면에서 뒤섞이기 때문이다.

import type {
  ExpandPayload,
  NodeRank,
  Status,
  Subgraph,
  SubgraphEdge,
  SubgraphNode,
} from "../types/answer";

/** 확장 누적 노드 상한 (Master Plan §10). */
export const MAX_NODES = 150;

export type EntityType =
  | "BusinessDomain"
  | "Account"
  | "Need"
  | "Capability"
  | "Feature"
  | "Industry"
  | "Product"
  | "Claim"
  | "Source"
  | "Observation"
  | "Event"
  | "Competitor"
  | "Persona"
  | "Deal"
  | "Unknown";

const KNOWN_TYPES: EntityType[] = [
  "BusinessDomain",
  "Account",
  "Need",
  "Capability",
  "Feature",
  "Industry",
  "Product",
  "Claim",
  "Source",
  "Observation",
  "Event",
  "Competitor",
  "Persona",
  "Deal",
];

/** 화면에 쓰는 한국어 이름. 영문 라벨은 온톨로지 식별자라 그대로 두고 옆에 붙인다. */
export const TYPE_KO: Record<EntityType, string> = {
  BusinessDomain: "사업영역",
  Account: "고객사",
  Need: "요구",
  Capability: "역량",
  Feature: "기능",
  Industry: "산업",
  Product: "제품",
  Claim: "주장",
  Source: "자료",
  Observation: "관찰",
  Event: "이벤트",
  Competitor: "경쟁사",
  Persona: "페르소나",
  Deal: "거래",
  Unknown: "기타",
};

/** styles.css 의 --t-* 변수 이름과 1:1 로 맞춘 키. */
export function typeColorVar(type: EntityType): string {
  const key = type.toLowerCase();
  return KNOWN_TYPES.some((t) => t.toLowerCase() === key)
    ? `var(--t-${key})`
    : "var(--t-default)";
}

/**
 * 1-hop 확장이 결과를 줄 수 있는 노드 종류.
 *
 * 서버의 확장은 비즈니스 엣지(BELONGS_TO·HAS_NEED·IN_DOMAIN·ADDRESSED_BY 등)만 따라간다.
 * 그 엣지는 엔티티끼리만 이어져 있어서, 지식 백본 쪽 노드(Claim·Observation·Source)로
 * 확장을 걸면 언제나 빈 결과가 온다. 실 API 로 재어 확인했다.
 * 눌러 봐야 아무 일도 없는 + 버튼을 그리지 않으려고 여기서 미리 거른다.
 */
const EXPANDABLE_TYPES: ReadonlySet<EntityType> = new Set<EntityType>([
  "BusinessDomain",
  "Account",
  "Need",
  "Capability",
  "Feature",
  "Industry",
  "Product",
  "Competitor",
  "Persona",
  "Deal",
  "Event",
]);

export function canExpand(type: EntityType | string): boolean {
  return EXPANDABLE_TYPES.has(type as EntityType);
}

export type EdgeStroke = "solid" | "dashed" | "double";
export type Emphasis = "critical" | "disputed" | "cited" | "plain";

export interface RenderNode {
  id: string;
  type: EntityType;
  typeKo: string;
  label: string;
  rank: NodeRank;
  status: Status | null;
  markers: number[];
  /** 답변이 직접 인용한 노드인가 */
  cited: boolean;
  /** 1-hop 확장으로 나중에 붙은 노드인가 */
  expanded: boolean;
  colorVar: string;
  /** elk 에 넘길 상자 크기. rank 로만 정해지고 authority 는 쓰지 않는다. */
  width: number;
  height: number;
}

export interface RenderEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  status: Status | null;
  cited: boolean;
  claimIds: string[];
  stroke: EdgeStroke;
  emphasis: Emphasis;
}

export interface RenderGraph {
  nodes: RenderNode[];
  edges: RenderEdge[];
  focalId: string | null;
  truncated: boolean;
  /** 확장으로 상한(150)에 걸려 잘린 노드가 있었는가 */
  capped: boolean;
}

export interface RenderOptions {
  /** 질문의 중심 entity. 없으면 rank === "focal" 인 노드를 쓴다. */
  focalId?: string | null;
  /** 답변이 인용한 노드 id. 없으면 citation_markers 유무로 판정한다. */
  citedIds?: Iterable<string>;
}

function entityType(node: SubgraphNode): EntityType {
  const first = node.labels?.[0];
  const hit = KNOWN_TYPES.find((t) => t === first);
  return hit ?? "Unknown";
}

/**
 * rank 별 상자 크기. graph.css 의 .rank-* 글자 크기와 맞춰야 한다.
 *
 * 값을 키운 이유: 개선 전 실측에서 노드 라벨이 11px 이었고 캔버스의 8.5% 만 덮었다.
 * 라벨을 13px(초점은 15px)로 올리면 같은 글자가 더 넓은 상자를 요구한다.
 * 초기 표시를 20개 안쪽으로 줄이는 것(coreSubgraph)과 한 쌍으로 움직인다.
 */
const BOX: Record<
  NodeRank,
  { width: number; charWidth: number; lineHeight: number }
> = {
  focal: { width: 262, charWidth: 15, lineHeight: 20 },
  cited: { width: 224, charWidth: 13, lineHeight: 18 },
  supporting: { width: 198, charWidth: 13, lineHeight: 18 },
};

function boxSize(
  rank: NodeRank,
  label: string,
): { width: number; height: number } {
  const box = BOX[rank];
  const inner = box.width - 58; // 아이콘 20 + 좌우 여백 + 왼쪽 띠 4
  const perLine = Math.max(5, Math.floor(inner / box.charWidth));
  const lines = Math.min(3, Math.max(1, Math.ceil(label.length / perLine)));
  const height = 14 + 14 + lines * box.lineHeight + 6; // 상하 여백 + 종류 줄 + 라벨 줄
  return { width: box.width, height: Math.round(height) };
}

function edgeStroke(status: Status | null | undefined): EdgeStroke {
  switch (status) {
    case "DISPUTED":
      return "double";
    case "CANDIDATE":
    case "UNVERIFIED":
    case "SUPERSEDED":
    case "ARCHIVED":
      return "dashed";
    default:
      // VERIFIED · CRITICAL · 상태 없는 구조 엣지(FROM_SOURCE 등)
      return "solid";
  }
}

function edgeEmphasis(edge: SubgraphEdge): Emphasis {
  if (edge.status === "DISPUTED") return "disputed";
  if (edge.status === "CRITICAL") return "critical";
  if (edge.cited) return "cited";
  return "plain";
}

export function edgeId(edge: SubgraphEdge): string {
  return `${edge.from}__${edge.type}__${edge.to}`;
}

/**
 * subgraph → 화면용 그래프.
 * focalId·citedIds 를 주면 그 값이 우선하고, 없으면 payload 의 rank·citation_markers 를 따른다.
 */
export function toRenderGraph(
  subgraph: Subgraph,
  options: RenderOptions = {},
): RenderGraph {
  const citedSet = new Set(options.citedIds ?? []);
  const declaredFocal =
    options.focalId ??
    subgraph.nodes.find((n) => n.rank === "focal")?.id ??
    null;

  const seen = new Set<string>();
  const nodes: RenderNode[] = [];
  let capped = false;

  for (const node of subgraph.nodes) {
    if (seen.has(node.id)) continue;
    if (nodes.length >= MAX_NODES) {
      capped = true;
      continue;
    }
    seen.add(node.id);

    const markers = [...(node.citation_markers ?? [])].sort((a, b) => a - b);
    const cited = citedSet.has(node.id) || markers.length > 0;
    // focalId 를 바깥에서 지정하면 payload 의 focal 노드는 cited 로 한 단계 내려온다.
    const rank: NodeRank =
      node.id === declaredFocal
        ? "focal"
        : node.rank === "focal"
          ? "cited"
          : node.rank;
    const type = entityType(node);
    const size = boxSize(rank, node.label_text);

    nodes.push({
      id: node.id,
      type,
      typeKo: TYPE_KO[type],
      label: node.label_text,
      rank,
      status: node.status ?? null,
      markers,
      cited,
      expanded: node.expanded === true,
      colorVar: typeColorVar(type),
      width: size.width,
      height: size.height,
    });
  }

  const nodeIds = new Set(nodes.map((n) => n.id));
  const edgeSeen = new Set<string>();
  const edges: RenderEdge[] = [];

  for (const edge of subgraph.edges) {
    // 끝점이 잘려나간 엣지는 그리지 않는다(허공에 뜬 선을 만들지 않는다).
    if (!nodeIds.has(edge.from) || !nodeIds.has(edge.to)) continue;
    const id = edgeId(edge);
    if (edgeSeen.has(id)) continue;
    edgeSeen.add(id);

    edges.push({
      id,
      source: edge.from,
      target: edge.to,
      type: edge.type,
      status: edge.status ?? null,
      cited: edge.cited === true,
      claimIds: edge.claim_ids ?? [],
      stroke: edgeStroke(edge.status),
      emphasis: edgeEmphasis(edge),
    });
  }

  return {
    nodes,
    edges,
    focalId: declaredFocal,
    truncated: subgraph.truncated === true,
    capped,
  };
}

/** 처음에 보여줄 노드 수. 이 안쪽이면 라벨이 읽히고 관계가 눈에 들어온다. */
export const CORE_NODES = 18;

/**
 * 처음 그릴 **핵심 관계**만 골라낸다.
 *
 * 서버는 노드 50개까지 준다. 그걸 한 번에 그리면 라벨이 점처럼 작아지고 무엇이 중요한지
 * 사라진다(실측: 노드 50개일 때 캔버스의 8.5% 만 덮고 나머지는 빈 공간이었다).
 * 그래서 **초점에서 연결을 따라 18개까지만** 남기고, 나머지는 사용자가 펼치게 한다.
 *
 * 고르는 방법은 서버의 SubgraphBuilder 와 같은 원리다: 초점을 씨앗으로 두고 이어지는
 * 노드를 위계·연결 수 순서로 끌어온다. 짝이 없는 노드는 넣지 않는다 — 선 없이 떠 있는
 * 점은 칸만 쓰고 아무 관계도 말하지 않는다.
 */
export function coreSubgraph(
  subgraph: Subgraph,
  limit: number = CORE_NODES,
): Subgraph {
  if (subgraph.nodes.length <= limit) return subgraph;

  const degree = new Map<string, number>();
  const adjacent = new Map<string, Set<string>>();
  const present = new Set(subgraph.nodes.map((n) => n.id));

  for (const edge of subgraph.edges) {
    if (!present.has(edge.from) || !present.has(edge.to)) continue;
    degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1);
    degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1);
    if (!adjacent.has(edge.from)) adjacent.set(edge.from, new Set());
    if (!adjacent.has(edge.to)) adjacent.set(edge.to, new Set());
    adjacent.get(edge.from)!.add(edge.to);
    adjacent.get(edge.to)!.add(edge.from);
  }

  const rankOf = (n: SubgraphNode) =>
    n.rank === "focal" ? 0 : (n.citation_markers?.length ?? 0) > 0 ? 1 : 2;

  const ordered = [...subgraph.nodes].sort((a, b) => {
    const r = rankOf(a) - rankOf(b);
    if (r !== 0) return r;
    return (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0);
  });

  const kept = new Set<string>(
    ordered.filter((n) => n.rank === "focal").map((n) => n.id),
  );
  if (kept.size === 0 && ordered.length > 0) kept.add(ordered[0].id);

  while (kept.size < limit) {
    // 이미 남긴 것과 이어지는 노드를 우선순위대로 끌어온다.
    const next = ordered.find(
      (n) =>
        !kept.has(n.id) &&
        [...(adjacent.get(n.id) ?? [])].some((id) => kept.has(id)),
    );
    if (next) {
      kept.add(next.id);
      continue;
    }
    // 더 이을 것이 없으면 다음 무리의 씨앗을 짝과 함께 심는다.
    const seed = ordered.find(
      (n) => !kept.has(n.id) && (degree.get(n.id) ?? 0) > 0,
    );
    if (!seed) break;
    kept.add(seed.id);
    if (kept.size < limit) {
      const partner = ordered.find(
        (n) => !kept.has(n.id) && adjacent.get(seed.id)?.has(n.id),
      );
      if (partner) kept.add(partner.id);
    }
  }

  return {
    ...subgraph,
    nodes: subgraph.nodes.filter((n) => kept.has(n.id)),
    edges: subgraph.edges.filter((e) => kept.has(e.from) && kept.has(e.to)),
  };
}

/** 1-hop 확장 결과를 기존 subgraph 에 합친다. 이미 있는 노드·엣지는 건드리지 않는다. */
export function mergeExpansion(
  base: Subgraph,
  payload: ExpandPayload,
  cap: number = MAX_NODES,
): Subgraph {
  const nodeIds = new Set(base.nodes.map((n) => n.id));
  const nodes = [...base.nodes];
  for (const node of payload.nodes) {
    if (nodeIds.has(node.id)) continue;
    if (nodes.length >= cap) break;
    nodeIds.add(node.id);
    nodes.push({ ...node, expanded: true });
  }

  const edgeKeys = new Set(base.edges.map(edgeId));
  const edges = [...base.edges];
  for (const edge of payload.edges) {
    const key = edgeId(edge);
    if (edgeKeys.has(key)) continue;
    if (!nodeIds.has(edge.from) || !nodeIds.has(edge.to)) continue;
    edgeKeys.add(key);
    edges.push(edge);
  }

  return { ...base, nodes, edges };
}

export interface FocusInput {
  /** 답변 각주를 눌렀을 때의 번호 */
  activeMarker: number | null;
  /** 그래프에서 고른 노드 */
  selectedNodeId: string | null;
}

export interface FocusResult {
  /** 강조할 노드 */
  nodeIds: Set<string>;
  /** 강조할 엣지 */
  edgeIds: Set<string>;
  /** 강조가 걸려 있어 나머지를 흐리게 해야 하는가 */
  active: boolean;
}

/**
 * 각주 클릭 · 노드 선택을 그래프 강조로 바꾼다.
 * - 각주: 그 번호를 단 노드와, 그 노드들 사이를 잇는 인용 엣지
 * - 노드 선택: 그 노드와 1-hop 이웃, 그리고 이어진 엣지
 */
export function computeFocus(
  graph: RenderGraph,
  input: FocusInput,
): FocusResult {
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();

  if (input.activeMarker != null) {
    const marker = input.activeMarker;
    for (const node of graph.nodes) {
      if (node.markers.includes(marker)) nodeIds.add(node.id);
    }
    for (const edge of graph.edges) {
      if (nodeIds.has(edge.source) && nodeIds.has(edge.target))
        edgeIds.add(edge.id);
    }
    // 각주가 노드 하나만 가리키면 그 노드에서 나가는 인용 엣지까지 보여 준다.
    if (nodeIds.size === 1) {
      for (const edge of graph.edges) {
        if (
          edge.cited &&
          (nodeIds.has(edge.source) || nodeIds.has(edge.target))
        ) {
          edgeIds.add(edge.id);
        }
      }
    }
  }

  if (input.selectedNodeId) {
    nodeIds.add(input.selectedNodeId);
    for (const edge of graph.edges) {
      if (
        edge.source === input.selectedNodeId ||
        edge.target === input.selectedNodeId
      ) {
        edgeIds.add(edge.id);
        nodeIds.add(edge.source);
        nodeIds.add(edge.target);
      }
    }
  }

  return {
    nodeIds,
    edgeIds,
    active: input.activeMarker != null || input.selectedNodeId != null,
  };
}
