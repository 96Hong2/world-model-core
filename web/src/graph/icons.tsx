// Entity Type 별 아이콘. 색은 부모의 --type-color 를 currentColor 로 물려받는다.
// 선 하나짜리 단순 도형만 쓴다(작게 줄여도 형태가 남아야 한다).

import type { EntityType } from "./adapter";

const PATHS: Record<EntityType, string> = {
  // 사업영역: 4분면
  BusinessDomain: "M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z",
  // 고객사: 건물
  Account: "M3 14V3h7v11M6 5.5h1M6 8h1M6 10.5h1M10 7h3v7M3 14h11",
  // 요구: 느낌표 방울
  Need: "M8 1.6 14.6 13H1.4zM8 6v3.4M8 11.2v.6",
  // 역량: 톱니
  Capability:
    "M8 5.4a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2M8 1.6v2M8 12.4v2M1.6 8h2M12.4 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M12.5 3.5l-1.4 1.4M4.9 11.1l-1.4 1.4",
  // 기능: 체크 상자
  Feature: "M2.6 2.6h10.8v10.8H2.6zM5.2 8.2l2 2 3.6-4",
  // 산업: 공장
  Industry: "M2 14V7l4 2.6V7l4 2.6V3h4v11z",
  // 제품: 상자
  Product: "M8 1.8 14 5v6l-6 3.2L2 11V5zM2 5l6 3.2L14 5M8 8.2V14.2",
  // 주장: 말풍선
  Claim: "M2.4 3.2h11.2v8H8.8L5.6 14v-2.8H2.4z",
  // 자료: 문서
  Source: "M4 1.8h5l3 3v9.4H4zM9 1.8v3h3M6 8.4h4M6 10.8h4",
  Observation:
    "M1.6 8S4 3.6 8 3.6 14.4 8 14.4 8 12 12.4 8 12.4 1.6 8 1.6 8M8 6.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6",
  Event: "M2.6 3.6h10.8v10H2.6zM2.6 6.6h10.8M5.6 1.8v3M10.4 1.8v3",
  Competitor: "M4.6 2.2 8 5.6l3.4-3.4M2.2 8h11.6M4.6 13.8 8 10.4l3.4 3.4",
  Persona:
    "M8 2.4a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6M2.8 14c0-2.9 2.3-4.4 5.2-4.4s5.2 1.5 5.2 4.4",
  Deal: "M2.2 5.4h11.6v7.4H2.2zM5.6 5.4V3.8h4.8v1.6M2.2 8.6h11.6",
  Unknown: "M8 2.4a5.6 5.6 0 1 0 0 11.2 5.6 5.6 0 0 0 0-11.2",
};

export function EntityIcon({
  type,
  size = 14,
}: {
  type: EntityType;
  size?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.35"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[type] ?? PATHS.Unknown} />
    </svg>
  );
}
