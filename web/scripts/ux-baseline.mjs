// 개선 전 화면을 숫자로 잰다. "좋아 보인다"가 아니라 "무엇이 몇 픽셀을 쓰는가"로 적는다.
//
// 재는 것 (요구사항 §3 의 불만 항목과 1:1):
//   - 질문 후보 영역이 첫 화면에서 차지하는 높이 비율
//   - 답변 본문이 첫 화면에서 차지하는 높이 비율
//   - 답변보다 위에 있는 badge·metadata 개수
//   - 그래프 캔버스 대비 노드가 실제로 덮는 면적 비율 (빈 캔버스 문제)
//   - 노드 라벨 글자 크기 (읽을 수 있는가)
//   - 범례가 차지하는 높이
//   - 화면 전체의 border 개수 (박스 남발)
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'

// playwright 는 이 저장소에 깔지 않는다. 설치된 프로젝트 루트를 환경변수로 받는다.
const require = createRequire((process.env.WM_PLAYWRIGHT_ROOT ?? '.') + '/');
const { chromium } = require('playwright')

const WEB = process.env.WEB_URL ?? 'http://localhost:5173'
const QUESTION =
  process.env.BWM_QUESTION ?? '가나손해보험에게 어떤 Sales Point를 잡는 것이 좋은가?'
const OUT = process.env.OUT_DIR ?? 'web/screenshots/baseline'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

/** 첫 화면(뷰포트) 안에서 이 선택자가 실제로 차지하는 높이 비율 */
const measure = () =>
  page.evaluate(() => {
    const vh = window.innerHeight
    const vw = window.innerWidth
    const box = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const r = el.getBoundingClientRect()
      // 뷰포트와 겹치는 부분만 센다
      const top = Math.max(0, r.top)
      const bottom = Math.min(vh, r.bottom)
      return {
        h: Math.round(r.height),
        visibleH: Math.round(Math.max(0, bottom - top)),
        shareOfViewport: Math.round((Math.max(0, bottom - top) / vh) * 100),
        w: Math.round(r.width),
      }
    }

    // 그래프: 노드가 캔버스에서 실제로 덮는 면적
    const canvas = document.querySelector('.graph-body')
    let graphFill = null
    if (canvas) {
      const c = canvas.getBoundingClientRect()
      const nodes = [...document.querySelectorAll('.react-flow__node')]
      let area = 0
      let minX = Infinity
      let minY = Infinity
      let maxX = -Infinity
      let maxY = -Infinity
      let smallestFont = Infinity
      for (const n of nodes) {
        const r = n.getBoundingClientRect()
        area += r.width * r.height
        minX = Math.min(minX, r.left)
        minY = Math.min(minY, r.top)
        maxX = Math.max(maxX, r.right)
        maxY = Math.max(maxY, r.bottom)
        const label = n.querySelector('.label')
        if (label) {
          const fs = parseFloat(getComputedStyle(label).fontSize)
          if (fs) smallestFont = Math.min(smallestFont, fs)
        }
      }
      const canvasArea = c.width * c.height
      graphFill = {
        nodes: nodes.length,
        // 노드 상자들의 면적 합 / 캔버스 면적
        inkRatio: canvasArea ? Math.round((area / canvasArea) * 1000) / 10 : 0,
        // 노드들을 감싸는 사각형 / 캔버스 면적 (그래프가 구석에 뭉쳐 있는지)
        bboxRatio:
          nodes.length && canvasArea
            ? Math.round((((maxX - minX) * (maxY - minY)) / canvasArea) * 1000) / 10
            : 0,
        smallestLabelPx: Number.isFinite(smallestFont) ? smallestFont : null,
      }
    }

    // border 를 실제로 그리는 요소 수 (박스 남발 지표)
    let bordered = 0
    for (const el of document.querySelectorAll('main *')) {
      const s = getComputedStyle(el)
      const w = parseFloat(s.borderTopWidth) + parseFloat(s.borderLeftWidth)
      if (w > 0 && s.borderTopStyle !== 'none') bordered += 1
    }

    // 답변 본문보다 화면 위쪽에 있는 badge 수
    const answerTop =
      document.querySelector('.answer-text')?.getBoundingClientRect().top ?? Infinity
    const badgesAbove = [...document.querySelectorAll('.badge')].filter(
      (b) => b.getBoundingClientRect().top < answerTop,
    ).length

    return {
      viewport: { w: vw, h: vh },
      askArea: box('.ask-bar') ?? box('.pane > :first-child'),
      answerText: box('.answer-text'),
      legend: box('.legend-bar'),
      graphHead: box('.graph-head'),
      graphFill,
      borderedElements: bordered,
      badgesAboveAnswer: badgesAbove,
      badgesTotal: document.querySelectorAll('.badge').length,
      answerBodyFontPx: document.querySelector('.answer-text')
        ? parseFloat(getComputedStyle(document.querySelector('.answer-text')).fontSize)
        : null,
      answerLineHeight: document.querySelector('.answer-text')
        ? getComputedStyle(document.querySelector('.answer-text')).lineHeight
        : null,
      // 한 줄에 들어가는 글자 수 근사 (읽기 좋은 폭인가)
      answerCharsPerLine: (() => {
        const el = document.querySelector('.answer-text')
        if (!el) return null
        const fs = parseFloat(getComputedStyle(el).fontSize)
        return Math.round(el.getBoundingClientRect().width / (fs * 0.95))
      })(),
    }
  })

const report = {}

await page.goto(WEB, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)
report.initial = await measure()
await page.screenshot({ path: `${OUT}/1-initial.png` })

await page.fill('.ask-input', QUESTION)
await page.click('.ask-submit')
await page.waitForTimeout(2500)
report.loading = await measure()
await page.screenshot({ path: `${OUT}/2-loading.png` })

await page.locator('.react-flow__node').first().waitFor({ timeout: 300_000 })
await page.waitForTimeout(2600) // 펼침 애니메이션 끝난 뒤
report.answer = await measure()
await page.screenshot({ path: `${OUT}/3-answer.png` })
await page.screenshot({ path: `${OUT}/3-answer-full.png`, fullPage: true })

// 각주 하나 눌러 본 상태
const cite = page.locator('.answer-text .cite').first()
if (await cite.count()) {
  await cite.click()
  await page.waitForTimeout(600)
  report.citation = await measure()
  await page.screenshot({ path: `${OUT}/4-citation.png` })
}

report.consoleErrors = errors.length
writeFileSync(`${OUT}/baseline.json`, JSON.stringify(report, null, 2))

const line = (k, m) =>
  m
    ? `${k.padEnd(12)} 높이 ${String(m.h).padStart(4)}px · 첫화면 점유 ${String(m.shareOfViewport).padStart(3)}%`
    : `${k.padEnd(12)} (없음)`

for (const [state, m] of Object.entries(report)) {
  if (typeof m !== 'object' || m === null) continue
  console.log(`\n--- ${state} ---`)
  console.log(line('질문영역', m.askArea))
  console.log(line('답변본문', m.answerText))
  console.log(line('범례', m.legend))
  console.log(line('그래프머리', m.graphHead))
  if (m.graphFill)
    console.log(
      `그래프        노드 ${m.graphFill.nodes}개 · 노드가 덮는 면적 ${m.graphFill.inkRatio}% · 노드 묶음 범위 ${m.graphFill.bboxRatio}% · 가장 작은 라벨 ${m.graphFill.smallestLabelPx}px`,
    )
  console.log(
    `답변 글자     ${m.answerBodyFontPx}px / 줄높이 ${m.answerLineHeight} / 한 줄 약 ${m.answerCharsPerLine}자`,
  )
  console.log(
    `박스·배지     border 그린 요소 ${m.borderedElements}개 · badge 총 ${m.badgesTotal}개 (답변보다 위 ${m.badgesAboveAnswer}개)`,
  )
}
console.log(`\nconsole.error ${errors.length}건`)
console.log(`저장: ${OUT}/baseline.json`)

await browser.close()
