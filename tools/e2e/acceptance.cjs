// 真实浏览器验收：按赛题流程走完所有角色，并检查控制台错误和接口 5xx。
// 用法：NODE_PATH=<playwright-core 所在 node_modules> node tools/e2e/acceptance.cjs [baseUrl] [截图目录]
const { chromium } = require('playwright-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8100'
const OUT = process.argv[3] || path.join(__dirname, 'shots')
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const PASSWORD = 'Demo@2026'
fs.mkdirSync(OUT, { recursive: true })

const results = []
const problems = []
let browser
let shotIndex = 0

async function newUser(username, viewport = { width: 1440, height: 900 }, serviceWorkers = 'block') {
  const context = await browser.newContext({ viewport, locale: 'zh-CN', serviceWorkers })
  const page = await context.newPage()
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) problems.push(`[console ${username}] ${m.text()}`) })
  page.on('pageerror', (e) => problems.push(`[pageerror ${username}] ${e.message}`))
  page.on('response', (r) => { if (r.status() >= 500) problems.push(`[${r.status()} ${username}] ${r.url()}`) })
  await page.goto(`${BASE}/login`)
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(PASSWORD)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'))
  await page.waitForLoadState('networkidle')
  return page
}

async function shot(page, name) {
  shotIndex += 1
  await page.waitForTimeout(350)
  await page.screenshot({ path: path.join(OUT, `${String(shotIndex).padStart(2, '0')}-${name}.png`), fullPage: true })
}

async function step(name, fn) {
  try {
    await fn()
    results.push(`通过  ${name}`)
    console.log(`通过  ${name}`)
  } catch (error) {
    results.push(`失败  ${name}: ${error.message.split('\n')[0]}`)
    console.log(`失败  ${name}: ${error.message.split('\n').slice(0, 3).join(' | ')}`)
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        await page.screenshot({ path: path.join(OUT, `fail-${results.length}-${context.pages().indexOf(page)}-${browser.contexts().indexOf(context)}.png`) }).catch(() => {})
      }
    }
  }
}

async function openStar(page) {
  await page.goto(`${BASE}/elders`)
  await page.getByLabel('姓名、编号、完整手机号或身份证').fill('王秀英')
  await page.getByRole('button', { name: '查询' }).click()
  await page.locator('tbody tr', { hasText: '王秀英' }).first().waitFor()
  await page.waitForTimeout(300)
  await page.locator('tbody tr', { hasText: '王秀英' }).first().click()
  await page.waitForURL(/\/elders\/\d+/)
  await page.waitForLoadState('networkidle')
}

const expectText = async (page, text, timeout = 6000) => page.getByText(text, { exact: false }).first().waitFor({ timeout })

async function main() {
  browser = await chromium.launch({ executablePath: EDGE, headless: true })
  let family, grid, cg, comm, org, org2, street, admin
  let bookedId

  await step('家属登录、首页适老化大字', async () => {
    family = await newUser('family_wang', { width: 900, height: 1000 })
    await expectText(family, '一键呼救')
    const size = await family.evaluate(() => getComputedStyle(document.documentElement).fontSize)
    if (size !== '21px') throw new Error(`大字模式未生效: ${size}`)
    await shot(family, 'family-home')
  })

  await step('自然语言预约并提交', async () => {
    await family.goto(`${BASE}/family/book`)
    await family.getByPlaceholder('比如：帮我约明天下午两点助浴').fill('帮我约后天下午三点半助浴')
    await family.getByRole('button', { name: '识别' }).click()
    await expectText(family, '已填入下方')
    await shot(family, 'family-book')
    await family.getByRole('button', { name: '提交预约' }).click()
    await expectText(family, '预约已提交')
    await family.getByRole('button', { name: '查看进度' }).click()
    await family.waitForURL(/\/orders\/\d+/)
    bookedId = Number(family.url().split('/').pop())
  })

  await step('网格员工作台受理', async () => {
    grid = await newUser('grid_zhang')
    await expectText(grid, '待受理预约')
    await shot(grid, 'grid-workbench')
    const row = grid.locator('div.row.between', { hasText: '王秀英 助浴' }).filter({ has: grid.getByRole('button', { name: '受理' }) }).first()
    await row.getByRole('button', { name: '受理' }).click()
    await grid.getByRole('button', { name: '确认受理' }).click()
    await expectText(grid, '已受理，进入待派单')
  })

  await step('智能派单：前 3 名与解释，一键派单', async () => {
    await grid.goto(`${BASE}/dispatch/${bookedId}`)
    await expectText(grid, '推荐候选')
    await grid.locator('.candidate').first().waitFor()
    const count = await grid.locator('.candidate').count()
    if (count < 1) throw new Error('没有候选')
    await shot(grid, 'grid-dispatch')
    await grid.getByRole('button', { name: '一键派单' }).click()
    await expectText(grid, '请等待其在 30 分钟内接单')
    await grid.waitForLoadState('networkidle')
    await shot(grid, 'grid-dispatch-offered')
  })

  await step('护理员今日工单与接单', async () => {
    cg = await newUser('cg01', { width: 480, height: 900 })
    await expectText(cg, '今日工单')
    await shot(cg, 'cg-today')
  })

  await step('上门执行：偏差须说明、清单、照片、血压、签退', async () => {
    await cg.locator('.candidate', { hasText: '助浴' }).filter({ hasText: '已派单' }).first().click()
    await cg.waitForURL(/\/cg\/order\//)
    await cg.getByRole('button', { name: '模拟定位偏差' }).click()
    await cg.getByRole('button', { name: '签到', exact: true }).click()
    await expectText(cg, '定位偏差超出范围')
    await shot(cg, 'cg-too-far')
    await cg.getByPlaceholder('比如：小区门禁、地址标注偏差').fill('小区门禁，地址标注偏差')
    await cg.getByRole('button', { name: '继续签到' }).click()
    await expectText(cg, '第 2 步')
    for (const row of await cg.locator('.check-row').all()) await row.click()
    const image = process.env.E2E_IMAGE
    if (image) {
      await cg.locator('input[type=file]').setInputFiles(image)
      await expectText(cg, '已上传，压缩')
    }
    await cg.getByLabel('收缩压').fill('152')
    await cg.getByLabel('舒张压').fill('88')
    await cg.getByLabel('服务小结').fill('助浴顺利完成，老人精神状态良好')
    await shot(cg, 'cg-execute')
    await cg.getByRole('button', { name: '签退', exact: true }).click()
    await expectText(cg, '服务已完成')
    await shot(cg, 'cg-done')
  })

  await step('家属查看证据链并低分评价，生成回访', async () => {
    await family.goto(`${BASE}/orders?status=COMPLETED`)
    await family.getByRole('button', { name: '查看' }).first().click()
    await family.waitForURL(/\/orders\/\d+/)
    await expectText(family, '服务证据链')
    await family.getByRole('button', { name: '评价服务' }).click()
    for (const group of await family.locator('.stars').all()) await group.getByRole('button', { name: '3 星' }).click()
    await family.getByRole('button', { name: '提交评价' }).click()
    await expectText(family, '48 小时内回访')
    await family.waitForLoadState('networkidle')
    await shot(family, 'family-order-detail')
  })

  await step('随访录入高血压，红色预警置顶', async () => {
    await openStar(grid)
    await shot(grid, 'grid-elder-detail')
    await grid.getByRole('button', { name: '录入随访' }).click()
    await grid.getByLabel('收缩压 mmHg').fill('186')
    await grid.getByLabel('舒张压 mmHg').fill('104')
    await grid.locator('.modal').getByRole('button', { name: '保存' }).click()
    await expectText(grid, '条预警')
    await grid.goto(`${BASE}/alerts`)
    await grid.locator('.alert-card.RED').first().waitFor()
    await shot(grid, 'grid-alerts')
  })

  await step('二次授权查看敏感信息', async () => {
    await openStar(grid)
    await grid.getByRole('button', { name: '查看完整信息' }).click()
    await grid.getByPlaceholder('比如：联系紧急联系人').fill('联系紧急联系人核实情况')
    await grid.getByLabel('本人登录密码').fill(PASSWORD)
    await grid.getByRole('button', { name: '确认查看' }).click()
    await expectText(grid, '本次查看已留痕')
    await expectText(grid, '青松路18号')
    await shot(grid, 'grid-reveal')
  })

  await step('快进 31 分钟，红色预警自动升级到街道', async () => {
    await grid.goto(`${BASE}/alerts`)
    await grid.getByRole('button', { name: '+31 分钟' }).click()
    await expectText(grid, '时钟已快进')
    await grid.locator('.alert-card.RED', { hasText: '已升级' }).first().waitFor()
    await shot(grid, 'grid-escalated')
    street = await newUser('street_sun')
    await street.goto(`${BASE}/alerts`)
    await street.locator('label', { hasText: '只看已升级' }).click()
    await street.locator('.alert-card', { hasText: '已升级' }).first().waitFor()
  })

  await step('预警响应并闭环为已转诊', async () => {
    const card = grid.locator('.alert-card.RED', { hasText: '王秀英' }).first()
    await card.waitFor()
    await card.click()
    await shot(grid, 'grid-alert-detail')
    await grid.getByRole('button', { name: '闭环预警' }).click()
    await grid.locator('label', { hasText: '已转诊' }).click()
    await grid.getByLabel('转诊医院').fill('青松社区卫生服务中心')
    await grid.getByRole('button', { name: '确认闭环' }).click()
    await expectText(grid, '预警已闭环')
  })

  await step('异常处置队列闭环', async () => {
    await grid.goto(`${BASE}/exceptions`)
    await grid.getByRole('button', { name: '接手' }).first().click()
    await expectText(grid, '已接手处置')
    await grid.getByRole('button', { name: '闭环' }).first().click()
    await grid.getByLabel('工单处理').selectOption('NO_SHOW')
    await grid.getByLabel('处置结果').fill('联系家属确认老人外出')
    await grid.getByRole('button', { name: '确认闭环' }).click()
    await expectText(grid, '异常已闭环')
    await shot(grid, 'grid-exceptions')
  })

  let billUrl
  await step('社区管理员处理账单差异并人工复核', async () => {
    comm = await newUser('comm_liu')
    await comm.goto(`${BASE}/bills`)
    await shot(comm, 'comm-bills')
    await comm.locator('tbody tr', { hasText: '幸福里' }).filter({ hasText: '待人工复核' }).getByRole('button', { name: '详情' }).click()
    await comm.waitForURL(/\/bills\/\d+/)
    billUrl = comm.url()
    await expectText(comm, '对账差异清单')
    await shot(comm, 'comm-bill-detail')
    for (let guard = 0; guard < 60; guard += 1) {
      const button = comm.getByRole('button', { name: '处理', exact: true }).first()
      if (await button.count() === 0) break
      await button.click()
      await comm.getByLabel('说明').fill('逐条核实后处理')
      await comm.getByRole('button', { name: '保存' }).click()
      await comm.locator('.modal').waitFor({ state: 'detached' })
      await comm.waitForTimeout(500)
    }
    await comm.getByRole('button', { name: '人工复核' }).click()
    await comm.getByLabel('复核意见').fill('差异已逐条核实，同意提交')
    await comm.getByRole('button', { name: '完成复核' }).click()
    await expectText(comm, '人工复核完成')
    await comm.waitForLoadState('networkidle')
    await shot(comm, 'comm-bill-reviewed')
  })

  await step('网格员核对、机构确认、监管抽查、账单生效', async () => {
    await grid.goto(billUrl)
    await grid.getByRole('button', { name: '网格员核对提交' }).click()
    await expectText(grid, '已核对')
    for (const login of ['org_zhao', 'org_gao']) {
      const page = await newUser(login)
      if (login === 'org_zhao') org = page; else org2 = page
      await page.goto(billUrl)
      await page.getByRole('button', { name: '机构确认' }).click()
      await expectText(page, '已确认')
    }
    await street.goto(billUrl)
    await expectText(street, '监管抽查')
    await shot(street, 'street-sampling')
    for (let guard = 0; guard < 60; guard += 1) {
      const button = street.getByRole('button', { name: '核查' }).first()
      if (await button.count() === 0) break
      await button.click()
      await street.getByRole('button', { name: '提交抽查结果' }).click()
      await street.locator('.modal').waitFor({ state: 'detached' })
      await street.waitForTimeout(500)
    }
    await expectText(street, '已生效')
    await shot(street, 'street-effective')
  })

  await step('缴费登记回写', async () => {
    await comm.goto(`${BASE}/statements`)
    await comm.getByRole('button', { name: '登记缴费' }).first().click()
    await expectText(comm, '已登记缴费')
    await shot(comm, 'comm-statements')
  })

  await step('回访转投诉、机构说明、结论退费', async () => {
    await comm.goto(`${BASE}/quality`)
    await comm.getByRole('button', { name: '回访', exact: true }).first().click()
    await comm.locator('label', { hasText: '转投诉' }).click()
    await comm.getByLabel('核实情况').fill('家属反映服务敷衍，要求投诉')
    await comm.getByRole('button', { name: '保存' }).click()
    await expectText(comm, '已保存')
    await comm.waitForLoadState('networkidle')
    await shot(comm, 'comm-quality')
    await comm.getByRole('button', { name: '下结论' }).first().click()
    await comm.getByLabel('结论依据').fill('机构已核实，情况属实')
    await comm.locator('.modal label', { hasText: '计扣分' }).click()
    await comm.getByRole('button', { name: '归档结论' }).click()
    await expectText(comm, '已保存')
  })

  await step('三个看板、四类图表', async () => {
    for (const [page, kind, name] of [[comm, 'community', 'comm-dashboard'], [org, 'org', 'org-dashboard'], [street, 'street', 'street-dashboard']]) {
      await page.goto(`${BASE}/stats/${kind}`)
      await page.locator('canvas').nth(3).waitFor({ timeout: 8000 })
      await page.waitForTimeout(900)
      await shot(page, name)
    }
    await street.getByRole('button', { name: '近一年' }).click()
    await street.waitForTimeout(1200)
    await shot(street, 'street-dashboard-year')
    const [download] = await Promise.all([street.waitForEvent('download'), street.getByRole('button', { name: '导出 Excel' }).click()])
    if (!download.suggestedFilename().endsWith('.xlsx')) throw new Error('导出文件名不对')
  })

  await step('绩效榜、服务目录、护理员、规则', async () => {
    await org.goto(`${BASE}/performance`)
    await org.locator('tbody tr').first().waitFor()
    await shot(org, 'org-performance')
    await org.goto(`${BASE}/catalog`)
    await org.getByRole('button', { name: '价格版本' }).first().click()
    await expectText(org, '价格只增不改')
    await shot(org, 'org-catalog')
    await org.locator('.modal').getByRole('button', { name: '关闭' }).click()
    await comm.goto(`${BASE}/rules`)
    await comm.locator('tbody tr').first().waitFor()
    await shot(comm, 'comm-rules')
  })

  await step('审计日志与哈希链校验', async () => {
    await street.goto(`${BASE}/audit`)
    await street.getByRole('button', { name: '校验哈希链' }).click()
    await expectText(street, '审计链完整')
    await shot(street, 'street-audit')
  })

  await step('系统管理：用户、权限矩阵、业务参数、任务', async () => {
    admin = await newUser('admin')
    await shot(admin, 'admin-users')
    await admin.goto(`${BASE}/admin/roles`)
    await admin.locator('table.matrix').waitFor()
    await shot(admin, 'admin-roles')
    await admin.goto(`${BASE}/admin/configs`)
    await expectText(admin, '派单权重')
    await shot(admin, 'admin-configs')
    await admin.goto(`${BASE}/admin/jobs`)
    await admin.getByRole('button', { name: '立即执行' }).first().click()
    await admin.waitForTimeout(500)
    await shot(admin, 'admin-jobs')
  })

  await step('离线同步：断网打开页面、签到、勾选、签退，联网自动同步', async () => {
    // 用网格员身份准备一张派给 cg01 的工单
    const orderId = await grid.evaluate(async () => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('yihu.token')}` }
      const call = async (method, url, body) => {
        const response = await fetch(`/api${url}`, { method, headers, body: body ? JSON.stringify(body) : undefined })
        const data = await response.json()
        if (!response.ok) throw new Error(`${url}: ${data.message}`)
        return data
      }
      const items = await call('GET', '/catalog/items?on_shelf_only=true')
      const meal = items.find((i) => i.name === '助餐')
      const elders = (await call('GET', '/elders?size=50')).items
      const clock = await call('GET', '/demo/clock')
      const start = new Date(new Date(clock.now).getTime() + 2 * 3600e3)
      const pad = (n) => String(n).padStart(2, '0')
      const iso = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:00:00`
      const elder = elders.find((e) => e.name !== '王秀英')
      const order = await call('POST', '/orders', { elder_id: elder.id, item_id: meal.id, scheduled_start: iso })
      await call('POST', `/orders/${order.id}/accept`, { note: '离线演示' })
      const caregivers = await call('GET', '/caregivers')
      const me = caregivers.find((c) => c.name === '王小红') || caregivers[0]
      await call('POST', `/orders/${order.id}/reassign`, { caregiver_id: me.id, reason: '离线演示指派' })
      return order.id
    })
    const worker = await newUser('cg01', { width: 480, height: 900 }, 'allow')
    const offer = worker.getByRole('button', { name: '接单', exact: true }).first()
    await offer.waitFor()
    await worker.locator('.candidate', { hasText: '助餐' }).getByRole('button', { name: '接单', exact: true }).first().click()
    await expectText(worker, '接单成功')
    await worker.waitForTimeout(800)
    // 等 Service Worker 接管并预缓存完成
    await worker.evaluate(() => navigator.serviceWorker.ready)
    await worker.reload()
    await worker.waitForFunction(() => navigator.serviceWorker.controller !== null, null, { timeout: 15000 })
    await worker.waitForTimeout(1500)
    await worker.locator('.candidate', { hasText: '助餐' }).first().waitFor()

    await worker.context().setOffline(true)
    await worker.reload()
    await expectText(worker, '离线缓存')
    await shot(worker, 'cg-offline-list')
    await worker.locator('.candidate', { hasText: '助餐' }).first().click()
    await worker.waitForURL(/\/cg\/order\//)
    await expectText(worker, '离线作业中')
    await worker.getByRole('button', { name: '签到', exact: true }).click()
    await expectText(worker, '第 2 步')
    for (const row of await worker.locator('.check-row').all()) await row.click()
    await worker.getByLabel('服务小结').fill('断网期间完成服务')
    await shot(worker, 'cg-offline-execute')
    await worker.getByRole('button', { name: '签退', exact: true }).click()
    await expectText(worker, '签退已保存在本机')
    const pending = await worker.evaluate(() => new Promise((resolve) => {
      const open = indexedDB.open('yihu-offline')
      open.onsuccess = () => {
        const request = open.result.transaction('queue').objectStore('queue').count()
        request.onsuccess = () => resolve(request.result)
      }
    }))
    if (pending < 2) throw new Error(`离线队列条数不对: ${pending}`)

    await worker.getByRole('button', { name: '返回工单列表' }).click()
    await worker.waitForURL(/\/cg$/)
    await worker.context().setOffline(false)
    await worker.evaluate(() => window.dispatchEvent(new Event('online')))
    await expectText(worker, '离线操作已同步', 15000)
    await shot(worker, 'cg-offline-synced')
    const detail = await grid.evaluate(async (id) => {
      const response = await fetch(`/api/orders/${id}`, { headers: { Authorization: `Bearer ${localStorage.getItem('yihu.token')}` } })
      return response.json()
    }, orderId)
    if (detail.status !== 'COMPLETED' || !detail.execution.offline_synced) throw new Error(`同步后状态不对: ${detail.status}`)
  })

  await step('备份、校验、两步恢复、核对报告', async () => {
    await admin.goto(`${BASE}/admin/backups`)
    await admin.getByRole('button', { name: '立即备份' }).click()
    await expectText(admin, '备份完成')
    await admin.waitForLoadState('networkidle')
    await admin.getByRole('button', { name: '校验' }).first().click()
    await expectText(admin, '校验通过')
    await shot(admin, 'admin-backups')
    await admin.getByRole('button', { name: '恢复', exact: true }).first().click()
    await expectText(admin, '确认恢复数据')
    await admin.getByLabel('本人登录密码').fill(PASSWORD)
    await admin.getByLabel('输入确认词').fill('确认恢复')
    await shot(admin, 'admin-restore-confirm')
    await admin.locator('.modal').getByRole('button', { name: '确认恢复' }).click()
    await expectText(admin, '恢复核对报告', 20000)
    await shot(admin, 'admin-restore-report')
  })

  await step('越权：家属直接访问管理页被拦回首页', async () => {
    await family.goto(`${BASE}/admin/users`)
    await family.waitForURL(/\/family/)
  })

  console.log('\n===== 汇总 =====')
  results.forEach((r) => console.log(r))
  console.log(`\n控制台错误、5xx 共 ${problems.length} 条`)
  problems.slice(0, 40).forEach((p) => console.log(p))
  await browser.close()
  process.exit(results.some((r) => r.startsWith('失败')) || problems.length ? 1 : 0)
}

main().catch((error) => {
  console.error(error)
  process.exit(2)
})
