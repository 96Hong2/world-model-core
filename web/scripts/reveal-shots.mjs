// 관계도 펼침 애니메이션을 시점별로 찍어 겹 단위로 번지는지 눈으로 확인한다.
// 정지 화면 한 장으로는 "움직였는지" 를 알 수 없어서 프레임을 여러 장 남긴다.
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'

// Playwright 는 workflow-ui-automation 저장소 것을 빌려 쓴다(이 저장소에 새로 깔지 않는다).
// playwright 는 이 저장소에 깔지 않는다. 설치된 프로젝트 루트를 환경변수로 받는다.
const require = createRequire((process.env.WM_PLAYWRIGHT_ROOT ?? '.') + '/');
const { chromium } = require('playwright')

const WEB = process.env.WEB_URL ?? 'http://localhost:5173'
const QUESTION = process.env.BWM_QUESTION ?? '여러 Business Domain에서 공통적으로 등장하는 Need는 무엇인가?'
const OUT = 'web/screenshots/reveal'
const STOPS = (process.env.BWM_STOPS ?? '120,350,600,900,1300,1900,2600').split(',').map(Number) // 겹 간격 420ms 를 사이사이로 끊어 본다

mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(WEB, { waitUntil: 'networkidle' })
await page.fill('.ask-input', QUESTION)

const started = Date.now()
await page.click('.ask-submit')
// 관계도가 붙는 순간부터 재기 시작한다. 답변 대기 시간은 애니메이션과 무관하다.
await page.locator('.react-flow__node').first().waitFor({ timeout: 300_000 })
const drawn = Date.now()

const counts = []
let previous = 0
for (const at of STOPS) {
  const wait = at - (Date.now() - drawn)
  if (wait > 0) await page.waitForTimeout(wait)
  const shown = await page.locator('.gnode:not(.unrevealed)').count()
  // 엣지는 DOM 에 항상 있고 투명도로 감춘다. 개수로는 못 잰다.
  const edges = await page.evaluate(() =>
    [...document.querySelectorAll('.react-flow__edge-path')].filter(
      (p) => Number(p.style.opacity || 1) > 0.01,
    ).length,
  )
  // 건너뛰기는 애니메이션이 끝나면 사라진다. 프레임마다 재야 "중간에 있었나" 를 알 수 있다.
  const skipNow = await page.locator('.board-tool', { hasText: '건너뛰기' }).count()
  counts.push({ at, shown, edges, skipNow })
  if (shown < previous) console.log(`  ⚠ ${at}ms: 보이는 노드가 줄었다 (${previous} → ${shown})`)
  previous = shown
  await page.screenshot({ path: `${OUT}/t${String(at).padStart(4, '0')}.png` })
}

const total = await page.locator('.gnode').count()
const bar = await page.locator('.reveal-bar').count()
const replay = await page.locator('.board-tool', { hasText: '다시 보기' }).count()
const skip = await page.locator('.board-tool', { hasText: '건너뛰기' }).count()

console.log(`답변까지 ${((drawn - started) / 1000).toFixed(1)}초 · 노드 총 ${total}개`)
for (const c of counts)
  console.log(
    `  ${String(c.at).padStart(4)}ms  보이는 노드 ${c.shown}/${total} · 엣지 ${c.edges} · 건너뛰기 ${c.skipNow ? '보임' : '없음'}`,
  )
console.log(`펼침 바 ${bar} · 다시 보기 ${replay} · 건너뛰기 ${skip}`)
console.log(`console.error ${errors.length}건${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`)

const grew = counts.some((c, i) => i > 0 && c.shown > counts[i - 1].shown)
const finished = counts.at(-1).shown === total
console.log(`겹 단위로 늘어났나: ${grew ? '예' : '아니오'} · 끝에 전부 보이나: ${finished ? '예' : '아니오'}`)
await browser.close()
process.exit(grew && finished && errors.length === 0 ? 0 : 1)
