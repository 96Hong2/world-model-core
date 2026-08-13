import type { Scenario } from "../types/answer";
import { gqD3 } from "./gq-d3-b2b-gap";
import { gqD4 } from "./gq-d4-cross-bd-need";
import { gqD6 } from "./gq-d6-haneul-multitenant";
import { gqD8 } from "./gq-d8-guest-continuity";

// 목 시나리오 4종. 백엔드 없이 화면만 손볼 때 쓰는 개발용 폴백이고,
// VITE_DATA_SOURCE=mock 일 때만 MockEngine 이 이걸 읽는다(기본은 실 API).
export const SCENARIOS: Scenario[] = [gqD4, gqD3, gqD6, gqD8];

export function findScenario(id: string): Scenario | undefined {
  return SCENARIOS.find((s) => s.id === id);
}
