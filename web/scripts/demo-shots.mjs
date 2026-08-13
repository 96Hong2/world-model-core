// 실 데이터 데모 시나리오를 브라우저로 그대로 밟고 화면을 남긴다.
//
//   질문 입력 → 답변 → 각주 클릭 → 그래프 노드 강조 → 노드 클릭 → 근거 서랍 → 원본 위치
//
// 전제: Answer API(:8099)와 vite dev(:5273)가 떠 있어야 한다. 목이 아니라 실 API 를 본다.
// Playwright 는 workflow-ui-automation 저장소 것을 빌려 쓴다(이 저장소에 새로 깔지 않는다).
//
//   node scripts/demo-shots.mjs

import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// playwright 는 이 저장소에 깔지 않는다. 설치된 프로젝트 루트를 환경변수로 받는다.
const require = createRequire((process.env.WM_PLAYWRIGHT_ROOT ?? '.') + '/');
const { chromium } = require('playwright');

const here = dirname(fileURLToPath(import.meta.url));
const shots = resolve(here, '..', 'screenshots');
mkdirSync(shots, { recursive: true });

const APP = process.env.APP_URL ?? 'http://localhost:5273';
const FACT_QUESTION = '하늘IT와 구독 사업을 추진하려면 반드시 해결해야 할 기술 전제는 무엇인가?';
const STRATEGIC_QUESTION =
  'B2B 유통 Sales 영역에서 우리 제품이 해결할 수 있는 문제와 부족한 Capability는 무엇인가?';

const consoleErrors = [];
const pageErrors = [];
const timings = {};

function log(step, extra = '') {
  console.log(`· ${step}${extra ? ` — ${extra}` : ''}`);
}

async function ask(page, question, label) {
  await page.fill('.ask-input', question);
  const started = Date.now();
  await page.click('.ask-submit');
  await page.waitForSelector('.answer-text', { timeout: 600_000 });
  const seconds = (Date.now() - started) / 1000;
  timings[label] = seconds;
  log(`질문 응답 (${label})`, `${seconds.toFixed(1)}초`);
  // elk 배치가 끝나 노드가 그려질 때까지 기다린다.
  await page.waitForSelector('.gnode', { timeout: 60_000 });
  await page.waitForTimeout(900);
  return seconds;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });

page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});
page.on('pageerror', (err) => pageErrors.push(String(err)));

await page.goto(APP, { waitUntil: 'networkidle' });
await page.waitForSelector('.ask-chip', { timeout: 30_000 });
const chipCount = await page.locator('.ask-chip').count();
log('골든 질문 칩', `${chipCount}개`);
if (chipCount < 20) throw new Error(`골든 질문이 ${chipCount}개다. /golden 이 제대로 안 붙었다.`);

/* 1 ------------------------------------------------------------ 실 API 답변 */

await ask(page, FACT_QUESTION, 'Q-E(사실 질문)');
const citeCount = await page.locator('.answer-text .cite').count();
const evCount = await page.locator('.ev-card').count();
log('답변 각주', `${citeCount}개 · 인용 근거 카드 ${evCount}개`);
if (citeCount === 0) throw new Error('답변에 각주가 없다. 3클릭 경로가 성립하지 않는다.');
await page.screenshot({ path: resolve(shots, 'live-01-answer.png'), fullPage: false });

/* 2 --------------------------------------------------- 각주 클릭 → 노드 강조 */

await page.locator('.answer-text .cite').first().click();
await page.waitForTimeout(700);
const highlighted = await page.locator('.gnode.hl').count();
log('각주 클릭 후 강조된 노드', `${highlighted}개`);
if (highlighted === 0) throw new Error('각주를 눌렀는데 강조된 노드가 없다.');
await page.screenshot({ path: resolve(shots, 'live-02-citation.png') });

/* 3 --------------------------------------------- 노드 클릭 → 근거 서랍(원본 위치) */

await page.locator('.gnode.hl').first().click();
await page.waitForSelector('.drawer', { timeout: 15_000 });
await page.waitForTimeout(400);

// 노드 서랍에서 근거 카드를 한 번 더 눌러 발췌·자료·원본 위치가 다 보이는 화면까지 간다.
const drawerEvidence = page.locator('.drawer .ev-card');
if (await drawerEvidence.count()) {
  await drawerEvidence.first().click();
  await page.waitForTimeout(400);
}
const originVisible = await page.locator('.drawer .origin-box').count();
const originText = await page.locator('.drawer .origin-human').first().textContent();
const pathText = await page.locator('.drawer .origin-box .mono').first().textContent();
log('근거 서랍 원본 위치', `${originText?.trim()} · ${pathText?.trim()}`);
if (originVisible === 0) throw new Error('근거 서랍에 원본 위치 블록이 없다.');
await page.screenshot({ path: resolve(shots, 'live-03-drawer.png') });

/* 4 --------------------------------------------- 전략 질문 (Gap 배지가 붙는다) */

await page.keyboard.press('Escape');
await page.waitForTimeout(300);

await ask(page, STRATEGIC_QUESTION, 'Q-S(전략 질문)');
const route = await page.locator('.route-line .badge').first().textContent();
const gapCards = await page.locator('.gap-card').count();
log('전략 질문 경로', `${route?.trim()} · 제품 공백 ${gapCards}건`);
if (gapCards === 0) throw new Error('전략 질문인데 제품 공백(Gap) 이 하나도 없다.');
// 답변 본문과 제품 공백은 한 화면에 다 안 들어간다. 위쪽(답변)과 아래쪽(공백)을 따로 남긴다.
await page.screenshot({ path: resolve(shots, 'live-06-strategic-top.png') });
await page.locator('.gap-card').first().scrollIntoViewIfNeeded();
await page.waitForTimeout(500);
await page.screenshot({ path: resolve(shots, 'live-04-strategic.png') });

/* 5 ------------------------------------------------------- 1-hop 확장 (실 API) */

// 확장 버튼이 붙어 있어도 그 노드에 비즈니스 엣지가 없으면 0건이 온다.
// 늘어날 때까지 다른 노드로 옮겨 가며 눌러 본다(최대 6번).
const expandButtons = page.locator('.expand-btn');
log('확장 버튼이 붙은 노드', `${await expandButtons.count()}개`);

let grew = false;
for (let attempt = 0; attempt < 6 && !grew; attempt += 1) {
  const before = await page.locator('.gnode').count();
  const buttons = await expandButtons.count();
  if (buttons === 0) break;
  await expandButtons.nth(attempt % buttons).click({ force: true });
  await page.waitForTimeout(2200);
  const after = await page.locator('.gnode').count();
  log(`1-hop 확장 시도 ${attempt + 1}`, `${before} → ${after} 노드`);
  grew = after > before;
}
if (!grew) throw new Error('1-hop 확장으로 노드가 한 번도 늘지 않았다.');
await page.waitForTimeout(600);
await page.screenshot({ path: resolve(shots, 'live-05-expand.png') });

/* ------------------------------------------------------------------- 마무리 */

await browser.close();

console.log('\n=== 응답 시간 ===');
for (const [k, v] of Object.entries(timings)) console.log(`${k}: ${v.toFixed(1)}초`);

console.log('\n=== 콘솔 ===');
console.log(`console.error ${consoleErrors.length}건 · pageerror ${pageErrors.length}건`);
for (const e of [...consoleErrors, ...pageErrors]) console.log(`  ${e}`);

if (consoleErrors.length || pageErrors.length) process.exit(1);
console.log('\n스크린샷 4장을 screenshots/ 에 남겼습니다.');
