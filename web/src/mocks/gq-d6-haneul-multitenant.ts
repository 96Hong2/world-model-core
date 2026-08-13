import type { Scenario } from "../types/answer";

// GQ-D6 (eval/golden.yaml). Rare critical.
// 이 사실은 문서 23종 중 하늘IT 메모 한 곳에만 있다. 반대로 온보딩 문서는 제품을
// "센터 기반 멀티테넌시"로 소개해서 키워드 검색이면 "이미 지원한다"는 오답이 나온다.
// 그 두 신호를 raw_signals("추가 원문 근거")로 분리 표시해 오답을 막는 것이 이 시나리오의 핵심이다.

export const gqD6: Scenario = {
  id: "GQ-D6",
  question:
    "하늘IT와 구독 사업을 추진하려면 반드시 해결해야 할 기술 전제는 무엇인가?",
  demoNote:
    'CRITICAL/UNVERIFIED 노드 강조와 "추가 원문 근거"(구조화 Knowledge 미반영 신호) 구분 표시를 본다. Gap 은 CONFIRMED.',
  answer: {
    answer: {
      text: "멀티테넌트 구조 개발입니다. 하늘IT 검토 문서는 구독으로 가려면 멀티테넌트 구조를 새로 개발해야 하고 SaaS 로 이야기된 부분은 검토 후 협의가 필요하다고 적습니다[1]. 개발 범위는 유저 라이선스 구조까지 포함해 작지 않다고 보고 있으며, 현 구조가 교보와 오로라소프트가 한서버에서 여러 고객을 대응하는 형태라 확인이 필요한 상태로 남아 있습니다[1]. 사업화 방향 자체가 구축과 구독 두 형태를 놓고 검토 중이라[2], 이 전제가 풀리기 전에는 구독 판매 정책·수익 배분 논의도 확정할 수 없습니다[3].",
      recommendation:
        "구독 가격 정책을 정하기 전에 멀티테넌트 개발 범위와 현 서버 구조부터 확정하는 것이 순서입니다. 이 항목은 자료 한 곳에만 있어 사라지기 쉬우니 별도로 추적하십시오.",
    },
    citations: [
      {
        marker: 1,
        evidence_id: "ev_6faa4652596e62c6",
        quoted_span: "멀티테넌트 구조를 새로 개발해야",
      },
      {
        marker: 2,
        evidence_id: "ev_32fe3b7cf577e03f",
        quoted_span: "구축과 구독 두 형태",
      },
      {
        marker: 3,
        evidence_id: "ev_5a9d2093318fd4de",
        quoted_span: "구독 판매 정책·수익 배분",
      },
    ],
    evidence: [
      {
        evidence_id: "ev_6faa4652596e62c6",
        source_id: "src_doc_haneul_it_memo",
        locator: "p.2",
        snippet:
          "고려사항\n\n  ●​ 멀티테넌트 구조 개발 필요 (Saas로 이야기되었는데 이 부분 검토 후 협의 필요)\n       ○​ 내부구조는 변경 가능\n       ○​ 개발 필요 : 유저 라이선스 구조 등 고려하면 범위가 작진 않음.\n            ■​ 확인 필요 현 구조 (교보+오로라소프트 한서버에서 멀티 고객 대응)\n  ●​ 운영 조직 필요\n  ●​ 고객별 커스터마이징 제한",
        authority_label: {
          tier: "T2",
          claim_domain: "product_intent",
          source_type: "internal_memo",
          policy_version: "1.0.0",
          source_of_record_for: "하늘IT 협업의 미해결 전제",
          caveat: "실동작 최고권위(production_signal) 부재",
        },
        extractor: "deterministic",
        masked: true,
      },
      {
        evidence_id: "ev_32fe3b7cf577e03f",
        source_id: "src_doc_haneul_it_memo",
        locator: "p.1#2",
        snippet:
          "3. 핵심 의사결정 사항 #1\n구독 사업 모델 추진 여부\n\n현재 하늘IT와 통합 솔루션을 구축/구독 두 가지 형태로 사업화하는 방향을 검토중",
        authority_label: {
          tier: "T2",
          claim_domain: "product_intent",
          source_type: "internal_memo",
          policy_version: "1.0.0",
        },
        extractor: "deterministic",
        masked: true,
      },
      {
        evidence_id: "ev_5a9d2093318fd4de",
        source_id: "src_doc_haneul_it_memo",
        locator: "p.3",
        snippet:
          "검토 필요\n\n  ●​   하늘IT와 수익 배분 구조\n  ●​   구축형 판매 정책 (여기서 논의할 사항 아님 개별 사업에서 정리)\n  ●​   구독 판매 정책 (마켓플리이스 + 단독구독사업)\n  ●​   유지보수 정책 (비용은 구독료 포함, SLA 등 정책 상호 협의 필요)",
        authority_label: {
          tier: "T2",
          claim_domain: "product_intent",
          source_type: "internal_memo",
          policy_version: "1.0.0",
        },
        extractor: "deterministic",
        masked: true,
      },
    ],
    evidence_strength: {
      band: "MEDIUM",
      basis: {
        independent_evidence: 1,
        highest_authority: "internal_memo",
        contradiction: "none",
        recency: "current",
        source_type_variety: 1,
      },
    },
    unknowns: [
      "멀티테넌트 개발 일정과 담당 조직은 확인된 자료가 없습니다.",
      "하늘IT 와의 수익 배분 비율은 검토 항목으로만 적혀 있고 결정된 값이 없습니다.",
    ],
    raw_signals: [
      {
        evidence_id: "ev_c38e6d47ce702ffd",
        source_id: "src_repo_product_md",
        locator: "docs/product.md#Overview",
        snippet:
          "Overview\n\n**오로라 스위트 오로라웍스**는 기업용 AI 에이전트 플랫폼 + CS(고객 지원) 협업 시스템입니다.\n- 제품명: 오로라 스위트 오로라웍스\n- 운영사: AuroraSoft\n- 배포 형태: On-premise 또는 SaaS (센터 기반 멀티테넌시)",
        match_terms: ["멀티테넌시"],
        in_graph: false,
      },
      {
        evidence_id: "ev_cb08f54387992c37",
        source_id: "src_repo_kafka_topics",
        locator:
          "docs/codebase/kafka-topics.md#Kafka 토픽 중앙 레지스트리",
        snippet:
          "> 토픽 상수는 `{prefix}EventTopics.java`, `{prefix}CommandTopics.java` 형태로 정의된다.\n> 실제 토픽 이름에는 멀티테넌시 prefix(`KafkaTenant.PREFIX`)가 런타임에 추가된다.",
        match_terms: ["멀티테넌시"],
        in_graph: false,
      },
    ],
    gaps: [
      {
        subject: "구독 사업에 필요한 멀티테넌트 구조",
        verdict: "CONFIRMED",
        basis: {
          reason:
            "구독으로 가려면 개발이 필요하다고 문서가 직접 적고 있습니다. 연결이 없어서가 아니라 부재를 적은 근거가 있어서 확정입니다.",
          need_evidence_ids: ["ev_32fe3b7cf577e03f"],
          capability_evidence_ids: [],
          explicit_absence_evidence_ids: ["ev_6faa4652596e62c6"],
          out_of_scope_by_design: false,
        },
      },
    ],
    claims: [
      {
        claim_id: "clm_haneul_multitenant",
        statement: "구독 사업 추진에는 멀티테넌트 구조 개발이 선행되어야 한다",
        status: "CRITICAL",
        lane: ["critical"],
        claim_kind: "strategic_judgment",
        evidence_ids: ["ev_6faa4652596e62c6"],
      },
      {
        claim_id: "clm_haneul_license_scope",
        statement: "개발 범위는 유저 라이선스 구조를 포함해 작지 않다",
        status: "UNVERIFIED",
        lane: ["critical"],
        claim_kind: "interpretation",
        evidence_ids: ["ev_6faa4652596e62c6"],
      },
    ],
    subgraph: {
      nodes: [
        {
          id: "acct_haneul",
          labels: ["Account"],
          label_text: "하늘IT",
          rank: "focal",
          status: null,
          citation_markers: [1, 2, 3],
        },
        {
          id: "cap_multitenant",
          labels: ["Capability"],
          label_text: "멀티테넌트 구조",
          rank: "cited",
          status: "CRITICAL",
          citation_markers: [1],
        },
        {
          id: "cap_user_license",
          labels: ["Capability"],
          label_text: "유저 라이선스 구조",
          rank: "cited",
          status: "UNVERIFIED",
          citation_markers: [1],
        },
        {
          id: "need_subscription",
          labels: ["Need"],
          label_text: "통합 솔루션을 구독(SaaS) 형태로 판매",
          rank: "cited",
          status: "CANDIDATE",
          citation_markers: [2],
        },
        {
          id: "need_revenue_share",
          labels: ["Need"],
          label_text: "하늘IT 와의 수익 배분 구조 확정",
          rank: "cited",
          status: "CANDIDATE",
          citation_markers: [3],
        },
        {
          id: "cap_ops_org",
          labels: ["Capability"],
          label_text: "구독 운영 조직",
          rank: "supporting",
          status: "CANDIDATE",
          citation_markers: [1],
        },
        {
          id: "prod_auroraworks",
          labels: ["Product"],
          label_text: "오로라웍스 통합 솔루션",
          rank: "supporting",
          status: null,
          citation_markers: [],
        },
        {
          id: "clm_haneul_multitenant",
          labels: ["Claim"],
          label_text:
            "구독 사업 추진에는 멀티테넌트 구조 개발이 선행되어야 한다",
          rank: "cited",
          status: "CRITICAL",
          citation_markers: [1],
        },
        {
          id: "src_haneul_memo",
          labels: ["Source"],
          label_text: "하늘IT 협업 관련 검토 사항.pdf",
          rank: "supporting",
          status: null,
          citation_markers: [1, 2, 3],
        },
      ],
      edges: [
        {
          from: "acct_haneul",
          to: "need_subscription",
          type: "HAS_NEED",
          claim_ids: ["clm_haneul_subscription"],
          status: "CANDIDATE",
          cited: true,
        },
        {
          from: "acct_haneul",
          to: "need_revenue_share",
          type: "HAS_NEED",
          claim_ids: ["clm_haneul_revenue"],
          status: "CANDIDATE",
          cited: true,
        },
        {
          from: "need_subscription",
          to: "cap_multitenant",
          type: "BLOCKED_BY",
          claim_ids: ["clm_haneul_multitenant"],
          status: "CRITICAL",
          cited: true,
        },
        {
          from: "need_subscription",
          to: "cap_user_license",
          type: "BLOCKED_BY",
          claim_ids: ["clm_haneul_license_scope"],
          status: "UNVERIFIED",
          cited: true,
        },
        {
          from: "need_subscription",
          to: "cap_ops_org",
          type: "BLOCKED_BY",
          claim_ids: ["clm_haneul_ops"],
          status: "CANDIDATE",
          cited: false,
        },
        {
          from: "cap_multitenant",
          to: "prod_auroraworks",
          type: "FOR_PRODUCT",
          status: "CRITICAL",
          cited: true,
        },
        {
          from: "clm_haneul_multitenant",
          to: "cap_multitenant",
          type: "ABOUT",
          status: "CRITICAL",
          cited: true,
        },
        {
          from: "clm_haneul_multitenant",
          to: "src_haneul_memo",
          type: "FROM_SOURCE",
          status: null,
          cited: true,
        },
        {
          from: "acct_haneul",
          to: "src_haneul_memo",
          type: "MENTIONS",
          status: null,
          cited: true,
        },
      ],
      truncated: false,
    },
    route: {
      retriever: "Q-E",
      matched_rule: "entity_lookup:Account+Capability",
      llm_classifier_used: false,
      claim_domain: "product_intent",
    },
    next_actions: [
      "멀티테넌트 개발 범위(유저 라이선스 포함)를 견적 수준으로 산정하기",
      "현 서버 구조(교보+오로라소프트 한서버 멀티 고객)를 개발팀과 확인하기",
    ],
    notices: {
      results_may_be_incomplete: false,
      critical_unverified_included: true,
      raw_signal_count: 2,
    },
    query_id: "mock-gq-d6",
    policy_version: "1.0.0",
    answered_at: "2026-08-08T09:04:00+09:00",
  },
  expansions: {
    acct_haneul: {
      nodes: [
        {
          id: "need_aws_box",
          labels: ["Need"],
          label_text: "AWS BOX 프로그램 공동 신청·지원금 활용",
          rank: "supporting",
          status: "CANDIDATE",
          expanded: true,
        },
        {
          id: "need_joint_booth",
          labels: ["Need"],
          label_text: "AI Summit·EXPO 공동 부스 참가",
          rank: "supporting",
          status: "CANDIDATE",
          expanded: true,
        },
      ],
      edges: [
        {
          from: "acct_haneul",
          to: "need_aws_box",
          type: "HAS_NEED",
          status: "CANDIDATE",
          cited: false,
        },
        {
          from: "acct_haneul",
          to: "need_joint_booth",
          type: "HAS_NEED",
          status: "CANDIDATE",
          cited: false,
        },
      ],
    },
    cap_multitenant: {
      nodes: [
        {
          id: "need_custom_limit",
          labels: ["Need"],
          label_text: "고객별 커스터마이징 제한(멀티테넌트에서는 커스텀 불가)",
          rank: "supporting",
          status: "CANDIDATE",
          expanded: true,
        },
      ],
      edges: [
        {
          from: "cap_multitenant",
          to: "need_custom_limit",
          type: "BLOCKED_BY",
          status: "CANDIDATE",
          cited: false,
        },
      ],
    },
  },
};
