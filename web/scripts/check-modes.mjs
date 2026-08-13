// 실 API 말고 나머지 두 상태가 실제로 도는지 브라우저로 확인한다.
//
//   1) 목 폴백  — VITE_DATA_SOURCE=mock 이면 백엔드 없이도 답변이 뜬다
//   2) 오류 상태 — Answer API 가 죽어 있으면 조용히 빈 화면이 되지 않고 이유를 말한다
//
// 각각 vite dev 를 따로 띄웠다 내린다. 실 API 데모(:5273)는 건드리지 않는다.
//
//   node scripts/check-modes.mjs

import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// playwright 는 이 저장소에 깔지 않는다. 설치된 프로젝트 루트를 환경변수로 받는다.
const require = createRequire((process.env.WM_PLAYWRIGHT_ROOT ?? '.') + '/');
const { chromium } = require('playwright');

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, '..');

function startVite(port, env) {
  const child = spawn(
    'npx',
    ['vite', '--port', String(port), '--strictPort'],
    { cwd: webRoot, env: { ...process.env, ...env }, stdio: 'pipe' },
  );
  return new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error(`vite ${port} 가 안 떴다`)), 30_000);
    child.stdout.on('data', (buf) => {
      if (buf.toString().includes('ready in')) {
        clearTimeout(timer);
        setTimeout(() => ok(child), 400);
      }
    });
    child.on('exit', (code) => fail(new Error(`vite ${port} 가 코드 ${code} 로 끝났다`)));
  });
}

const browser = await chromium.launch();
let problems = 0;

/* 1 ------------------------------------------------------------- 목 폴백 */

const mockVite = await startVite(5281, { VITE_DATA_SOURCE: 'mock' });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

  await page.goto('http://localhost:5281', { waitUntil: 'networkidle' });
  await page.waitForSelector('.ask-chip', { timeout: 15_000 });
  const chips = await page.locator('.ask-chip').count();
  const badge = await page.locator('.topbar .badge').first().textContent();
  await page.locator('.ask-chip').first().click();
  await page.waitForSelector('.answer-text', { timeout: 15_000 });
  await page.waitForSelector('.gnode', { timeout: 15_000 });
  const nodes = await page.locator('.gnode').count();
  console.log(`목 폴백: 칩 ${chips}개 · 배지 "${badge?.trim()}" · 답변 노드 ${nodes}개 · 콘솔 오류 ${errors.length}건`);
  if (chips !== 4) { console.error('  목 시나리오는 4개여야 한다'); problems += 1; }
  if (!badge?.includes('목 데이터')) { console.error('  목 모드 표시가 없다'); problems += 1; }
  if (nodes === 0) { console.error('  목 답변에 그래프가 없다'); problems += 1; }
  if (errors.length) { console.error(`  콘솔 오류: ${errors.join(' / ')}`); problems += 1; }
  await page.close();
} finally {
  mockVite.kill();
}

/* 2 ------------------------------------------------------------ 오류 상태 */

// 아무도 듣지 않는 포트로 프록시를 돌려 백엔드가 죽은 상황을 만든다.
const deadVite = await startVite(5282, { VITE_API_TARGET: 'http://127.0.0.1:9' });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto('http://localhost:5282', { waitUntil: 'domcontentloaded' });
  await page.fill('.ask-input', '백엔드가 죽었을 때 화면이 무엇을 말하나?');
  await page.click('.ask-submit');
  await page.waitForSelector('.state-box[data-state="error"]', { timeout: 30_000 });
  const title = await page.locator('.state-title').first().textContent();
  const note = await page.locator('.state-note').first().textContent();
  const retry = await page.locator('.state-box .ghost-btn').count();
  console.log(`오류 상태: "${title?.trim()}" · "${note?.trim()}" · 다시 물어보기 버튼 ${retry}개`);
  if (retry === 0) { console.error('  다시 물어보기 버튼이 없다'); problems += 1; }
  await page.close();
} finally {
  deadVite.kill();
}

await browser.close();

if (problems > 0) {
  console.error(`\n${problems}건 어긋났다.`);
  process.exit(1);
}
console.log('\n목 폴백·오류 상태 모두 확인했습니다.');
