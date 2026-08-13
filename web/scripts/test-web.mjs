// 화면의 순수 로직 검증. 브라우저 없이 도는 것만 여기서 본다.
//   - 실 API 가 실제로 내려주는 locator 표기를 사람 말로 풀 수 있는가
//   - API 주소·모드(실 API / 목)를 환경변수에서 어떻게 정하는가
//   - source_id 로 자료 이름·경로를 찾을 수 있는가 (근거 서랍의 "원본 위치")
//   - 1-hop 확장 버튼을 어떤 노드에 붙일 것인가
//
// 기대값은 시스템 출력이 아니라 실제 자료·계약에서 왔다:
//   locator 표기는 contracts/ontology.schema.json 의 정의와 실 API 응답 실측값,
//   자료 이름은 data/parsed/<source_id>.source.json 의 canonical_location.
//
// vitest 를 새로 깔지 않고 esbuild(vite 의존성)로 TS 를 번들해 노드에서 돌린다.
// validate-mocks.mjs 가 목을 읽을 때 쓰는 방법과 같다.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const srcDir = resolve(here, '..', 'src');

async function load(entry) {
  const result = await build({
    entryPoints: [resolve(srcDir, entry)],
    bundle: true,
    format: 'esm',
    platform: 'node',
    write: false,
  });
  const url =
    'data:text/javascript;base64,' +
    Buffer.from(result.outputFiles[0].text).toString('base64');
  return import(url);
}

let failed = 0;
let passed = 0;

function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`[ok] ${name}`);
  } catch (err) {
    failed += 1;
    console.error(`[FAIL] ${name}\n   ${err.message}`);
  }
}

function eq(actual, expected, what) {
  if (actual !== expected) {
    throw new Error(`${what}: 기대 ${JSON.stringify(expected)} · 실제 ${JSON.stringify(actual)}`);
  }
}

function has(haystack, needle, what) {
  if (!String(haystack).includes(needle)) {
    throw new Error(`${what}: ${JSON.stringify(needle)} 가 ${JSON.stringify(haystack)} 안에 없다`);
  }
}

const locator = await load('lib/locator.ts');
const client = await load('api/client.ts');
const sources = await load('lib/sources.ts');
const adapter = await load('graph/adapter.ts');

/* ---------------------------------------------------- locator (실 API 실측 표기) */

check('슬랙 locator 는 채널 이름으로 푼다 (ID 를 그대로 보여주지 않는다)', () => {
  // 실 API 응답 실측: evidence[].locator === "slack:C087J33P9PT/1772062465.171759"
  //
  // 기대값이 바뀐 자리다. 예전에는 채널 ID(`#C087J33P9PT`)를 그대로 내보냈고 테스트도 그걸
  // 단언했다. 사람은 ID 로 어느 대화인지 알 수 없어서, 덤프에서 뽑아 구운 이름을 쓴다.
  const read = locator.readLocator('slack:C087J33P9PT/1772062465.171759');
  eq(read.kind, '슬랙 쓰레드', 'kind');
  has(read.human, 'all-영업활동공유', 'human 에 채널 이름');
  if (read.human.includes('C087J33P9PT')) {
    throw new Error(`채널 ID 가 화면 문구에 남아 있다: ${read.human}`);
  }
  has(read.human, '2026-', 'human 에 날짜');
  eq(read.raw, 'slack:C087J33P9PT/1772062465.171759', 'raw 는 원문 그대로');
});

check('이름을 모르는 슬랙 채널은 ID 를 그대로 쓴다 (지어내지 않는다)', () => {
  const read = locator.readLocator('slack:C000UNKNOWN0/1772062465.171759');
  eq(read.kind, '슬랙 쓰레드', 'kind');
  has(read.human, 'C000UNKNOWN0', '모르는 채널은 ID 유지');
});

check('엑셀 시트!셀 표기는 그대로 읽힌다 (기존 동작 유지)', () => {
  const read = locator.readLocator('Pain Point 목록!E57');
  eq(read.kind, '엑셀 시트', 'kind');
  has(read.human, 'Pain Point 목록', 'human 에 시트 이름');
  has(read.human, 'E57', 'human 에 셀');
});

check('날짜가 시트 이름인 엑셀 표기도 시트로 읽는다', () => {
  // 실 API 실측: "6월18일(목)!C37"
  const read = locator.readLocator('6월18일(목)!C37');
  eq(read.kind, '엑셀 시트', 'kind');
  has(read.human, '6월18일(목)', 'human 에 시트 이름');
});

check('PDF 쪽·블록 표기는 그대로 읽힌다', () => {
  const read = locator.readLocator('p.1#2');
  eq(read.kind, 'PDF 쪽 번호', 'kind');
  has(read.human, '1쪽', 'human');
  has(read.human, '2번째 블록', 'human');
});

/* ------------------------------------------------------------- API client */

check('기본 모드는 실 API 다', () => {
  eq(client.resolveDataSource({}), 'live', '환경변수가 없을 때');
  eq(client.resolveDataSource({ VITE_DATA_SOURCE: '' }), 'live', '빈 값');
  eq(client.resolveDataSource({ VITE_DATA_SOURCE: 'live' }), 'live', 'live');
});

check('목은 환경변수로만 켠다', () => {
  eq(client.resolveDataSource({ VITE_DATA_SOURCE: 'mock' }), 'mock', 'mock');
  eq(client.resolveDataSource({ VITE_DATA_SOURCE: 'MOCK' }), 'mock', '대문자도 받는다');
});

check('API 주소 기본값은 vite 프록시 경로 /api 다', () => {
  eq(client.resolveApiBase({}), '/api', '기본값');
  eq(client.resolveApiBase({ VITE_API_BASE: 'http://localhost:8099/' }), 'http://localhost:8099', '끝 슬래시 제거');
});

check('확장 요청은 node_id 를 URL 인코딩하고 누적 노드 수를 함께 보낸다', () => {
  // 실 데이터의 노드 키에는 한글과 | 가 들어 있다: "다라카드|기본"
  const url = client.expandUrl('/api', '다라카드|기본', 42);
  has(url, '/api/graph/expand?', '경로');
  has(url, `node_id=${encodeURIComponent('다라카드|기본')}`, 'node_id 인코딩');
  has(url, 'hops=1', 'hops');
  has(url, 'already=42', '누적 노드 수');
});

/* ------------------------------------------------------------ 자료 레지스트리 */

check('source_id 로 실제 파일 이름을 찾는다', () => {
  // 근거: data/parsed/src_slack_all_sales_share.source.json 의 canonical_location
  eq(sources.sourceTitle('src_slack_all_sales_share'), 'all-영업활동공유.jsonl', '슬랙 자료 이름');
  eq(sources.sourceTitle('src_pain_registry'), '고객사 Pain Point·구축 대응 통합 관리.xlsx', 'pain 자료 이름');
});

check('모르는 source_id 는 지어내지 않고 그대로 보여준다', () => {
  eq(sources.sourceTitle('src_없는자료'), 'src_없는자료', '폴백');
  eq(sources.findSource('src_없는자료'), undefined, '없는 자료는 undefined');
});

check('자료 상세에는 원본 경로가 들어 있다', () => {
  const info = sources.findSource('src_doc_haneul_it_memo');
  if (!info) throw new Error('하늘IT 메모 자료를 찾지 못했다');
  has(info.canonical_location, '.pdf', '경로');
  eq(info.source_type, 'internal_memo', 'source_type');
});

/* ------------------------------------------------------- 확장 버튼 대상 노드 */

check('확장 버튼은 비즈니스 엣지를 가진 엔티티에만 붙는다', () => {
  // 실측: /graph/expand 는 Claim·Observation·Source 에 대해 항상 0건을 준다.
  // 비즈니스 엣지(BELONGS_TO·HAS_NEED·IN_DOMAIN 등)가 엔티티끼리만 연결되기 때문이다.
  eq(adapter.canExpand('Account'), true, 'Account');
  eq(adapter.canExpand('BusinessDomain'), true, 'BusinessDomain');
  eq(adapter.canExpand('Need'), true, 'Need');
  eq(adapter.canExpand('Capability'), true, 'Capability');
  eq(adapter.canExpand('Deal'), true, 'Deal');
  eq(adapter.canExpand('Industry'), true, 'Industry');
  eq(adapter.canExpand('Claim'), false, 'Claim');
  eq(adapter.canExpand('Observation'), false, 'Observation');
  eq(adapter.canExpand('Source'), false, 'Source');
  eq(adapter.canExpand('Unknown'), false, 'Unknown');
});

/* ------------------------------------------------------------------ 결과 */

console.log(`\n${passed} 통과 · ${failed} 실패`);
if (failed > 0) process.exit(1);

// 검증이 실제로 무엇을 읽었는지 남긴다(빈 파일을 통과시키지 않기 위해).
const registry = readFileSync(resolve(srcDir, 'lib', 'source-registry.ts'), 'utf8');
const count = (registry.match(/source_id: "/g) ?? []).length;
if (count < 20) {
  console.error(`자료 레지스트리가 ${count}종뿐이다. gen-sources 를 다시 돌려야 한다.`);
  process.exit(1);
}
console.log(`자료 레지스트리 ${count}종 확인`);
