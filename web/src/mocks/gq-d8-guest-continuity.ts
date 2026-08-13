import type { Scenario } from "../types/answer";

// GQ-D8 (eval/golden.yaml). Product fact.
// 기능맵이 이 사실의 정본이라 1A 에서 VERIFIED Claim 이 될 수 있는 몇 안 되는 문항이다.
// 작은 Subgraph 라 노드 위계(focal > cited > supporting) 대비가 잘 보인다.

export const gqD8: Scenario = {
  id: "GQ-D8",
  question:
    "비회원 고객이 같은 기기로 재방문하면 상담이 이어지나? 언제부터 되나?",
  demoNote:
    "VERIFIED 실선과 정본(source_of_record_for) 배지를 본다. 근거 1건, 작은 그래프.",
  answer: {
    answer: {
      text: '이어집니다. 비회원 고객이 같은 기기로 재방문하면 진행 중인 상담을 이어서 계속할 수 있습니다[1]. 이 "비회원 상담 연속성 유지" 기능은 Guest Zone(고객 화면) 의 상담 대화접수 아래 편의성 제공 항목이고, 버전 표기상 2.0.0 부터 제공됩니다[1].',
    },
    citations: [
      {
        marker: 1,
        evidence_id: "ev_wrrvcdfwmglnfsabopjbtcxu",
        quoted_span: "비회원 상담 연속성 유지",
      },
    ],
    evidence: [
      {
        evidence_id: "ev_wrrvcdfwmglnfsabopjbtcxu",
        source_id: "src_featuremap_v21",
        locator: "v2.1!E15",
        snippet:
          "Guest Zone(고객 화면) > 상담 대화접수 > 편의성 제공 | 비회원 상담 연속성 유지: 비회원 고객이 같은 기기로 재방문하면 진행 중인 상담을 이어서 계속할 수 있습니다.",
        authority_label: {
          tier: "T1",
          claim_domain: "product_behavior",
          source_type: "release_spec",
          policy_version: "1.0.0",
          source_of_record_for: "제품 기능의 존재와 도입 버전(introduced_in)",
        },
        observed_at: "2026-08-05",
        extractor: "deterministic",
        masked: true,
      },
    ],
    evidence_strength: {
      band: "HIGH",
      basis: {
        independent_evidence: 1,
        highest_authority: "release_spec",
        contradiction: "none",
        recency: "current",
        source_type_variety: 1,
      },
    },
    unknowns: [],
    gaps: [],
    claims: [
      {
        claim_id: "clm_guest_continuity_2_0_0",
        statement: "비회원 상담 연속성 유지는 2.0.0 버전부터 제공된다",
        status: "VERIFIED",
        lane: ["default"],
        claim_kind: "product_spec",
        evidence_ids: ["ev_wrrvcdfwmglnfsabopjbtcxu"],
        temporal: { valid_from: "2026-08-05", stale_flag: false },
      },
    ],
    subgraph: {
      nodes: [
        {
          id: "feat_guest_continuity",
          labels: ["Feature"],
          label_text: "비회원 상담 연속성 유지",
          rank: "focal",
          status: "VERIFIED",
          citation_markers: [1],
        },
        {
          id: "clm_guest_continuity_2_0_0",
          labels: ["Claim"],
          label_text: "비회원 상담 연속성 유지는 2.0.0 버전부터 제공된다",
          rank: "cited",
          status: "VERIFIED",
          citation_markers: [1],
        },
        {
          id: "prod_auroraworks",
          labels: ["Product"],
          label_text: "오로라웍스",
          rank: "supporting",
          status: null,
          citation_markers: [],
        },
        {
          id: "cap_guest_zone",
          labels: ["Capability"],
          label_text: "Guest Zone(고객 화면) 상담 대화접수",
          rank: "supporting",
          status: "CANDIDATE",
          citation_markers: [1],
        },
        {
          id: "src_featuremap",
          labels: ["Source"],
          label_text: "오로라웍스 기능맵 v2.1.xlsx",
          rank: "supporting",
          status: null,
          citation_markers: [1],
        },
      ],
      edges: [
        {
          from: "feat_guest_continuity",
          to: "prod_auroraworks",
          type: "BELONGS_TO",
          claim_ids: ["clm_guest_continuity_2_0_0"],
          status: "VERIFIED",
          cited: true,
        },
        {
          from: "clm_guest_continuity_2_0_0",
          to: "feat_guest_continuity",
          type: "ABOUT",
          status: "VERIFIED",
          cited: true,
        },
        {
          from: "clm_guest_continuity_2_0_0",
          to: "src_featuremap",
          type: "FROM_SOURCE",
          status: null,
          cited: true,
        },
        {
          from: "cap_guest_zone",
          to: "feat_guest_continuity",
          type: "IMPLEMENTS",
          status: "CANDIDATE",
          cited: false,
        },
      ],
      truncated: false,
    },
    route: {
      retriever: "Q-E",
      matched_rule: "entity_lookup:Feature",
      llm_classifier_used: false,
      claim_domain: "product_behavior",
    },
    notices: {
      results_may_be_incomplete: false,
      critical_unverified_included: false,
      raw_signal_count: 0,
    },
    query_id: "mock-gq-d8",
    policy_version: "1.0.0",
    answered_at: "2026-08-08T09:06:00+09:00",
  },
  expansions: {
    feat_guest_continuity: {
      nodes: [
        {
          id: "feat_guest_form",
          labels: ["Feature"],
          label_text: "상담 접수 전 사전 정보 입력",
          rank: "supporting",
          status: "CANDIDATE",
          expanded: true,
        },
        {
          id: "feat_guest_notice",
          labels: ["Feature"],
          label_text: "상담 대기 안내",
          rank: "supporting",
          status: "CANDIDATE",
          expanded: true,
        },
      ],
      edges: [
        {
          from: "feat_guest_form",
          to: "prod_auroraworks",
          type: "BELONGS_TO",
          status: "CANDIDATE",
          cited: false,
        },
        {
          from: "feat_guest_notice",
          to: "prod_auroraworks",
          type: "BELONGS_TO",
          status: "CANDIDATE",
          cited: false,
        },
        {
          from: "cap_guest_zone",
          to: "feat_guest_form",
          type: "IMPLEMENTS",
          status: "CANDIDATE",
          cited: false,
        },
      ],
    },
  },
};
