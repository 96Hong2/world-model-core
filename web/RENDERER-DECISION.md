# 그래프 렌더러 결정 — React Flow + elkjs

- 결정일: 2026-08-08
- 근거 화면: `screenshots/spike-renderer-comparison.png` (같은 목 그래프: 노드 25 · 엣지 28 · 한글 라벨, 좌 React Flow+elkjs / 우 Cytoscape.js+fcose)
- 판정 기준: Master Plan §10 "데모 심미성 · 이해 용이성"

## 결론

**React Flow(@xyflow/react) + elkjs(layered)** 를 쓴다. Cytoscape.js + fcose 는 스파이크 후 제거했다.

## 근거 (스파이크 화면에서 실제로 확인한 것)

1. **방향이 읽힌다.** elk layered(RIGHT)는 Account → Need → Capability → Feature → Product 가 왼쪽에서 오른쪽으로 흐른다. fcose 는 힘기반이라 방향 개념이 없어 같은 데이터가 중심부에 뭉친 구름으로 보인다. 이 그래프는 "근거가 어디서 와서 어디로 이어지는가"를 보여주는 것이 목적이라 방향이 곧 이해 용이성이다.
2. **노드 겹침.** elk layered 는 레이어 안에서 겹침이 없다(스파이크 25노드 겹침 0). fcose 는 `randomize:false` 로 결정적으로 만들면 중앙에 노드가 몰려 근접·겹침이 생겼다.
3. **재현성.** elk 는 입력이 같으면 항상 같은 그림이다. fcose 는 기본값 `randomize:true` 에서 실행마다 배치가 달라져 데모 스크린샷을 재현할 수 없었고, `randomize:false` 로 고정하면 2 회 실행 픽셀 동일(sha256 일치)이지만 배치 품질을 잃었다.
4. **긴 한글 라벨.** React Flow 노드는 HTML 이라 말줄임(`-webkit-line-clamp`) · hover 툴팁 · 폰트 크기 하한을 CSS 로 그대로 쓴다. Cytoscape 는 캔버스라 `text-max-width` 줄바꿈만 되고 말줄임·툴팁은 직접 만들어야 한다.
5. **상태·인용 표현.** VERIFIED 실선 / CANDIDATE 점선 / DISPUTED 이중선 / CRITICAL 강조 · 인용 글로우 · 각주 번호 뱃지를 HTML+CSS 로 바로 그린다. Cytoscape 에서는 이중선·글로우를 별도 노드 트릭으로 흉내내야 한다.
6. **테마.** HTML 노드라 라이트/다크 CSS 변수와 focus 링이 그대로 적용된다.

## 감수한 것

- React Flow 는 노드를 DOM 으로 그려 수천 노드에서 느려진다. Answer 계약이 노드 50 상한(확장 누적 150)이라 이 규모에서는 문제가 되지 않는다.
- elk layered 는 세로로 길어져 가로 여백이 남는다. `elk.aspectRatio` 와 fitView padding 으로 보정했다.
- 엣지 라벨이 겹치는 자리가 있었다. 제품에서는 인용된 엣지만 라벨을 상시 노출하고 나머지는 hover·선택 시 노출한다.

## 어댑터 경계

렌더러 의존은 `src/graph/GraphCanvas.tsx` 한 파일에만 둔다. 바깥은 `src/graph/adapter.ts` 의 `toRenderGraph(subgraph, ...)` 가 만든 `RenderNode`/`RenderEdge` 만 쓴다. 추상화는 이 한 겹까지다.
