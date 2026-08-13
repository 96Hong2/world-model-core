// 정리본(Brief). `POST /brief` 가 돌려주는 형태이고 정본은 `api/distill.py` 다.
//
// 화면은 이 구조만 보고 그린다. LLM 이 쓴 마크다운 문장 구조에 레이아웃이 기대지 않게
// 하려는 것이다. 예전에는 답변 본문 한 덩어리를 그대로 찍어서, 문장이 길어지면 결론과
// 근거와 권고가 한 화면에 같은 굵기로 쏟아졌다.
//
// Answer 계약(`types/answer.ts`)과 겹치는 것은 담지 않는다. 근거·출처·모르는 것·공백은
// 답변 payload 에 이미 있고, 화면은 그것을 상세 영역에서 그대로 쓴다. 여기에는 **정리기가
// 새로 만든 배치**만 있다.

import type { AnswerPayload } from "./answer";

export interface BriefKeyPoint {
  /** 12자 이내 짧은 제목 */
  title: string;
  /** 무슨 일인지 한 문장 */
  claim: string;
  /** 그래서 무엇을 뜻하는지. 원문에 그 판단이 없으면 빈 문자열 */
  reason: string;
  /** claim·reason 에 남아 있는 각주 번호. 서버가 세어 준다(모델이 적지 않는다) */
  citation_markers: number[];
}

export interface Brief {
  /** `raw` 는 정리하지 못해 원문을 그대로 실은 상태다. 화면이 그 사실을 밝힌다 */
  mode: "distilled" | "raw";
  conclusion: { title: string; summary: string };
  key_points: BriefKeyPoint[];
  /** sequence: 원문이 말한 제안 순서 · next: 사용자가 다음에 할 일 */
  actions: { sequence: string[]; next: string[] };
  /** 결론을 뒤집을 만한 불확실성만 */
  caveats: string[];
  /** 정리 전 답변 원문. 상세 영역에서 언제든 대조할 수 있게 항상 싣는다 */
  raw_text: string;
  raw_recommendation: string;
  /** raw 로 내려간 이유 */
  note: string;
  meta: {
    llm_used: boolean;
    cost_usd: number;
    cache_hit: boolean;
    /** 근거 검사에서 버린 핵심 주장 수 */
    dropped: number;
    /** 각주를 되붙인 뒤에도 번호가 없는 문장 수. 0 이어야 한다(근거 추적 100%) */
    untraced: number;
  };
}

/** 문장 경계. 마침표 뒤가 공백일 때만 끊는다(`2.1.0` 이 쪼개지지 않게). */
const SENTENCE = /(?<=[.!?])\s+/;

/**
 * 정리본을 아직 못 받았을 때 쓰는 정리 전 상태.
 *
 * 서버의 `mode="raw"` 와 같은 모양을 만들어, 화면이 "정리본 있음/없음" 두 갈래를 그리지
 * 않게 한다. 문장을 새로 만들지 않는다 — 원문 앞 두 문장을 결론 자리에 올릴 뿐이다.
 */
export function rawBrief(answer: AnswerPayload, note: string): Brief {
  const text = answer.answer.text ?? "";
  const lead = text
    .replace(/\n+/g, " ")
    .split(SENTENCE)
    .slice(0, 2)
    .join(" ")
    .trim();
  return {
    mode: "raw",
    conclusion: { title: "", summary: lead },
    key_points: [],
    actions: { sequence: [], next: answer.next_actions ?? [] },
    caveats: [],
    raw_text: text,
    raw_recommendation: answer.answer.recommendation ?? "",
    note,
    meta: {
      llm_used: false,
      cost_usd: 0,
      cache_hit: false,
      dropped: 0,
      untraced: 0,
    },
  };
}
