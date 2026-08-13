// Business World Model — 그래프 DDL
//
// 근거: contracts/ontology.schema.json (15라벨 / 18관계 closed set)
// 적용: python -m graph.ddl apply   (2회 이상 실행해도 결과가 같아야 하므로 전부 IF NOT EXISTS)
//
// 규칙
//  - 모든 노드는 계약상 natural_key 를 갖는다. MERGE 는 (라벨, natural_key) 로만 한다.
//    → 15개 라벨 전부에 natural_key UNIQUE 를 건다.
//  - 계약이 별도의 식별자 속성을 natural key 로 명시한 라벨은 그 속성에도 UNIQUE 를 건다
//    (capability_id · canonical_name · need_id · bd_id · source_id · evidence_id ·
//     observation_id · claim_id). 같은 값이 다른 natural_key 로 두 번 들어오는 것을 막는다.
//  - 속성 존재(NOT NULL) 제약은 Community 에디션이 지원하지 않는다. 필수값 검증은 계약 스키마로 한다.
//  - vector 인덱스는 만들지 않는다. 원문 검색은 fulltext 로만 한다.
//  - fulltext analyzer 는 한국어 때문에 cjk 를 쓴다. 서버가 cjk 를 지원하지 않으면
//    적용 스크립트가 standard 로 바꿔 넣고 그 사실을 출력·기록한다.

// ---------------------------------------------------------------------------
// 1. UNIQUE 제약 — 라벨별 natural key
// ---------------------------------------------------------------------------

CREATE CONSTRAINT bwm_product_key IF NOT EXISTS
FOR (n:Product) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_feature_key IF NOT EXISTS
FOR (n:Feature) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_capability_key IF NOT EXISTS
FOR (n:Capability) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_capability_id IF NOT EXISTS
FOR (n:Capability) REQUIRE n.capability_id IS UNIQUE;

CREATE CONSTRAINT bwm_account_key IF NOT EXISTS
FOR (n:Account) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_account_canonical_name IF NOT EXISTS
FOR (n:Account) REQUIRE n.canonical_name IS UNIQUE;

CREATE CONSTRAINT bwm_industry_key IF NOT EXISTS
FOR (n:Industry) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_need_key IF NOT EXISTS
FOR (n:Need) REQUIRE n.natural_key IS UNIQUE;

// need_id 는 taxonomy 매핑에 성공한 Need 에만 있다(unmapped 는 속성 자체가 없어 제약 대상 밖).
CREATE CONSTRAINT bwm_need_id IF NOT EXISTS
FOR (n:Need) REQUIRE n.need_id IS UNIQUE;

CREATE CONSTRAINT bwm_deal_key IF NOT EXISTS
FOR (n:Deal) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_competitor_key IF NOT EXISTS
FOR (n:Competitor) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_test_key IF NOT EXISTS
FOR (n:Test) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_businessdomain_key IF NOT EXISTS
FOR (n:BusinessDomain) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_businessdomain_id IF NOT EXISTS
FOR (n:BusinessDomain) REQUIRE n.bd_id IS UNIQUE;

CREATE CONSTRAINT bwm_source_key IF NOT EXISTS
FOR (n:Source) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_source_id IF NOT EXISTS
FOR (n:Source) REQUIRE n.source_id IS UNIQUE;

CREATE CONSTRAINT bwm_evidence_key IF NOT EXISTS
FOR (n:Evidence) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_evidence_id IF NOT EXISTS
FOR (n:Evidence) REQUIRE n.evidence_id IS UNIQUE;

CREATE CONSTRAINT bwm_event_key IF NOT EXISTS
FOR (n:Event) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_observation_key IF NOT EXISTS
FOR (n:Observation) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_observation_id IF NOT EXISTS
FOR (n:Observation) REQUIRE n.observation_id IS UNIQUE;

CREATE CONSTRAINT bwm_claim_key IF NOT EXISTS
FOR (n:Claim) REQUIRE n.natural_key IS UNIQUE;

CREATE CONSTRAINT bwm_claim_id IF NOT EXISTS
FOR (n:Claim) REQUIRE n.claim_id IS UNIQUE;

// ---------------------------------------------------------------------------
// 2. 속성 인덱스 — 질의 경로에서 실제로 거르는 값들
// ---------------------------------------------------------------------------

// 지식 레이어: 상태·critical 합류·민감도 필터·시간 게이트
CREATE INDEX bwm_claim_status IF NOT EXISTS FOR (n:Claim) ON (n.status);
CREATE INDEX bwm_claim_lane IF NOT EXISTS FOR (n:Claim) ON (n.lane);
CREATE INDEX bwm_claim_kind IF NOT EXISTS FOR (n:Claim) ON (n.claim_kind);
CREATE INDEX bwm_claim_domain IF NOT EXISTS FOR (n:Claim) ON (n.claim_domain);
CREATE INDEX bwm_observation_criticality IF NOT EXISTS FOR (n:Observation) ON (n.criticality);
CREATE INDEX bwm_evidence_source_id IF NOT EXISTS FOR (n:Evidence) ON (n.source_id);
CREATE INDEX bwm_evidence_authored_at IF NOT EXISTS FOR (n:Evidence) ON (n.authored_at);
CREATE INDEX bwm_source_sensitivity IF NOT EXISTS FOR (n:Source) ON (n.sensitivity);
CREATE INDEX bwm_source_visibility IF NOT EXISTS FOR (n:Source) ON (n.visibility);
CREATE INDEX bwm_source_type IF NOT EXISTS FOR (n:Source) ON (n.source_type);
CREATE INDEX bwm_event_type IF NOT EXISTS FOR (n:Event) ON (n.event_type);

// 비즈니스 레이어: 딜 단계·도메인 집계·엔티티 이름 링킹
CREATE INDEX bwm_deal_stage_value IF NOT EXISTS FOR (n:Deal) ON (n.stage_value);
CREATE INDEX bwm_deal_outcome IF NOT EXISTS FOR (n:Deal) ON (n.outcome);
CREATE INDEX bwm_deal_account_canonical IF NOT EXISTS FOR (n:Deal) ON (n.account_canonical);
CREATE INDEX bwm_businessdomain_status IF NOT EXISTS FOR (n:BusinessDomain) ON (n.bd_status);
CREATE INDEX bwm_businessdomain_scope IF NOT EXISTS FOR (n:BusinessDomain) ON (n.industry_scope);
CREATE INDEX bwm_businessdomain_name IF NOT EXISTS FOR (n:BusinessDomain) ON (n.name);
CREATE INDEX bwm_need_type IF NOT EXISTS FOR (n:Need) ON (n.need_type);
CREATE INDEX bwm_need_canonical IF NOT EXISTS FOR (n:Need) ON (n.canonical);
CREATE INDEX bwm_capability_addon IF NOT EXISTS FOR (n:Capability) ON (n.addon);
CREATE INDEX bwm_capability_name IF NOT EXISTS FOR (n:Capability) ON (n.name);
CREATE INDEX bwm_feature_screen IF NOT EXISTS FOR (n:Feature) ON (n.screen);
CREATE INDEX bwm_feature_introduced_in IF NOT EXISTS FOR (n:Feature) ON (n.introduced_in);
CREATE INDEX bwm_feature_name IF NOT EXISTS FOR (n:Feature) ON (n.name);
CREATE INDEX bwm_product_name IF NOT EXISTS FOR (n:Product) ON (n.name);
CREATE INDEX bwm_industry_name IF NOT EXISTS FOR (n:Industry) ON (n.name);
CREATE INDEX bwm_competitor_name IF NOT EXISTS FOR (n:Competitor) ON (n.name);
CREATE INDEX bwm_account_kind IF NOT EXISTS FOR (n:Account) ON (n.account_kind);

// ---------------------------------------------------------------------------
// 3. fulltext 인덱스 — 추출이 놓친 원문을 "추가 원문 근거"로 되찾는 경로
// ---------------------------------------------------------------------------
// Evidence 의 발췌 속성은 계약상 `snippet` 이다. 초기 요구 문구의 `excerpt` 표기도 함께 색인해
// 어느 쪽 이름으로 적재되어도 검색이 끊기지 않게 한다.
// eventually_consistent=false: 커밋 직후 검색 결과에 보이게 한다(적재→즉시 질의 시나리오).

CREATE FULLTEXT INDEX evidence_fulltext IF NOT EXISTS
FOR (n:Evidence) ON EACH [n.snippet, n.excerpt]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk', `fulltext.eventually_consistent`: false } };

CREATE FULLTEXT INDEX observation_fulltext IF NOT EXISTS
FOR (n:Observation) ON EACH [n.statement]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk', `fulltext.eventually_consistent`: false } };
