// 목 payload 를 contracts/answer.schema.json 으로 실제 검증한다.
// npm run build 의 첫 단계라 스키마를 어기는 목은 빌드를 통과하지 못한다.
//
// contracts/ 는 읽기만 한다(§7 directory ownership).

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..');
const contractsDir = resolve(repoRoot, 'contracts');
const mocksDir = resolve(here, '..', 'src', 'mocks');

const BASE = 'https://aurorasoft.internal/bwm/contracts/';

function loadContract(name) {
  return JSON.parse(readFileSync(resolve(contractsDir, name), 'utf8'));
}

const ajv = new Ajv2020({ strict: false, allErrors: true });
for (const name of ['policy.schema.json', 'source_type.enum.json', 'state.enum.json']) {
  ajv.addSchema(loadContract(name), BASE + name);
}
const validate = ajv.compile(loadContract('answer.schema.json'));

// 목은 TS 모듈이라 tsx 없이 읽을 수 없다. 정규식 파싱 대신 esbuild(vite 의존성)로 번들해서 실행한다.
const { build } = await import('esbuild');

const mockFiles = readdirSync(mocksDir).filter(
  (f) => f.startsWith('gq-') && f.endsWith('.ts'),
);
if (mockFiles.length === 0) {
  console.error('목 시나리오 파일을 찾지 못했습니다.');
  process.exit(1);
}

const entry = resolve(here, '.mock-entry.generated.mjs');
const entryContents = `
${mockFiles.map((f, i) => `import * as m${i} from '../src/mocks/${f.replace(/\.ts$/, '')}';`).join('\n')}
export const scenarios = [${mockFiles.map((_, i) => `...Object.values(m${i})`).join(', ')}];
`;

const result = await build({
  stdin: {
    contents: entryContents,
    resolveDir: here,
    sourcefile: 'mock-entry.ts',
    loader: 'ts',
  },
  bundle: true,
  format: 'esm',
  platform: 'node',
  write: false,
});

const dataUrl =
  'data:text/javascript;base64,' +
  Buffer.from(result.outputFiles[0].text).toString('base64');
const { scenarios } = await import(dataUrl);
void entry;

let failed = 0;
let checked = 0;

for (const scenario of scenarios) {
  if (!scenario || typeof scenario !== 'object' || !scenario.answer) continue;
  checked += 1;
  const ok = validate(scenario.answer);
  if (!ok) {
    failed += 1;
    console.error(`\n[FAIL] ${scenario.id} — answer.schema.json 위반`);
    for (const err of validate.errors ?? []) {
      console.error(`   ${err.instancePath || '(root)'} ${err.message}`);
    }
    continue;
  }

  // 스키마가 잡지 못하는 계약을 추가로 확인한다.
  const problems = [];
  const evidenceIds = new Set(scenario.answer.evidence.map((e) => e.evidence_id));

  for (const c of scenario.answer.citations) {
    if (!evidenceIds.has(c.evidence_id)) {
      problems.push(`각주 [${c.marker}] 의 evidence_id ${c.evidence_id} 가 evidence[] 에 없다 (traceability)`);
    }
  }
  const markers = new Set(scenario.answer.citations.map((c) => c.marker));
  for (const n of scenario.answer.subgraph.nodes) {
    for (const m of n.citation_markers ?? []) {
      if (!markers.has(m)) {
        problems.push(`노드 ${n.id} 가 존재하지 않는 각주 [${m}] 을 가리킨다`);
      }
    }
  }
  const nodeIds = new Set(scenario.answer.subgraph.nodes.map((n) => n.id));
  for (const e of scenario.answer.subgraph.edges) {
    if (!nodeIds.has(e.from) || !nodeIds.has(e.to)) {
      problems.push(`엣지 ${e.from} -${e.type}-> ${e.to} 의 끝점이 노드 목록에 없다`);
    }
  }
  const focal = scenario.answer.subgraph.nodes.filter((n) => n.rank === 'focal');
  if (focal.length !== 1) {
    problems.push(`focal 노드가 ${focal.length} 개다 (질문의 중심 entity 는 하나여야 한다)`);
  }
  if (scenario.answer.subgraph.nodes.length > 30) {
    problems.push(`초기 노드가 ${scenario.answer.subgraph.nodes.length} 개다 (초기 ≤30)`);
  }
  for (const g of scenario.answer.gaps) {
    if (g.verdict === 'CONFIRMED' && !(g.basis.explicit_absence_evidence_ids ?? []).length) {
      problems.push(`gap "${g.subject}" 가 CONFIRMED 인데 explicit_absence_evidence_ids 가 비었다`);
    }
  }
  for (const s of scenario.answer.raw_signals ?? []) {
    if (evidenceIds.has(s.evidence_id)) {
      problems.push(`raw_signal ${s.evidence_id} 이 인용 evidence 와 섞였다 (구분 표시가 무의미해진다)`);
    }
  }
  // 마스킹 하드 게이트: 전화번호 정규식이 답변 어디에도 매칭되면 안 된다.
  const blob = JSON.stringify(scenario.answer);
  const phone = /(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)/;
  if (phone.test(blob)) {
    problems.push('답변 payload 에 마스킹되지 않은 전화번호가 있다');
  }

  if (problems.length) {
    failed += 1;
    console.error(`\n[FAIL] ${scenario.id}`);
    problems.forEach((p) => console.error(`   ${p}`));
  } else {
    console.log(`[ok] ${scenario.id} — answer.schema.json 통과 (evidence ${scenario.answer.evidence.length} · 노드 ${scenario.answer.subgraph.nodes.length} · 엣지 ${scenario.answer.subgraph.edges.length})`);
  }
}

if (checked === 0) {
  console.error('검증한 시나리오가 0건이다.');
  process.exit(1);
}
if (failed > 0) {
  console.error(`\n${failed}/${checked} 시나리오 실패`);
  process.exit(1);
}
console.log(`\n${checked} 시나리오 전부 통과`);
