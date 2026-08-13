// 실 Answer API 클라이언트.
//
//   POST /ask            질문 → Answer JSON
//   POST /brief          Answer JSON → 정리본(결론·핵심 주장·할 일)
//   GET  /graph/expand   노드 1-hop 확장
//   GET  /golden         질문 칩 목록
//   GET  /health         그래프·합성기 상태
//
// 기본은 실 API 다. 목은 개발용 폴백이라 VITE_DATA_SOURCE=mock 으로만 켠다.
// 주소는 기본값 `/api` 이고 vite dev proxy 가 백엔드로 넘긴다(CORS 를 화면에서 다루지 않는다).

import type { AnswerPayload, ExpandPayload } from "../types/answer";
import type { Brief } from "../types/brief";

export type DataSource = "live" | "mock";

/** import.meta.env 처럼 문자열만 담긴 아무 객체. 테스트에서 그대로 넣을 수 있게 좁게 잡았다. */
export interface EnvLike {
  VITE_DATA_SOURCE?: string;
  VITE_API_BASE?: string;
}

export function resolveDataSource(env: EnvLike): DataSource {
  return (env.VITE_DATA_SOURCE ?? "").trim().toLowerCase() === "mock"
    ? "mock"
    : "live";
}

export function resolveApiBase(env: EnvLike): string {
  const raw = (env.VITE_API_BASE ?? "").trim();
  const base = raw === "" ? "/api" : raw;
  return base.endsWith("/") ? base.slice(0, -1) : base;
}

export function expandUrl(
  base: string,
  nodeId: string,
  already: number,
): string {
  const params = new URLSearchParams({
    node_id: nodeId,
    hops: "1",
    already: String(Math.max(0, already)),
  });
  return `${base}/graph/expand?${params.toString()}`;
}

export interface GoldenQuestion {
  id: string;
  question: string;
  type: string;
  expected_route: string;
}

export interface GoldenList {
  version: string;
  questions: GoldenQuestion[];
}

export interface HealthState {
  status: string;
  graph: { ok?: boolean; nodes?: number; edges?: number; error?: string };
  synthesizer: string;
}

/** 서버가 보낸 오류 문구를 그대로 살려서 던진다. 화면이 원인을 감추지 않게 한다. */
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

export class AnswerApi {
  constructor(private readonly base: string) {}

  async health(signal?: AbortSignal): Promise<HealthState> {
    const res = await fetch(`${this.base}/health`, { signal });
    if (!res.ok) return readError(res);
    return res.json();
  }

  async golden(signal?: AbortSignal): Promise<GoldenList> {
    const res = await fetch(`${this.base}/golden`, { signal });
    if (!res.ok) return readError(res);
    return res.json();
  }

  async ask(question: string, signal?: AbortSignal): Promise<AnswerPayload> {
    const res = await fetch(`${this.base}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal,
    });
    if (!res.ok) return readError(res);
    return res.json();
  }

  /** 답변을 그대로 되돌려 보낸다. 서버가 상태를 들고 있지 않아도 되고, 근거 검사에 쓸
   *  발췌가 그 안에 이미 다 있다(`api/distill.py` 의 `/brief` 설명). */
  async brief(
    question: string,
    answer: AnswerPayload,
    signal?: AbortSignal,
  ): Promise<Brief> {
    const res = await fetch(`${this.base}/brief`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, answer }),
      signal,
    });
    if (!res.ok) return readError(res);
    return res.json();
  }

  async expand(
    nodeId: string,
    already: number,
    signal?: AbortSignal,
  ): Promise<ExpandPayload> {
    const res = await fetch(expandUrl(this.base, nodeId, already), { signal });
    if (!res.ok) return readError(res);
    return res.json();
  }
}
