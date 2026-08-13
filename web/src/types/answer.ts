// contracts/answer.schema.json 의 TypeScript 표현.
// 스키마가 정본이고 이 파일은 그 사본이다. 빌드 시 scripts/validate-mocks.mjs 가
// 목 payload 를 실제 스키마로 검증하므로 둘이 어긋나면 빌드가 깨진다.

export type AuthorityTier = "T1" | "T2" | "T3" | "T4" | "T5";

export type ClaimDomain =
  | "product_behavior"
  | "product_intent"
  | "customer_need"
  | "deal_fact"
  | "market_fact";

export type Status =
  | "VERIFIED"
  | "CANDIDATE"
  | "CRITICAL"
  | "UNVERIFIED"
  | "DISPUTED"
  | "SUPERSEDED"
  | "ARCHIVED";

export type NodeRank = "focal" | "cited" | "supporting";

export interface AuthorityLabel {
  tier: AuthorityTier;
  claim_domain: ClaimDomain;
  source_type: string;
  policy_version: string;
  source_of_record_for?: string;
  caveat?: string;
}

export interface Evidence {
  evidence_id: string;
  source_id: string;
  locator: string;
  snippet: string;
  authority_label: AuthorityLabel;
  observed_at?: string | null;
  extractor?: "deterministic" | "vision";
  masked?: boolean;
}

export interface Citation {
  marker: number;
  evidence_id: string;
  quoted_span?: string;
}

export interface EvidenceStrength {
  band: "HIGH" | "MEDIUM" | "LOW";
  basis: {
    independent_evidence: number;
    highest_authority: string;
    contradiction: "none" | "detected";
    recency: "current" | "aging" | "stale" | "unknown";
    source_type_variety?: number;
  };
}

export interface RawSignal {
  evidence_id: string;
  source_id: string;
  locator: string;
  snippet: string;
  match_terms?: string[];
  in_graph?: boolean;
}

export interface Gap {
  subject: string;
  verdict: "CONFIRMED" | "POSSIBLE" | "UNKNOWN";
  basis: {
    reason: string;
    need_evidence_ids?: string[];
    capability_evidence_ids?: string[];
    explicit_absence_evidence_ids?: string[];
    out_of_scope_by_design?: boolean;
  };
}

export interface Dispute {
  subject: string;
  sides: { statement: string; evidence_ids: string[] }[];
}

export interface Claim {
  claim_id: string;
  statement: string;
  status: Status;
  lane?: ("default" | "critical" | "conflict")[];
  claim_kind?: string;
  evidence_ids?: string[];
  temporal?: {
    valid_from?: string | null;
    valid_until?: string | null;
    last_verified_at?: string | null;
    stale_flag?: boolean;
  };
  contradicted_by?: string[];
}

export interface SubgraphNode {
  id: string;
  labels: string[];
  label_text: string;
  rank: NodeRank;
  status?: Status | null;
  citation_markers?: number[];
  expanded?: boolean;
}

export interface SubgraphEdge {
  from: string;
  to: string;
  type: string;
  claim_ids?: string[];
  status?: Status | null;
  cited?: boolean;
}

export interface Subgraph {
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
  truncated?: boolean;
}

export interface AnswerPayload {
  answer: {
    text: string;
    recommendation?: string;
    estimated_parts?: string[];
  };
  citations: Citation[];
  evidence: Evidence[];
  evidence_strength: EvidenceStrength;
  unknowns: string[];
  raw_signals?: RawSignal[];
  gaps: Gap[];
  disputes?: Dispute[];
  claims?: Claim[];
  subgraph: Subgraph;
  route: {
    retriever: "Q-E" | "Q-M" | "Q-S";
    matched_rule?: string;
    llm_classifier_used?: boolean;
    claim_domain?: ClaimDomain;
    domain_classification_failed?: boolean;
  };
  next_actions?: string[];
  notices: {
    results_may_be_incomplete: boolean;
    stale_labeled_count?: number;
    critical_unverified_included?: boolean;
    raw_signal_count?: number;
  };
  query_id?: string;
  policy_version?: string;
  answered_at?: string;
}

/** 1-hop expand 응답. GET /graph/expand 가 돌려줄 형태와 같은 모양으로 목을 만든다. */
export interface ExpandPayload {
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
}

/** 목 시나리오 1건. 실 API 를 못 쓸 때 MockEngine 이 answer·expansions 를 대신 내준다. */
export interface Scenario {
  id: string;
  question: string;
  /** 이 시나리오가 어떤 UI 요구를 보여주는지 (데모 진행용 메모) */
  demoNote: string;
  answer: AnswerPayload;
  /** nodeId → 1-hop 확장 결과 */
  expansions: Record<string, ExpandPayload>;
}
