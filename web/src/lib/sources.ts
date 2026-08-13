// 자료(Source) 조회. 근거 서랍의 "원본 위치"가 여기에 기댄다.
//
// 서버 /ask 응답의 evidence 에는 source_id 만 들어 있고 파일 이름·경로는 없다. 그래서
// data/parsed 의 자료 메타를 빌드 시점에 구운 source-registry.ts 를 함께 본다.
// 목 시나리오도 같은 source_id 를 쓰므로 목·실 API 가 같은 표를 공유한다.
//
// 모르는 source_id 는 이름을 지어내지 않고 id 를 그대로 보여준다.

import { SOURCE_REGISTRY } from "./source-registry";
import type { SourceInfo } from "./source-registry";

export type { SourceInfo };
export { SOURCE_REGISTRY };

/** id 로 못 찾으면 화면에 적힌 이름(title)으로도 한 번 더 찾는다. */
export function findSource(idOrTitle: string): SourceInfo | undefined {
  return (
    SOURCE_REGISTRY[idOrTitle] ??
    Object.values(SOURCE_REGISTRY).find((s) => s.title === idOrTitle)
  );
}

export function sourceTitle(sourceId: string): string {
  return SOURCE_REGISTRY[sourceId]?.title ?? sourceId;
}
