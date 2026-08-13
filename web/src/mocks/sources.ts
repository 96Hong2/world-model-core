// 목 시나리오도 실 API 와 같은 자료 표를 본다.
// 정본은 src/lib/source-registry.ts (data/parsed 에서 구워 낸 파일)이고 여기는 옛 import 경로만 유지한다.

export {
  SOURCE_REGISTRY as SOURCES,
  findSource,
  sourceTitle,
} from "../lib/sources";
export type { SourceInfo } from "../lib/sources";
