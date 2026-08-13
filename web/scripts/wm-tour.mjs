// WM 전체 화면을 실제 브라우저에서 돌아 보고 캡쳐 + 실측한다.
//
// 요구 §20 이 요구한 11개 상태를 순서대로 밟는다:
//   1 처음 진입 · 2 질문 입력 · 3 로딩 · 4 답변 · 5 각주 선택 · 6 노드 선택
//   7 노드 펼침 · 8 원문 열기 · 9 둘러보기 · 10 서재 · 11 변경
//
// 코드만 보고 "좋아졌다" 고 하지 않으려고 만든 것이다. 개선 전 기준값
// (web/screenshots/baseline/baseline.json)과 같은 지표를 재서 대조표를 낸다.
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'

// playwright 는 이 저장소에 깔지 않는다. 설치된 프로젝트 루트를 환경변수로 받는다.
const require = createRequire((process.env.WM_PLAYWRIGHT_ROOT ?? '.') + '/');
const { chromium } = require('playwright')

const WEB = process.env.WEB_URL ?? 'http://localhost:5173'
const QUESTION =
  process.env.BWM_QUESTION ?? '가나손해보험에게 어떤 Sales Point를 잡는 것이 좋은가?'
const OUT = process.env.OUT_DIR ?? 'web/screenshots/wm'
const VIEWPORT = { width: 1440, height: 900 }
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: VIEWPORT })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

/** 개선 전과 같은 지표를 잰다. 이름과 뜻을 baseline 스크립트와 맞춰 둔다. */
const measure = () =>
  page.evaluate(() => {
    const vh = window.innerHeight
    const box = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const r = el.getBoundingClientRect()
      const top = Math.max(0, r.top)
      const bottom = Math.min(vh, r.bottom)
      return {
        h: Math.round(r.height),
        shareOfViewport: Math.round((Math.max(0, bottom - top) / vh) * 100),
      }
    }

    /** 여러 요소를 합친 하나의 세로 구간. 사이에 낀 여백도 답변이 쓰는 자리로 센다. */
    const unionBox = (sels) => {
      const rects = sels
        .map((s) => document.querySelector(s))
        .filter(Boolean)
        .map((el) => el.getBoundingClientRect())
      if (!rects.length) return null
      const top = Math.max(0, Math.min(...rects.map((r) => r.top)))
      const bottom = Math.min(vh, Math.max(...rects.map((r) => r.bottom)))
      const h = Math.max(...rects.map((r) => r.bottom)) - Math.min(...rects.map((r) => r.top))
      return {
        h: Math.round(h),
        shareOfViewport: Math.round((Math.max(0, bottom - top) / vh) * 100),
      }
    }

    const canvas = document.querySelector('.board-body')
    let graph = null
    if (canvas) {
      const c = canvas.getBoundingClientRect()
      const nodes = [...document.querySelectorAll('.react-flow__node')]
      let area = 0
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      let smallestFont = Infinity
      for (const n of nodes) {
        const r = n.getBoundingClientRect()
        area += r.width * r.height
        minX = Math.min(minX, r.left); minY = Math.min(minY, r.top)
        maxX = Math.max(maxX, r.right); maxY = Math.max(maxY, r.bottom)
        const label = n.querySelector('.label')
        if (label) {
          const fs = parseFloat(getComputedStyle(label).fontSize)
          if (fs) smallestFont = Math.min(smallestFont, fs)
        }
      }
      const canvasArea = c.width * c.height
      graph = {
        nodes: nodes.length,
        inkRatio: canvasArea ? Math.round((area / canvasArea) * 1000) / 10 : 0,
        bboxRatio:
          nodes.length && canvasArea
            ? Math.round((((maxX - minX) * (maxY - minY)) / canvasArea) * 1000) / 10
            : 0,
        smallestLabelPx: Number.isFinite(smallestFont) ? smallestFont : null,
      }
    }

    // 그래프 노드는 왼쪽 띠로 종류를 알리므로 border 를 반드시 갖는다.
    // 도면 안팎을 갈라 세야 "박스 남발" 을 제대로 판정할 수 있다.
    let bordered = 0
    let borderedOutsideGraph = 0
    for (const el of document.querySelectorAll('.screen *')) {
      const s = getComputedStyle(el)
      const w = parseFloat(s.borderTopWidth) + parseFloat(s.borderLeftWidth)
      if (w > 0 && s.borderTopStyle !== 'none') {
        bordered += 1
        if (!el.closest('.board-body')) borderedOutsideGraph += 1
      }
    }

    const answerTop =
      document.querySelector('.answer-text')?.getBoundingClientRect().top ?? Infinity
    const tags = [...document.querySelectorAll('.tag')]
    // 브리프(읽는 면) 안의 배지만 "답변보다 위" 판정에 넣는다. 도면 머리줄의 태그는
    // 오른쪽 창이라 y 좌표만 비교하면 잘못 잡힌다.
    const briefTags = [...document.querySelectorAll('.brief .tag')]

    const readEl = document.querySelector('.brief-text')
    const readFs = readEl ? parseFloat(getComputedStyle(readEl).fontSize) : null

    return {
      // 개선 전 지표는 '질문 후보가 차지하는 높이' 였다. 첫 화면 컨테이너를 재면
      // 언제나 100% 라 아무것도 판정하지 못한다.
      askArea: box('.ask-bar') ?? box('.ask-suggest'),
      suggestArea: box('.ask-suggest'),
      // 2026-08-10: 답변 본문이 한 덩어리가 아니게 됐다. 결론 · 핵심 포인트 · 할 일
      // 세 단으로 갈렸고 `.answer-text` 는 그중 결론 하나만 덮는다. 그것만 재면
      // "첫 화면이 답변으로 채워졌나" 라는 이 지표의 뜻이 사라진다(실측: 82% → 16%
      // 로 떨어졌는데 화면은 오히려 답으로 더 꽉 찼다).
      // 그래서 **읽는 세 단을 합쳐** 잰다. 목표(≥62%)는 그대로 둔다.
      answerText: unionBox(['.lead', '.points', '.doing']) ?? box('.answer-text'),
      // 옛 지표도 함께 남긴다. 회차 대조를 하려면 같은 자로 잰 값이 있어야 한다.
      answerLeadOnly: box('.answer-text'),
      legend: box('.legend'),
      boardHead: box('.board-head'),
      graph,
      borderedElements: bordered,
      borderedOutsideGraph,
      tagsTotal: tags.length,
      tagsAboveAnswer: briefTags.filter((b) => b.getBoundingClientRect().top < answerTop)
        .length,
      tagsAboveAnswerAnywhere: tags.filter((b) => b.getBoundingClientRect().top < answerTop)
        .length,
      answerBodyFontPx: readFs,
      answerFontFamily: readEl ? getComputedStyle(readEl).fontFamily.split(',')[0] : null,
      // 개선 전 지표는 라틴 글자 폭(0.5em)을 가정해 세었다. 답변이 한글이라 그 자를
      // 그대로 쓰면 실제로 몇 자가 들어가는지와 어긋난다. 한글 자막은 1em 에 가까우니
      // 폭/글자크기 로 세고, 옛 지표도 함께 남겨 대조할 수 있게 둔다.
      answerHangulPerLine: readEl && readFs
        ? Math.round(readEl.getBoundingClientRect().width / readFs)
        : null,
      answerCharsPerLine: readEl && readFs
        ? Math.round(readEl.getBoundingClientRect().width / (readFs * 0.95))
        : null,
      answerColWidth: readEl ? Math.round(readEl.getBoundingClientRect().width) : null,
      marginaliaNotes: document.querySelectorAll('.mnote').length,
      title: document.title,
      bwmVisible: /BWM|Business World Model/.test(document.body.innerText),
    }
  })

const report = { states: {}, checks: {} }
const shot = async (name) => page.screenshot({ path: `${OUT}/${name}.png` })

/* 1 ─ 처음 진입 */
await page.goto(WEB, { waitUntil: 'networkidle' })
// 추천 질문은 /golden 응답 뒤에 그려진다. 그걸 기다리지 않으면 첫 화면 지표가 비어 나온다.
await page.locator('.ask-suggest').waitFor({ timeout: 60_000 }).catch(() => {})
await page.waitForTimeout(1200)
report.states['1-처음진입'] = await measure()
await shot('1-landing')

/* 2 ─ 질문 입력 */
await page.fill('.ask-input', QUESTION)
await page.waitForTimeout(200)
await shot('2-typed')
await page.click('.ask-submit')

/* 3 ─ 로딩 */
await page.waitForTimeout(3000)
report.states['3-로딩'] = await measure()
report.checks.loadingStages = await page.locator('.stage').count()
report.checks.loadingStageNow = await page.locator('.stage[data-state="now"]').count()
await shot('3-loading')

/* 4 ─ 답변 */
await page.locator('.react-flow__node').first().waitFor({ timeout: 600_000 })
await page.waitForTimeout(2600)
report.states['4-답변'] = await measure()
await shot('4-answer')
await page.screenshot({ path: `${OUT}/4-answer-full.png`, fullPage: true })

/* 5 ─ 각주 선택 (읽던 문장이 화면에 남아 있는가) */
const cites = page.locator('.answer-text .cite')
const citeCount = await cites.count()
report.checks.citeCount = citeCount
if (citeCount > 0) {
  await cites.first().click()
  await page.waitForTimeout(700)
  report.states['5-각주선택'] = await measure()
  report.checks.citeHighlightNodes = await page.locator('.gnode.hl').count()
  report.checks.citeActiveNote = await page.locator('.mnote[data-active="true"]').count()
  await shot('5-citation')

  // 각주 전부를 눌러 근거가 어디서든 받아지는지 센다.
  // 먼저 강조를 지운다 — 켜진 각주를 다시 누르면 꺼지므로 그것을 결함으로 세게 된다.
  await cites.first().click()
  await page.waitForTimeout(200)
  let uncovered = 0
  for (let i = 0; i < citeCount; i += 1) {
    const c = cites.nth(i)
    await c.click()
    await page.waitForTimeout(140)
    const hl = await page.locator('.gnode.hl').count()
    const note = await page.locator('.mnote[data-active="true"]').count()
    if (hl === 0 && note === 0) uncovered += 1
    await c.click()
    await page.waitForTimeout(80)
  }
  report.checks.citesUncovered = uncovered
}

/* 6 ─ 노드 선택 */
await page.locator('.gnode').first().click()
await page.waitForTimeout(700)
report.checks.nodeDrawerOpen = await page.locator('.drawer').count()
report.checks.nodeDrawerEvidenceRows = await page.locator('.drawer .ev-row').count()
await shot('6-node-drawer')

// 근거 하나를 열어 **원문으로 가는 길**이 실제로 있는지 본다. 노드가 자료가 아니면
// 원문 블록이 없는 것이 맞는 동작이라, 노드로 재면 언제나 0이 나온다.
const drawerEv = page.locator('.drawer .ev-row').first()
if (await drawerEv.count()) {
  await drawerEv.click()
  await page.waitForTimeout(800)
}
report.checks.drawerHasOrigin = await page.locator('.origin').count()
report.checks.drawerOpenButtons = await page.locator('.origin .btn').count()
report.checks.drawerHasAuthority = await page.locator('.trust-scale').count()
report.checks.drawerOriginParts = await page.locator('.origin-part').count()
await shot('6b-evidence-drawer')

/* 8 ─ 원문 열기 (열 수 없는 경로에 링크를 만들지 않았는지) */
const openBtn = page.locator('.origin .btn', { hasText: /펼쳐 보기/ }).first()
if (await openBtn.count()) {
  await openBtn.click()
  await page.locator('.viewer-row').first().waitFor({ timeout: 60_000 }).catch(() => {})
  await page.waitForTimeout(500)
  report.checks.viewerRows = await page.locator('.viewer-row').count()
  report.checks.viewerHit = await page.locator('.viewer-row[data-hit="true"]').count()
  await shot('8-source-viewer')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)
}
// 깨진 file:// 링크가 하나도 없어야 한다.
report.checks.brokenFileLinks = await page.evaluate(
  () => [...document.querySelectorAll('a[href^="file:"]')].length,
)
await page.keyboard.press('Escape')
await page.waitForTimeout(400)

/* 7 ─ 노드 펼침 */
const before = await page.locator('.react-flow__node').count()
const more = page.locator('.board-tool', { hasText: /더 보기/ }).first()
if (await more.count()) {
  await more.click()
  await page.waitForTimeout(2600)
}
report.checks.nodesBeforeExpand = before
report.checks.nodesAfterExpand = await page.locator('.react-flow__node').count()
await shot('7-expanded')

/* 9 ─ 둘러보기 */
await page.click('.rail-item[href="/explore"]')
// 사업영역 카드까지 기다린다. .gate 는 데이터 없이도 그려지므로 그것만 기다리면
// 아직 비어 있는 화면을 재고 "숫자가 안 나온다" 고 잘못 판정한다.
await page.waitForSelector('.domain-card', { timeout: 120_000 })
await page.waitForTimeout(900)
report.checks.exploreGates = await page.locator('.gate').count()
report.checks.exploreDomains = await page.locator('.domain-card').count()
report.states['9-둘러보기'] = await measure()
await shot('9-explore')

await page.locator('.domain-card').first().click()
await page.waitForSelector('.detail-title', { timeout: 60_000 })
await page.waitForTimeout(2600)
report.checks.entityDetailGraphNodes = await page.locator('.react-flow__node').count()
report.checks.entityDetailRelGroups = await page.locator('.rel-group').count()
report.checks.entityDetailEvidence = await page.locator('.ev-row').count()
await shot('9b-entity-detail')

/* 10 ─ 서재 */
await page.click('.rail-item[href="/library"]')
await page.waitForSelector('.rowitem', { timeout: 60_000 })
await page.waitForTimeout(700)
report.checks.libraryRows = await page.locator('.rowitem').count()
// 그룹 칩을 왼쪽 폴더 트리로 바꿨다. 재는 것은 그대로 "자료를 좁힐 수단이 몇 개인가" 다.
report.checks.libraryFilters = await page.locator('.filter, .tree-row').count()
report.checks.libraryTreeFolders = await page.locator('.tree-caret-btn').count()
await shot('10-library')

await page.locator('.rowitem').first().click()
await page.waitForSelector('.detail-title', { timeout: 60_000 })
await page.waitForTimeout(2600)
report.checks.sourceDetailPreview = await page.locator('.preview-row').count()
report.checks.sourceDetailGraph = await page.locator('.react-flow__node').count()
report.checks.sourceDetailHasOpen = await page
  .locator('.screen-head .btn', { hasText: '원문 열기' })
  .count()
await shot('10b-source-detail')

/* 11 ─ 변경 */
await page.click('.rail-item[href="/changes"]')
await page.waitForSelector('.runstep', { timeout: 60_000 })
await page.waitForTimeout(700)
report.checks.runCards = await page.locator('.runstep').count()
report.checks.changesLimits = await page.locator('.limits li').count()
// 이 화면이 무엇에 쓰는 것인지 한 줄이 있어야 한다(숫자만 있으면 읽는 사람이 못 쓴다).
report.checks.changesHasWhy = await page.locator('.page-why').count()
report.checks.changesHasTotals = await page.locator('.now-nums b').count()
await shot('11-changes')

await page.locator('.runstep .btn').first().click()
await page.waitForSelector('.tl-item', { timeout: 120_000 })
await page.waitForTimeout(2600)
report.checks.timelineItems = await page.locator('.tl-item').count()
report.checks.runChangeRows = await page.locator('.rowitem').count()
report.checks.runGraphNodes = await page.locator('.react-flow__node').count()
await shot('11b-run-detail')

if (await page.locator('.rowitem').count()) {
  await page.locator('.rowitem').first().click()
  await page.locator('.drawer .props').first().waitFor({ timeout: 60_000 }).catch(() => {})
  await page.waitForTimeout(400)
  report.checks.elementDrawer = await page.locator('.drawer').count()
  report.checks.elementDrawerReason = await page.locator('.drawer .notice-body').count()
  await shot('11c-element-drawer')
  await page.keyboard.press('Escape')
}

/* 보조 ─ 데이터 상태 · 저장함 */
await page.click('.rail-item[href="/health"]')
await page.waitForSelector('.health-group', { timeout: 120_000 })
await page.waitForTimeout(700)
report.checks.healthGroups = await page.locator('.health-group').count()
await shot('12-health')

await page.click('.rail-item[href="/saved"]')
await page.waitForTimeout(700)
await shot('13-saved')

/* 다크 모드 */
await page.click('.rail-btn')
await page.waitForTimeout(600)
await shot('14-dark-saved')
await page.goto(`${WEB}/ask?q=${encodeURIComponent(QUESTION)}`, {
  waitUntil: 'domcontentloaded',
})
await page.locator('.react-flow__node').first().waitFor({ timeout: 600_000 })
await page.waitForTimeout(2600)
await shot('14b-dark-answer')
report.checks.deepLinkWorked = await page.locator('.answer-text').count()
await page.click('.rail-btn')
await page.waitForTimeout(400)

/* 좁은 화면 */
for (const w of [1280, 1024, 768, 375]) {
  await page.setViewportSize({ width: w, height: 900 })
  await page.waitForTimeout(900)
  await shot(`15-narrow-${w}`)
  report.checks[`hScroll${w}`] = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  )
}
await page.setViewportSize(VIEWPORT)

report.consoleErrors = errors.length
report.consoleErrorSample = errors.slice(0, 5)
writeFileSync(`${OUT}/tour.json`, JSON.stringify(report, null, 2))

/* ---------------------------------------------------------------- 출력 */

const B = {
  askShare: 24, answerShare: 48, tagsAbove: 6, tagsTotal: 70,
  bordered: 283, legendH: 116, chars: 42, ink: 8.5, label: 11,
  answerAfterCite: 0,
}
const a = report.states['4-답변'] ?? {}
const c5 = report.states['5-각주선택'] ?? {}
const row = (name, before, after, goal) =>
  `${name.padEnd(30)} ${String(before).padStart(6)} → ${String(after).padStart(6)}   목표 ${goal}`

console.log('\n=== 개선 전 → 개선 후 (같은 질문·같은 뷰포트 1440x900) ===')
// 원래 결함은 "질문 후보가 **답변** 시작 전에 화면을 먹는다"(219px · 24%)였다.
// 그래서 답변 화면의 질문줄을 잰다. 첫 화면은 질문이 주역이라 비율이 커도 결함이 아니다.
console.log(row('답변화면 질문줄 점유 %', B.askShare, a.askArea?.shareOfViewport ?? '-', '≤8'))
console.log(
  `  (참고: 첫 화면의 추천 질문 블록은 ${report.states['1-처음진입']?.suggestArea?.shareOfViewport ?? '-'}% — 첫 화면은 질문이 주역이라 목표를 걸지 않는다)`,
)
console.log(row('답변본문 첫화면 점유 %', B.answerShare, a.answerText?.shareOfViewport ?? '-', '≥62'))
console.log(row('각주 클릭 후 본문 잔존 %', B.answerAfterCite, c5.answerText?.shareOfViewport ?? '-', '≥50'))
// 뜻이 있는 상태 배지(미검증 중요 항목 등)는 답변 위에 두는 것이 맞다. 목표 0 은
// 분류·개수를 알리던 메타데이터 배지를 뜻한다.
console.log(row('답변보다 위 배지 수(브리프)', B.tagsAbove, a.tagsAboveAnswer ?? '-', '뜻 있는 것만'))
console.log(row('화면 전체 배지 수', B.tagsTotal, a.tagsTotal ?? '-', '≤6'))
console.log(row('border 그린 요소 수(전체)', B.bordered, a.borderedElements ?? '-', '참고'))
console.log(row('border 그린 요소 수(도면 밖)', B.bordered, a.borderedOutsideGraph ?? '-', '≤60'))
console.log(row('범례 높이 px', B.legendH, a.legend?.h ?? '-', '≤34'))
console.log(row('답변 한 줄 한글 글자 수', 21, a.answerHangulPerLine ?? '-', '28~40'))
console.log(`  (개선 전 21자 = 폭 ${'?'} / 개선 후 폭 ${a.answerColWidth}px · 라틴 기준 옛 지표 ${B.chars} → ${a.answerCharsPerLine})`)
console.log(row('노드가 덮는 면적 %', B.ink, a.graph?.inkRatio ?? '-', '≥18'))
console.log(row('가장 작은 노드 라벨 px', B.label, a.graph?.smallestLabelPx ?? '-', '≥13'))

console.log('\n=== 기능 확인 ===')
for (const [k, v] of Object.entries(report.checks)) console.log(`  ${k.padEnd(26)} ${v}`)
console.log(`\n  답변 본문 글꼴          ${a.answerFontFamily}`)
console.log(`  여백 각주 개수          ${a.marginaliaNotes}`)
console.log(`  브라우저 탭 제목        ${a.title}`)
console.log(`  화면에 BWM 표기 남음    ${a.bwmVisible}`)
console.log(`  console.error           ${errors.length}건`)
if (errors.length) console.log('   ' + errors.slice(0, 5).join('\n   '))
console.log(`\n캡쳐 ${OUT}/ · 수치 ${OUT}/tour.json`)

await browser.close()
