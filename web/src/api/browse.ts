// 질문하지 않고 회사 지식을 둘러보는 조회 API.
//
//   GET /browse/overview            둘러보기 첫 화면 (사업영역 + 종류별 규모)
//   GET /browse/entities            종류별 Entity 목록 (검색·필터·페이지)
//   GET /browse/entity/{id}         Entity 상세 + 주변 관계도 + 근거
//   GET /browse/sources             서재 목록 (그룹·검색·필터)
//   GET /browse/source/{id}         자료 상세 + 여기서 나온 지식 + 관계도
//   GET /browse/runs                실행 목록 (변경 화면)
//   GET /browse/run/{id}            실행 상세 (자료별 타임라인 + 새로 생긴 관계)
//   GET /browse/element/{id}        노드·관계 하나의 상세 (변경 화면 서랍)
//   GET /browse/health              데이터 상태
//
// 답변 경로(/ask, /graph/expand)와 **완전히 분리**된다. api/browse.py 가 이 라우트만 갖고,
// api/service.py 는 손대지 않는다.
//
// 관계도는 Answer 계약의 `Subgraph` 모양을 그대로 쓴다. 그래야 graph/adapter.ts 와
// GraphCanvas 를 한 줄도 안 고치고 네 화면에서 다 쓸 수 있다.

import type { Subgraph } from "../types/answer";

/** 화면이 원본으로 갈 수 있는 방법. 열 수 없는 경로에 "열기" 를 달지 않으려고 서버가 판정한다. */
export interface OpenTarget {
  /** url: 새 탭 · viewer: WM 이 파싱해 둔 자료라 안에서 위치까지 열 수 있다 · path: 복사만 */
  kind: "url" | "viewer" | "path";
  /** kind === "url" 일 때만 있다 */
  url?: string;
  /** 항상 있다. 복사 버튼이 이 값을 쓴다. */
  path: string;
  /** 사람이 읽는 위치 설명 */
  where?: string;
  /** 왜 새 탭으로 못 여는지 (kind === "path") */
  reason?: string;
}

export interface CountEntry {
  label: string;
  label_ko: string;
  count: number;
}

export interface DomainCard {
  id: string;
  name: string;
  status?: string | null;
  maturity?: string | null;
  industry_scope?: string | null;
  account_count: number;
  need_count: number;
  deal_count: number;
  evidence_count: number;
}

export interface BrowseOverview {
  domains: DomainCard[];
  counts: CountEntry[];
  /** 그래프 전체 규모 */
  total_nodes: number;
  total_edges: number;
  /** 마지막 ingest 시각 */
  last_ingest_at?: string | null;
}

export interface EntityRow {
  id: string;
  type: string;
  name: string;
  /** 한 줄 설명. 없으면 빈 문자열 */
  subtitle: string;
  status?: string | null;
  evidence_count: number;
  /** 이 대상이 걸린 사업영역 이름 */
  domains: string[];
  /** 목록에서 규모를 가늠하게 하는 숫자 (관계 수) */
  degree: number;
}

export interface EntityList {
  total: number;
  items: EntityRow[];
  /** 이 종류에서 고를 수 있는 필터 값 */
  facets: { domains: string[]; statuses: string[] };
}

export interface PropertyEntry {
  key: string;
  key_ko: string;
  value: string;
}

export interface EvidenceRow {
  evidence_id: string;
  source_id: string;
  source_title: string;
  source_type: string;
  authority_tier?: string | null;
  locator: string;
  snippet: string;
  observed_at?: string | null;
  open: OpenTarget;
}

export interface ClaimRow {
  claim_id: string;
  statement: string;
  status: string;
  lane: string[];
  evidence_ids: string[];
  updated_at?: string | null;
}

export interface RelatedGroup {
  /** 관계 이름 (HAS_NEED 등) */
  type: string;
  type_ko: string;
  /** 이 관계로 이어진 상대 */
  items: { id: string; type: string; name: string }[];
}

export interface EntityDetail {
  id: string;
  type: string;
  type_ko: string;
  name: string;
  summary: string;
  status?: string | null;
  properties: PropertyEntry[];
  /** "우리가 아는 것" — 이 대상에 붙은 주장 */
  claims: ClaimRow[];
  evidence: EvidenceRow[];
  sources: { source_id: string; title: string; source_type: string }[];
  related: RelatedGroup[];
  subgraph: Subgraph;
}

export interface SourceRow {
  source_id: string;
  title: string;
  source_type: string;
  source_type_ko: string;
  /** 서재 그룹 (제품 / 영업·BD / 제안 / 시장·전략 / 고객 / 슬랙 / 엔지니어링) */
  group: string;
  description: string;
  domains: string[];
  accounts: string[];
  ingested_at?: string | null;
  evidence_count: number;
  entity_count: number;
  sensitivity?: string | null;
  open: OpenTarget;
}

export interface SourceList {
  total: number;
  items: SourceRow[];
  facets: {
    groups: { name: string; count: number }[];
    source_types: {
      name: string;
      name_ko: string;
      count: number;
      /** 이 종류가 속한 폴더(그룹). 서재 트리가 이걸로 갈린다 */
      group: string;
    }[];
    domains: string[];
    accounts: string[];
  };
}

export interface Excerpt {
  locator: string;
  where: string;
  text: string;
}

export interface SourceDetail extends SourceRow {
  /** 자료 상세에 끼우는 앞부분 발췌 (최대 60건) */
  preview: Excerpt[];
  /** 이 자료의 발췌 총 건수 */
  preview_total: number;
  /** 원문 보기가 쓰는 전체 발췌. 위치 순서로 정렬돼 있다. */
  all_excerpts: Excerpt[];
  /** 이 자료에서 뽑아낸 지식 */
  extracted: { label: string; label_ko: string; count: number }[];
  claims: ClaimRow[];
  entities: { id: string; type: string; name: string }[];
  evidence: EvidenceRow[];
  metadata: PropertyEntry[];
  subgraph: Subgraph;
}

export interface RunSummary {
  run_id: string;
  started_at?: string | null;
  ended_at?: string | null;
  source_count: number;
  node_count: number;
  edge_count: number;
  counts: CountEntry[];
}

export interface RunStep {
  at: string;
  source_id: string;
  source_title: string;
  source_type_ko: string;
  /** 이 자료를 처리하면서 새로 생긴 것 */
  added: CountEntry[];
  /** 이미 있던 것에 근거가 더 붙은 것 (근거 자료가 둘 이상인 것) */
  touched: CountEntry[];
}

export interface GraphChange {
  kind: "node" | "edge";
  id: string;
  /** node 면 라벨, edge 면 관계 이름 */
  type: string;
  type_ko: string;
  label: string;
  /** edge 일 때 양 끝 */
  from_label?: string;
  to_label?: string;
  status?: string | null;
}

export interface RunDetail extends RunSummary {
  steps: RunStep[];
  /** 이번 실행에서 새로 생긴 대표 노드·관계 */
  changes: GraphChange[];
  /** 이 실행이 만든 것 중 대표만 그린 관계도 */
  subgraph: Subgraph;
  /** 지원하지 않는 변경 종류를 화면이 있는 척 하지 않게 서버가 알려 준다 */
  limits: string[];
}

export interface ElementDetail {
  kind: "node" | "edge";
  id: string;
  type: string;
  type_ko: string;
  label: string;
  status?: string | null;
  status_reason?: string;
  properties: PropertyEntry[];
  sources: { source_id: string; title: string }[];
  evidence: EvidenceRow[];
  created_at?: string | null;
  updated_at?: string | null;
  subgraph: Subgraph;
}

export interface HealthIssue {
  /** 어디로 갈 수 있는지 (Explore/Library 링크용) */
  target_kind: "entity" | "source" | "none";
  target_id?: string;
  target_type?: string;
  label: string;
  detail: string;
}

export interface HealthGroup {
  key: string;
  title: string;
  why: string;
  count: number;
  items: HealthIssue[];
}

export interface HealthReport {
  groups: HealthGroup[];
  checked_at: string;
}

async function readError(res: Response): Promise<never> {
  let detail = "";
  try {
    const body = await res.json();
    detail =
      typeof body?.detail === "string" ? body.detail : JSON.stringify(body);
  } catch {
    detail = await res.text().catch(() => "");
  }
  throw new Error(
    `서버가 ${res.status} 로 답했습니다${detail ? ` — ${detail}` : ""}`,
  );
}

type QueryParams = Record<string, string | number | undefined>;

function query(params: QueryParams): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export type EntityQuery = QueryParams & {
  type?: string;
  q?: string;
  domain?: string;
  status?: string;
  limit?: number;
  offset?: number;
};

export type SourceQuery = QueryParams & {
  q?: string;
  group?: string;
  source_type?: string;
  domain?: string;
  account?: string;
  limit?: number;
  offset?: number;
};

export class BrowseApi {
  constructor(private readonly base: string) {}

  private async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const res = await fetch(`${this.base}${path}`, { signal });
    if (!res.ok) return readError(res);
    return res.json() as Promise<T>;
  }

  /** 자료 원본 파일을 서버가 도는 컴퓨터의 기본 앱으로 연다. 경로는 보내지 않는다. */
  async openSource(sourceId: string, signal?: AbortSignal) {
    const res = await fetch(`${this.base}/browse/open-source`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_id: sourceId }),
      signal,
    });
    if (!res.ok) return readError(res);
    return res.json() as Promise<{ opened: boolean; path: string }>;
  }

  overview(signal?: AbortSignal) {
    return this.get<BrowseOverview>("/browse/overview", signal);
  }

  entities(params: EntityQuery, signal?: AbortSignal) {
    return this.get<EntityList>(`/browse/entities${query(params)}`, signal);
  }

  entity(id: string, signal?: AbortSignal) {
    return this.get<EntityDetail>(
      `/browse/entity/${encodeURIComponent(id)}`,
      signal,
    );
  }

  sources(params: SourceQuery, signal?: AbortSignal) {
    return this.get<SourceList>(`/browse/sources${query(params)}`, signal);
  }

  source(id: string, signal?: AbortSignal) {
    return this.get<SourceDetail>(
      `/browse/source/${encodeURIComponent(id)}`,
      signal,
    );
  }

  runs(signal?: AbortSignal) {
    return this.get<{ runs: RunSummary[] }>("/browse/runs", signal);
  }

  run(id: string, signal?: AbortSignal) {
    return this.get<RunDetail>(`/browse/run/${encodeURIComponent(id)}`, signal);
  }

  element(id: string, kind: "node" | "edge", signal?: AbortSignal) {
    return this.get<ElementDetail>(
      `/browse/element${query({ id, kind })}`,
      signal,
    );
  }

  health(signal?: AbortSignal) {
    return this.get<HealthReport>("/browse/health", signal);
  }
}
