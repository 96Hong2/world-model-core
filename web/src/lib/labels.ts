// 계약의 enum 값을 화면에 쓸 한국어로 바꾼다.
// 뜻은 contracts/source_type.enum.json 의 x-catalog · answer.schema.json 설명에서 가져왔다.
// 근거 없이 지어낸 설명을 넣지 않는다.

import type { ClaimDomain, Status } from "../types/answer";

export const SOURCE_TYPE_KO: Record<string, string> = {
  release_spec: "기능맵(제품 기능 정본)",
  sales_activity_log: "영업 활동일지",
  sales_weekly_plan: "주간 영업 계획",
  bd_registry: "BD 레지스트리(사업영역 정본)",
  bd_registry_aux: "BD 분류 보조 문서",
  pain_registry: "고객사 Pain Point 관리대장",
  bd_openbook: "BD 오픈북",
  proposal: "제안서",
  product_brochure: "제품 소개 자료",
  user_manual: "사용자 매뉴얼",
  internal_memo: "내부 검토 메모",
  internal_analysis: "내부 분석 자료",
  internal_deck: "내부 발표 자료",
  customer_internal_report: "고객사 내부 문서",
  slack_thread: "슬랙 쓰레드",
  repo_doc: "저장소 문서",
  product_doc: "구축·설정 가이드",
  glossary: "용어집",
  code: "소스 코드",
  test: "테스트 코드",
  compliance_checklist: "법규 점검표",
  ai_eval_dataset: "AI 품질 평가 기록",
  architecture_spec: "아키텍처 명세",
  production_signal: "운영 환경 실동작",
  web_official: "외부 공식 출처",
};

export function sourceTypeKo(type: string): string {
  return SOURCE_TYPE_KO[type] ?? type;
}

/** authority tier. 같은 주장 유형 안에서의 순서만 뜻한다(정책 스키마 tiers 설명). */
export const TIER_KO: Record<string, string> = {
  T1: "T1 · 이 주제의 정본",
  T2: "T2 · 1차 기록",
  T3: "T3 · 2차 자료",
  T4: "T4 · 참고",
  T5: "T5 · 약한 근거",
};

export const CLAIM_DOMAIN_KO: Record<ClaimDomain, string> = {
  product_behavior: "제품이 실제로 어떻게 동작하는가",
  product_intent: "제품을 어떻게 하겠다고 했는가",
  customer_need: "고객이 무엇을 필요로 하는가",
  deal_fact: "딜에서 무슨 일이 있었는가",
  market_fact: "시장 사실",
};

export const STATUS_KO: Record<Status, string> = {
  VERIFIED: "확인됨",
  CANDIDATE: "후보",
  CRITICAL: "중요(놓치면 안 됨)",
  UNVERIFIED: "미검증",
  DISPUTED: "자료끼리 어긋남",
  SUPERSEDED: "옛 버전",
  ARCHIVED: "보관",
};

export function statusTone(status: Status | null | undefined): string {
  switch (status) {
    case "VERIFIED":
      return "ok";
    case "CRITICAL":
      return "critical";
    case "DISPUTED":
      return "disputed";
    case "CANDIDATE":
    case "UNVERIFIED":
      return "warn";
    default:
      return "neutral";
  }
}

export const RETRIEVER_KO: Record<string, string> = {
  "Q-E": "Q-E · 단순 사실 조회",
  "Q-M": "Q-M · 여러 단계 연결 조회",
  "Q-S": "Q-S · 전략 질문 합성",
};

export const RECENCY_KO: Record<string, string> = {
  current: "최신",
  aging: "조금 지남",
  stale: "오래됨",
  unknown: "확인 불가",
};

export const CONTRADICTION_KO: Record<string, string> = {
  none: "없음",
  detected: "발견됨",
};

export const BAND_KO: Record<string, string> = {
  HIGH: "높음",
  MEDIUM: "보통",
  LOW: "낮음",
};

export const BAND_WHY: Record<string, string> = {
  HIGH: "정본급 자료가 여러 건 서로 어긋나지 않게 같은 이야기를 합니다.",
  MEDIUM: "근거는 있지만 자료 수나 종류가 적거나, 일부가 오래됐습니다.",
  LOW: "근거가 한쪽에만 있거나 서로 어긋납니다. 사람이 한 번 더 확인해야 합니다.",
};

export const GAP_KO: Record<
  string,
  { title: string; tone: string; hint: string }
> = {
  CONFIRMED: {
    title: "확정된 공백",
    tone: "critical",
    hint: "없다고 적은 근거가 실제로 있습니다.",
  },
  POSSIBLE: {
    title: "공백 가능성",
    tone: "warn",
    hint: "요구는 확인됐지만 대응 근거를 못 찾았습니다. 없다는 뜻은 아닙니다.",
  },
  UNKNOWN: {
    title: "판단 자료 부족",
    tone: "neutral",
    hint: "있다·없다를 가릴 자료 자체가 없습니다.",
  },
};
