// 각주를 눌렀을 때 무엇이 강조되는지 실제로 확인한다.
//
// 자료 노드를 없앤 뒤(D-7 B안) 관계도에 대응 노드가 없는 각주가 늘었다. 그런 각주는
// 왼쪽 근거 목록이 받아야 한다. "받는다고 코드에 썼다" 와 "화면에서 실제로 강조된다" 는
// 다른 이야기라 눌러 보고 센다.
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'

// playwright 는 이 저장소에 깔지 않는다. 설치된 프로젝트 루트를 환경변수로 받는다.
const require = createRequire((process.env.WM_PLAYWRIGHT_ROOT ?? '.') + '/');
const { chromium } = require('playwright')

const WEB = process.env.WEB_URL ?? 'http://localhost:5173'
const QUESTION = process.env.BWM_QUESTION ?? '현재 회사가 개척하려는 Business Domain들은 무엇이고 각각 어느 단계인가?'
const OUT = 'web/screenshots/citation'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(WEB, { waitUntil: 'networkidle' })
await page.fill('.ask-input', QUESTION)
await page.click('.ask-submit')
await page.locator('.react-flow__node').first().waitFor({ timeout: 300_000 })
await page.waitForTimeout(2200) // 펼침 애니메이션이 끝난 뒤에 센다

const orphans = await page.evaluate(() => {
  const ids = new Set([...document.querySelectorAll('.react-flow__node')].map((n) => n.getAttribute('data-id')))
  const linked = new Set()
  for (const p of document.querySelectorAll('.react-flow__edge')) {
    const id = p.getAttribute('data-testid') || p.id || ''
    for (const nid of ids) if (id.includes(nid)) linked.add(nid)
  }
  return { total: ids.size, unlinked: [...ids].filter((x) => !linked.has(x)).length }
})

// 답변 본문의 각주 전부를 하나씩 눌러 본다.
const markers = await page.locator('.answer-text .cite').count()
const rows = []
for (let i = 0; i < markers; i += 1) {
  const cite = page.locator('.answer-text .cite').nth(i)
  const label = (await cite.textContent())?.trim() ?? '?'
  await cite.click()
  await page.waitForTimeout(220)
  const hlNodes = await page.locator('.gnode.hl').count()
  const activeCards = await page.locator('.ev-row[data-active="true"]').count()
  // header 에 .sub 가 둘 있다(제목 옆 부제 + 강조 상태 안내). 상태 안내는 마지막 것이다.
  const hint = (await page.locator('.board-head .board-stat').last().textContent())?.trim() ?? ''
  rows.push({ label, hlNodes, activeCards, hint })
  await cite.click() // 토글을 되돌려 다음 각주가 겹치지 않게 한다
  await page.waitForTimeout(120)
}

await page.locator('.answer-text .cite').first().click()
await page.waitForTimeout(400)
await page.screenshot({ path: `${OUT}/marker-first.png` })

console.log(`노드 ${orphans.total}개 중 선 없는 노드 ${orphans.unlinked}개`)
console.log(`각주 ${markers}개를 하나씩 눌러 봤다`)
let uncovered = 0
for (const r of rows) {
  const covered = r.hlNodes > 0 || r.activeCards > 0
  if (!covered) uncovered += 1
  console.log(
    `  ${r.label.padEnd(5)} 관계도 강조 ${r.hlNodes} · 근거카드 강조 ${r.activeCards} ${covered ? '' : '❌ 아무것도 강조되지 않았다'}`,
  )
}
const nodeless = rows.filter((r) => r.hlNodes === 0)
const guided = nodeless.filter((r) => r.hint.includes('근거 목록')).length
console.log(`관계도에 대응 노드가 없는 각주 ${nodeless.length}개 · 그중 안내 문구가 뜬 것 ${guided}개`)
for (const r of nodeless.slice(0, 3)) console.log(`   실제 문구: ${JSON.stringify(r.hint)}`)
console.log(`console.error ${errors.length}건${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`)

const ok = uncovered === 0 && errors.length === 0 && guided === nodeless.length
console.log(ok ? '판정: 통과' : '판정: 미달')
await browser.close()
process.exit(ok ? 0 : 1)
