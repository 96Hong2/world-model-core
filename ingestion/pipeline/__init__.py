"""Snapshot Ingest 파이프라인.

파싱이 끝난 `data/parsed/` 를 읽어 Neo4j 그래프로 옮긴다. 파서는 건드리지 않는다.

단계
    ① Source 등록      config/sources.yaml 이 정본. sensitivity 없으면 거부
    ② Evidence 적재     data/parsed/*.jsonl 전량 + FROM_SOURCE
    ③ 결정적 직행       structured.record_kind 별 노드·엣지 (LLM 없음)
    ④ canonical 매핑    Need·Capability 를 taxonomy 사전으로. 실패는 unmapped 기록
    ⑤ LLM 추출          예산 안에서 후보 생성 → T2 게이트 통과분만
    ⑥ ER               aliases.yaml exact match. do_not_merge 는 절대 합치지 않음
    ⑦ criticality      규칙 발화 시 lane=critical (검색에서 사라지지 않게)
    ⑧ 상태 판정         Observation 확정 / 정본 명시 사실만 VERIFIED / 나머지 CANDIDATE
    ⑨ idempotent MERGE  natural key 기준. 두 번 넣어도 수가 같다
"""
