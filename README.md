# world-model-core

조직에 흩어진 자료(스프레드시트·문서·메신저 로그)를 지식그래프로 적재하고,
근거가 달린 답변으로 꺼내 보는 월드모델 엔진.

핵심 설계는 하나다. **모든 주장(Claim)은 원문 발췌(Evidence)를 달고 다닌다.**
답변은 그래프를 조회해 만들고, 문장마다 어느 파일 어느 셀에서 왔는지 따라갈 수 있다.

## 구성

| 디렉토리 | 역할 |
| --- | --- |
| `contracts/` | 온톨로지·답변·정책의 JSON Schema 계약. 코드보다 먼저 확정하고, 코드가 여기에 맞춘다 |
| `graph/` | Neo4j DDL(제약·인덱스)과 연결 관리 |
| `ingestion/` | 파서(xlsx·pdf·html·slack)와 적재 파이프라인 |
| `llm/` | LLM 추출 계층. 예산 게이트·호출 원장·캐시 |
| `retrieval/` | 질문 라우팅·서브그래프 조회·답변 조립 |
| `api/` | 답변·탐색 HTTP API |
| `web/` | 답변(읽는 글) + 지식그래프(도면) 이중 화면 웹 뷰어 |
| `config/` | **도메인 프로필.** 소스 목록·엔티티 별칭·분류 체계를 여기서 갈아 끼운다 |
| `eval/` | 골든 문항 하네스. 기대값의 근거는 시스템 출력이 아니라 자료 원문이다 |
| `infra/` | 로컬 Neo4j docker-compose |

## 도메인을 갈아 끼우는 법

엔진은 도메인을 모른다. 도메인 지식은 전부 두 곳에 있다.

1. `config/*.yaml` — 어떤 소스를 읽을지(`sources.yaml`), 이름 표기를 어떻게 묶을지(`aliases.yaml`),
   분류 체계(`*-taxonomy.yaml`), 시드 엔티티(`bd-seed.yaml`)
2. `contracts/ontology.schema.json` — 노드 라벨과 관계 정의

이 저장소의 config 와 예시 데이터는 전부 **가상의 회사(오로라소프트)와 가상의 고객사(가나은행·마바손해보험 등)** 로 채워져 있다.
자기 도메인에 쓰려면 config 를 자기 자료에 맞게 다시 쓰면 된다.

## 빠른 시작

```bash
# 1. 그래프 DB
docker compose -f infra/docker-compose.yml up -d

# 2. 스키마
python -m graph.ddl apply

# 3. 적재 (config/sources.yaml 이 가리키는 자료를 읽는다)
python -m ingestion.pipeline.runner

# 4. API + 웹
python -m api.main
cd web && npm install && npm run dev
```

환경 변수는 `BWM_NEO4J_URI` / `BWM_NEO4J_USER` / `BWM_NEO4J_PASSWORD` 로 넘긴다.
기본값은 `infra/docker-compose.yml` 의 로컬 데모 값과 같다.

## 내 도메인의 월드모델 만들기

이 레포는 GitHub 템플릿이다. **Use this template** 로 새 레포를 뜨면
커밋 이력 없이 독립된 내 레포가 생긴다(fork 와 달리 원본과 연결이 없다).

1. `config/*.yaml` 을 내 자료에 맞게 다시 쓴다 (소스 목록 → 별칭 → 시드 → 분류)
2. `contracts/ontology.schema.json` 의 라벨을 내 도메인의 개념으로 바꾼다
3. `web/src/app/profile.ts` 의 제품·시스템 이름을 바꾼다
4. `data/sources/` 에 자료를 넣고 적재를 돌린다

테스트는 세 층이다. 단위·계약 테스트는 항상 돌고, 실자료 통합 테스트는
원본 파일이 있을 때만, 코퍼스 통합 테스트는 `WM_CORPUS_TESTS=1` 로
명시했을 때만 돈다. 자료 없이 클론해도 전체 suite 가 green 이다.

## 데이터는 커밋하지 않는다

`data/` 전체가 gitignore 다. 원문 자료, 파싱 산출물, LLM 호출 원장, 캐시는
전부 로컬에만 남는다. 발췌에 크리덴셜·개인정보가 섞일 수 있기 때문이다.
