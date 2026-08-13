// 화면이 질문·확장·질문목록을 얻는 통로 하나.
//
// 기본은 실 API(LiveEngine)다. 목(MockEngine)은 백엔드를 못 띄우는 자리에서만 쓰는
// 개발용 폴백이고 VITE_DATA_SOURCE=mock 으로만 켜진다. 화면 코드는 둘을 구분하지 않는다.

import type { AnswerPayload, ExpandPayload } from "../types/answer";
import type { Brief } from "../types/brief";
import { rawBrief } from "../types/brief";
import { AnswerApi } from "./client";
import type { GoldenQuestion, HealthState } from "./client";
import { SCENARIOS } from "../mocks";

export interface Engine {
  readonly kind: "live" | "mock";
  health(signal?: AbortSignal): Promise<HealthState>;
  golden(signal?: AbortSignal): Promise<GoldenQuestion[]>;
  ask(question: string, signal?: AbortSignal): Promise<AnswerPayload>;
  /** 답변을 읽는 순서로 다시 배치한 정리본 */
  brief(
    question: string,
    answer: AnswerPayload,
    signal?: AbortSignal,
  ): Promise<Brief>;
  expand(
    nodeId: string,
    already: number,
    signal?: AbortSignal,
  ): Promise<ExpandPayload>;
}

export class LiveEngine implements Engine {
  readonly kind = "live" as const;
  private readonly api: AnswerApi;

  constructor(base: string) {
    this.api = new AnswerApi(base);
  }

  health(signal?: AbortSignal) {
    return this.api.health(signal);
  }

  async golden(signal?: AbortSignal) {
    return (await this.api.golden(signal)).questions;
  }

  ask(question: string, signal?: AbortSignal) {
    return this.api.ask(question, signal);
  }

  brief(question: string, answer: AnswerPayload, signal?: AbortSignal) {
    return this.api.brief(question, answer, signal);
  }

  expand(nodeId: string, already: number, signal?: AbortSignal) {
    return this.api.expand(nodeId, already, signal);
  }
}

export class MockEngine implements Engine {
  readonly kind = "mock" as const;

  async health(): Promise<HealthState> {
    return {
      status: "mock",
      graph: { ok: true, nodes: 0, edges: 0 },
      synthesizer: "mock",
    };
  }

  async golden(): Promise<GoldenQuestion[]> {
    return SCENARIOS.map((s) => ({
      id: s.id,
      question: s.question,
      type: "",
      expected_route: s.answer.route.retriever,
    }));
  }

  async ask(question: string): Promise<AnswerPayload> {
    const hit = SCENARIOS.find((s) => s.question === question);
    if (!hit) {
      throw new Error(
        "목 모드에는 준비된 질문 4개만 있습니다. 실 API 로 물어보려면 VITE_DATA_SOURCE 를 지우고 다시 띄우세요.",
      );
    }
    return hit.answer;
  }

  /** 목에는 정리기가 없다. 원문을 그대로 실은 상태로 돌려주고 화면이 그 사실을 밝힌다. */
  async brief(_question: string, answer: AnswerPayload): Promise<Brief> {
    return rawBrief(
      answer,
      "목 모드에는 정리기가 없어 답변 원문을 그대로 싣습니다.",
    );
  }

  async expand(nodeId: string): Promise<ExpandPayload> {
    for (const s of SCENARIOS) {
      const payload = s.expansions[nodeId];
      if (payload) return payload;
    }
    return { nodes: [], edges: [] };
  }
}
