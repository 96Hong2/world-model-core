# WM 화면 (web)

**오로라웍스 World Model (WM)** 의 웹 화면이다. 사용자에게 보이는 이름은 WM 이고,
저장소·API·컨테이너 이름은 `business-world-model` / `bwm-*` 그대로다.

디자인 정본은 `DESIGN.md` 하나다. 색·글자·간격·모션을 컴포넌트에서 새로 만들지 않는다.

## 네 화면

| 경로 | 무엇 | 그래프가 그리는 것 |
| --- | --- | --- |
| `/ask?q=…` | 질문 → 브리프 + 도면 | 이 답을 만든 관계 |
| `/explore` `/explore/:type` `/explore/:type/:id` | 대상 중심 탐색 | 고른 대상 주변 관계 |
| `/library` `/library/:sourceId` | 원본 자료 서재 | 이 자료가 만든 지식 |
| `/changes` `/changes/:runId` | 수집으로 무엇이 늘었나 | 이번에 새로 생긴 관계 |

보조: `/saved` (이 브라우저에만 저장) · `/health` (근거가 얇은 곳)

**Graph 는 메뉴가 아니다.** 네 화면을 관통하는 층이고, `src/graph/GraphPane.tsx` 하나를
제목과 subgraph 만 갈아 끼워 쓴다.

## 띄우기

```bash
# 0) 처음 한 번: 조회 인덱스 (없으면 화면이 10~40초씩 걸린다)
.venv/bin/python scripts/create_indexes.py

# 1) Answer API 먼저 (저장소 루트에서)
.venv/bin/python -m uvicorn api.main:app --port 8099

# 2) 화면
cd web && npm run dev
```

API 는 기동 직후 배경 스레드로 집계표를 미리 만든다(약 2~3분). 그 사이에 서재·변경 화면을
누르면 처음 한 번만 느리다.

화면은 같은 출처의 `/api` 로만 부르고 vite dev 서버가 그것을 `:8099` 로 넘긴다.

| 환경변수 | 기본값 | 뜻 |
|---|---|---|
| `VITE_API_TARGET` | `http://localhost:8099` | dev 프록시가 넘길 Answer API 주소 |
| `VITE_API_BASE` | `/api` | 화면이 부르는 주소. dev 프록시를 안 쓸 때만 절대 주소로 |
| `VITE_DATA_SOURCE` | (없음) = 실 API | `mock` 이면 백엔드 없이 시나리오 4종으로 돈다 |
| `VITE_SLACK_WORKSPACE` | (없음) | 슬랙 워크스페이스 이름. 없으면 **슬랙 링크를 만들지 않는다** |

`VITE_SLACK_WORKSPACE` 를 비워 두는 것이 기본이다. 추측한 주소로 링크를 만들면 눌러도
아무 일이 안 일어나고, 그건 링크가 없는 것보다 나쁘다(`src/lib/open-source.ts`).

## 확인

```bash
npm run build          # 목 계약 검증 → 순수 로직 검증 → 타입 검사 → 번들
npm run test:web       # locator·API 주소·자료 표·확장 대상 판정

node scripts/ux-baseline.mjs   # 개선 전 기준값을 다시 잰다 (screenshots/baseline/)
node scripts/wm-tour.mjs      # 11개 상태를 실제로 밟고 캡쳐 + 실측 (screenshots/wm/)
node scripts/demo-shots.mjs    # 데모 경로 캡쳐
node scripts/check-modes.mjs   # 목 폴백과 백엔드 장애 화면
```

`wm-tour.mjs` 가 요구사항 §20 의 11개 상태를 순서대로 밟고, 개선 전 기준값과 같은 지표를
재서 대조표를 찍는다. **화면을 고쳤으면 이걸 돌려 숫자로 확인한다.**

⚠️ 순회 도중에 소스를 고치지 않는다. vite HMR 이 편집 중간 상태를 잡아 엉뚱한 실패로 나온다.

Playwright 는 `workflow-ui-automation` 저장소에서 빌려 쓴다. 여기에 따로 깔지 않는다.

## 자료 표

`src/lib/source-registry.ts` 는 `data/parsed/<source_id>.source.json` 에서 구워 낸 파일이다.
서버 응답에는 `source_id` 만 있고 파일 이름·경로가 없어서, 근거의 "원본 위치" 가 이 표를 본다.

```bash
npm run gen:sources
```

## 렌더러

React Flow + elkjs. 고른 이유와 비교 대상은 `RENDERER-DECISION.md`.
렌더러를 아는 파일은 `src/graph/GraphCanvas.tsx` 하나뿐이고, 그 바깥은 `src/graph/adapter.ts`
가 만든 값만 쓴다. `api/browse.py` 도 Answer 계약의 subgraph 모양으로 응답해서, 네 화면이
같은 어댑터·같은 캔버스를 쓴다.

## 글꼴

CDN 을 쓰지 않고 npm 패키지에서 자체 호스팅한다(사내망·오프라인 데모에서 글꼴이 조용히
바뀌면 화면 밀도가 통째로 달라진다).

| 역할 | 글꼴 |
| --- | --- |
| UI·라벨·그래프 | Pretendard Variable |
| **답변 본문만** | Nanum Myeongjo (읽는 글과 조작하는 글을 갈라 놓는 장치) |
| 위치·ID·수치 | JetBrains Mono |
