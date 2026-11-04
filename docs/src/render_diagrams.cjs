// 把 diagrams.html 里的图导出成 PNG，供设计文档使用。
const { chromium } = require('playwright-core')
const path = require('path')

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const OUT = path.join(__dirname, 'img')

;(async () => {
  require('fs').mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch({ executablePath: EDGE, headless: true })
  const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width: 1240, height: 900 } })
  await page.setContent(require('fs').readFileSync(path.join(__dirname, 'diagrams.html'), 'utf8'))
  for (const id of ['architecture', 'state', 'er', 'dispatch']) {
    await page.locator(`#${id}`).screenshot({ path: path.join(OUT, `${id}.png`) })
  }
  await browser.close()
})()
