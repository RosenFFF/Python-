// 录制演示视频：驱动真实系统走完流程，用 CDP 截屏帧，配合旁白时长控制节奏。
// 用法：node tools/video/record_demo.cjs <baseUrl> <工作目录（含 tts 产物）> <现场照片> <设备密钥>
const { chromium } = require('playwright-core')
const { execFileSync } = require('child_process')
const fs = require('fs')
const path = require('path')

const [BASE, WORK, PHOTO, DEVICE_KEY] = process.argv.slice(2)
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const PASSWORD = 'Demo@2026'
const ROOT = path.resolve(__dirname, '..', '..')
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'scenes.json'), 'utf8'))
const durations = JSON.parse(fs.readFileSync(path.join(WORK, 'durations.json'), 'utf8'))
const FRAMES = path.join(WORK, 'frames')
const SHOTS = path.join(WORK, 'shots')
fs.mkdirSync(FRAMES, { recursive: true })
fs.mkdirSync(SHOTS, { recursive: true })

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const timeline = []
const frames = []
let page
let context
let startedAt
let cdp

async function caption(text) {
  await page.evaluate((value) => {
    let el = document.getElementById('demo-caption')
    if (!el) {
      el = document.createElement('div')
      el.id = 'demo-caption'
      el.style.cssText = 'position:fixed;left:50%;bottom:18px;transform:translateX(-50%);max-width:1120px;width:calc(100% - 80px);background:rgba(16,32,58,.88);color:#fff;font:500 19px/1.5 "Microsoft YaHei",sans-serif;padding:9px 18px;border-radius:8px;z-index:99999;text-align:center;pointer-events:none'
      document.body.appendChild(el)
    }
    el.textContent = value
    el.style.display = value ? 'block' : 'none'
  }, text).catch(() => {})
}

// 字幕跟随旁白分句显示，页面跳转后会重新注入
let currentLines = []
let captionTimer
function playCaptions(text, seconds) {
  clearInterval(captionTimer)
  const parts = text.split(/(?<=[。！？])/).flatMap((s) => (s.length > 42 ? s.split(/(?<=[，；：])/) : [s])).filter(Boolean)
  const lines = []
  for (const part of parts) {
    if (lines.length && (lines[lines.length - 1] + part).length <= 42) lines[lines.length - 1] += part
    else lines.push(part)
  }
  currentLines = lines
  const total = lines.reduce((sum, line) => sum + line.length, 0)
  const begin = Date.now()
  const show = () => {
    const elapsed = (Date.now() - begin) / 1000
    let acc = 0
    let line = lines[lines.length - 1]
    for (const item of lines) {
      acc += (item.length / total) * seconds
      if (elapsed < acc) { line = item; break }
    }
    caption(line)
  }
  show()
  captionTimer = setInterval(show, 350)
}

async function scene(id, action) {
  const info = config.scenes.find((s) => s.id === id)
  const seconds = durations[id]
  const at = (Date.now() - startedAt) / 1000
  timeline.push({ id, at, seconds })
  playCaptions(info.text, seconds)
  const begin = Date.now()
  try {
    if (action) await action()
  } catch (error) {
    console.log(`场景 ${id} 出错：${error.message.split('\n')[0]}`)
    await page.screenshot({ path: path.join(SHOTS, `fail-${id}.png`) }).catch(() => {})
    throw error
  }
  const remain = seconds * 1000 + 450 - (Date.now() - begin)
  if (remain > 0) await sleep(remain)
  console.log(`完成 ${id}，用时 ${((Date.now() - begin) / 1000).toFixed(1)}s，旁白 ${seconds.toFixed(1)}s`)
}

// 点击前先高亮，观众看得清操作了哪里
async function tap(locator, pause = 450) {
  await locator.waitFor({ timeout: 15000 })
  await locator.scrollIntoViewIfNeeded().catch(() => {})
  const handle = await locator.elementHandle()
  await handle.evaluate((el) => { el.style.outline = '3px solid #f0a020'; el.style.outlineOffset = '2px' }).catch(() => {})
  await sleep(pause)
  await handle.evaluate((el) => { el.style.outline = '' }).catch(() => {})
  await locator.click()
}

async function typeIn(locator, text, delay = 28) {
  await locator.waitFor({ timeout: 15000 })
  await locator.click()
  await locator.pressSequentially(text, { delay })
}

const shot = (name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) })

async function login(username) {
  await page.evaluate(() => { localStorage.removeItem('yihu.token'); localStorage.removeItem('yihu.me'); localStorage.removeItem('yihu.elder') }).catch(() => {})
  await page.goto(`${BASE}/login`)
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(PASSWORD)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'))
  await page.waitForLoadState('networkidle')
}

const apiCall = (method, url, body) => page.evaluate(async ([m, u, b]) => {
  const response = await fetch(`/api${u}`, {
    method: m, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('yihu.token')}` },
    body: b ? JSON.stringify(b) : undefined,
  })
  const data = await response.json()
  if (!response.ok) throw new Error(`${u}: ${data.message}`)
  return data
}, [method, url, body])

async function openStar() {
  await page.goto(`${BASE}/elders`)
  await page.getByLabel('姓名、编号、完整手机号或身份证').fill('王秀英')
  await page.getByRole('button', { name: '查询' }).click()
  await sleep(500)
  await page.locator('tbody tr', { hasText: '王秀英' }).first().click()
  await page.waitForURL(/\/elders\/\d+/)
  await page.waitForLoadState('networkidle')
}

async function main() {
  const browser = await chromium.launch({ executablePath: EDGE, headless: true })
  context = await browser.newContext({ viewport: { width: 1280, height: 720 }, locale: 'zh-CN' })
  page = await context.newPage()
  page.on('pageerror', (e) => console.log('页面错误', e.message))

  cdp = await context.newCDPSession(page)
  cdp.on('Page.screencastFrame', async ({ data, metadata, sessionId }) => {
    const index = frames.length
    frames.push({ t: metadata.timestamp })
    fs.writeFileSync(path.join(FRAMES, `${String(index).padStart(6, '0')}.jpg`), Buffer.from(data, 'base64'))
    await cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
  })

  await page.setContent(`<!doctype html><html><body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#163a6b;color:#fff;font-family:'Microsoft YaHei'">
    <div style="font-size:64px;font-weight:700;letter-spacing:.1em">颐护通</div>
    <div style="font-size:26px;margin-top:14px;color:#d5dfec">社区智慧养老服务与健康监测平台</div>
    <div style="font-size:18px;margin-top:30px;color:#a9bad2">2026 年第四届大学生信息系统创新大赛 · Python 编程巅峰赛</div></body></html>`)
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 78, maxWidth: 1280, maxHeight: 720, everyNthFrame: 1 })
  startedAt = Date.now()
  // 让静止画面也持续出帧：画面不变时 screencast 不发帧，靠 ffconcat 的时长补齐
  await scene('intro')

  await scene('family_home', async () => {
    await page.goto(`${BASE}/login`)
    await sleep(3200)
    await shot('login')
    await login('family_wang')
    await sleep(600)
    await shot('family-home')
  })
  let bookedId
  await scene('family_book', async () => {
    await page.goto(`${BASE}/family/book`)
    await page.waitForLoadState('networkidle')
    await typeIn(page.getByPlaceholder('比如：帮我约明天下午两点助浴'), '帮我约后天下午三点半助浴', 90)
    await tap(page.getByRole('button', { name: '识别' }))
    await page.getByText('已填入下方').waitFor()
    await sleep(800)
    await shot('family-book')
  })
  await scene('family_result', async () => {
    await page.getByRole('button', { name: '提交预约' }).scrollIntoViewIfNeeded()
    await tap(page.getByRole('button', { name: '提交预约' }))
    await page.getByText('预约已提交').first().waitFor()
    await page.evaluate(() => window.scrollTo(0, 0))
    await sleep(500)
    await tap(page.getByRole('button', { name: '查看进度' }))
    await page.waitForURL(/\/orders\/\d+/)
    bookedId = Number(page.url().split('/').pop())
  })

  await scene('grid_bench', async () => {
    await login('grid_zhang')
    await sleep(500)
    await shot('grid-workbench')
  })
  await scene('grid_accept', async () => {
    const row = page.locator('div.row.between', { hasText: '王秀英 助浴' }).filter({ has: page.getByRole('button', { name: '受理' }) }).first()
    await tap(row.getByRole('button', { name: '受理' }))
    await sleep(600)
    await tap(page.getByRole('button', { name: '确认受理' }))
    await page.getByText('已受理，进入待派单').waitFor()
  })
  await scene('dispatch', async () => {
    await page.goto(`${BASE}/dispatch/${bookedId}`)
    await page.locator('.candidate').first().waitFor()
    await sleep(1500)
    await shot('grid-dispatch')
    await sleep(6000)
    await tap(page.getByRole('button', { name: /查看被过滤的护理员/ }))
    await sleep(600)
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  })
  await scene('dispatch_go', async () => {
    await page.evaluate(() => window.scrollTo(0, 0))
    await tap(page.getByRole('button', { name: '一键派单' }))
    await page.getByText('已邀约').first().waitFor()
    await sleep(300)
    await shot('grid-dispatch-offered')
  })
  await scene('timeout', async () => {
    const seededOffer = (await apiCall('GET', '/orders?status=PENDING_DISPATCH&size=30')).items.find((o) => o.item_name === '助医')
    await tap(page.getByRole('button', { name: '+31 分钟' }))
    await page.getByText('时钟已快进').waitFor()
    await sleep(900)
    await page.goto(`${BASE}/dispatch/${seededOffer.id}`)
    await page.getByText('派单记录').waitFor()
    await sleep(500)
    await page.locator('h2', { hasText: '派单记录' }).scrollIntoViewIfNeeded()
    await shot('grid-offer-timeout')
    // 主流程的邀约也已超时，手工改派回 cg01，保证后面演示能接上
    const me = (await apiCall('GET', '/caregivers')).find((c) => c.name === '王小红')
    const current = await apiCall('GET', `/orders/${bookedId}/dispatch-history`)
    const active = current.offers.find((o) => o.status === 'OFFERED')
    if (!active || active.caregiver_id !== me.id) {
      await apiCall('POST', `/orders/${bookedId}/reassign`, { caregiver_id: me.id, reason: '演示：改派回王小红' })
    }
  })

  await scene('cg_today', async () => {
    await login('cg01')
    await page.getByRole('button', { name: '接单', exact: true }).first().waitFor()
    await sleep(800)
    await shot('cg-today')
    const offer = page.locator('.candidate', { hasText: '助浴' }).filter({ has: page.getByRole('button', { name: '接单', exact: true }) }).first()
    await tap(offer.getByRole('button', { name: '接单', exact: true }))
    await page.getByText('接单成功').waitFor()
    await sleep(700)
  })
  let visitId
  await scene('cg_far', async () => {
    const card = page.locator('.candidate', { hasText: '助浴' }).filter({ hasText: '已派单' }).first()
    await tap(card)
    await page.waitForURL(/\/cg\/order\//)
    visitId = Number(page.url().split('/').pop())
    await tap(page.getByRole('button', { name: '模拟定位偏差' }))
    await tap(page.getByRole('button', { name: '签到', exact: true }))
    await page.getByText('定位偏差超出范围').waitFor()
    await sleep(900)
    await shot('cg-too-far')
    await typeIn(page.getByPlaceholder('比如：小区门禁、地址标注偏差'), '小区门禁，地址标注偏差', 60)
    await tap(page.getByRole('button', { name: '继续签到' }))
    await page.getByText('第 2 步').waitFor()
  })
  await scene('cg_exec', async () => {
    for (const row of await page.locator('.check-row').all()) {
      await row.scrollIntoViewIfNeeded()
      await row.click()
      await sleep(320)
    }
    await page.locator('input[type=file]').scrollIntoViewIfNeeded()
    await page.locator('input[type=file]').setInputFiles(PHOTO)
    await page.getByText('已上传，压缩').waitFor()
    await sleep(500)
    await typeIn(page.getByLabel('收缩压'), '152', 120)
    await typeIn(page.getByLabel('舒张压'), '88', 120)
    await shot('cg-execute')
  })
  await scene('cg_out', async () => {
    await apiCall('POST', '/demo/clock/advance', { minutes: 62 })
    await typeIn(page.getByLabel('服务小结'), '助浴顺利完成，老人精神状态良好', 50)
    await tap(page.getByRole('button', { name: '签退', exact: true }))
    await page.getByText('服务已完成').waitFor()
    await page.evaluate(() => window.scrollTo(0, 0))
    await shot('cg-done')
  })

  await scene('family_detail', async () => {
    await login('family_wang')
    await page.goto(`${BASE}/orders/${visitId}`)
    await page.getByText('服务证据链').waitFor()
    await sleep(1800)
    await shot('family-order-detail')
    await page.evaluate(() => window.scrollTo(0, 500))
    await sleep(2200)
    await page.evaluate(() => window.scrollTo(0, 0))
    await tap(page.getByRole('button', { name: '评价服务' }))
    for (const group of await page.locator('.stars').all()) {
      await group.getByRole('button', { name: '3 星' }).click()
      await sleep(260)
    }
    await tap(page.getByRole('button', { name: '提交评价' }))
    await page.getByText('48 小时内回访').waitFor()
  })

  await scene('exception', async () => {
    await login('grid_zhang')
    await page.goto(`${BASE}/exceptions`)
    await tap(page.getByRole('button', { name: '接手' }).first())
    await page.getByText('已接手处置').waitFor()
    await sleep(500)
    await shot('grid-exceptions')
    await tap(page.getByRole('button', { name: '闭环' }).first())
    await page.getByLabel('工单处理').selectOption('NO_SHOW')
    await typeIn(page.getByLabel('处置结果'), '联系家属确认老人外出', 50)
    await tap(page.getByRole('button', { name: '确认闭环' }))
    await page.getByText('异常已闭环').waitFor()
  })

  await scene('wearable', async () => {
    await page.goto(`${BASE}/alerts`)
    await page.waitForLoadState('networkidle')
    await sleep(1500)
    const output = execFileSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [
      path.join(ROOT, 'backend', 'scripts', 'simulate_wearable.py'), '--url', BASE, '--key', DEVICE_KEY,
      '--devices', 'WB-10000', '--scenario', 'hypoxia', '--rounds', '3', '--interval', '0.3',
    ], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
    console.log(output)
    await page.reload()
    await page.locator('.alert-card.RED', { hasText: '血氧' }).first().waitFor()
    await sleep(600)
    await shot('grid-alerts')
    await tap(page.locator('.alert-card.RED', { hasText: '血氧' }).first())
    await page.locator('.modal').waitFor()
    await sleep(800)
    await shot('grid-alert-detail')
    await sleep(3000)
    await page.locator('.modal').getByRole('button', { name: '关闭' }).click()
  })
  await scene('escalate', async () => {
    await tap(page.getByRole('button', { name: '+31 分钟' }))
    await page.getByText('时钟已快进').waitFor()
    await page.locator('.alert-card.RED', { hasText: '血氧' }).filter({ hasText: '已升级' }).first().waitFor()
    await sleep(700)
    await shot('grid-escalated')
    await sleep(1500)
    await tap(page.locator('.alert-card.RED', { hasText: '血氧' }).first())
    await sleep(2000)
  })
  await scene('close', async () => {
    await typeIn(page.getByLabel('到场或电话确认情况'), '已到场，老人血氧偏低，联系家属送医', 40)
    await tap(page.getByRole('button', { name: '记录响应' }))
    await page.getByText('已记录响应').waitFor()
    await sleep(700)
    await tap(page.getByRole('button', { name: '闭环预警' }))
    await page.locator('label', { hasText: '已转诊' }).click()
    await typeIn(page.getByLabel('转诊医院'), '青松社区卫生服务中心', 40)
    await sleep(400)
    await tap(page.getByRole('button', { name: '确认闭环' }))
    await page.getByText('预警已闭环').waitFor()
  })

  await scene('reveal', async () => {
    await openStar()
    await sleep(1500)
    await shot('grid-elder-detail')
    await tap(page.getByRole('button', { name: '查看完整信息' }))
    await typeIn(page.getByPlaceholder('比如：联系紧急联系人'), '联系紧急联系人核实情况', 45)
    await page.getByLabel('本人登录密码').fill(PASSWORD)
    await tap(page.getByRole('button', { name: '确认查看' }))
    await page.getByText('本次查看已留痕').first().waitFor()
    await sleep(600)
    await shot('grid-reveal')
    await sleep(1800)
    await tap(page.getByRole('button', { name: '变更与查看日志' }))
    await sleep(1000)
  })

  await scene('quality', async () => {
    await login('comm_liu')
    await page.goto(`${BASE}/quality`)
    await tap(page.getByRole('button', { name: '回访', exact: true }).first())
    await page.locator('.modal label', { hasText: '转投诉' }).click()
    await typeIn(page.getByLabel('核实情况'), '家属反映服务敷衍，要求投诉', 45)
    await tap(page.getByRole('button', { name: '保存' }))
    await sleep(900)
    await shot('comm-quality')
    await sleep(1500)
    await tap(page.getByRole('button', { name: '下结论' }).first())
    await typeIn(page.getByLabel('结论依据'), '机构已核实，情况属实', 45)
    await page.locator('.modal label', { hasText: '退费' }).click()
    await sleep(800)
    await shot('comm-conclude')
    await tap(page.getByRole('button', { name: '归档结论' }))
    await sleep(600)
  })

  let billUrl
  await scene('bill', async () => {
    await page.goto(`${BASE}/bills`)
    await sleep(1300)
    await shot('comm-bills')
    await tap(page.locator('tbody tr', { hasText: '幸福里' }).filter({ hasText: '待人工复核' }).getByRole('button', { name: '详情' }))
    await page.waitForURL(/\/bills\/\d+/)
    billUrl = page.url()
    await page.getByText('对账差异清单').waitFor()
    await sleep(800)
    await shot('comm-bill-detail')
    await sleep(5000)
    await page.locator('h2', { hasText: '对账差异清单' }).scrollIntoViewIfNeeded()
  })
  await scene('bill_resolve', async () => {
    let first = true
    for (let guard = 0; guard < 60; guard += 1) {
      const button = page.getByRole('button', { name: '处理', exact: true }).first()
      if (await button.count() === 0) break
      if (first) await tap(button); else await button.click()
      await page.getByLabel('说明').fill('逐条核实后处理')
      if (first) await sleep(900)
      await page.locator('.modal').getByRole('button', { name: '保存' }).click()
      await page.locator('.modal').waitFor({ state: 'detached' })
      await sleep(280)
      first = false
    }
    await page.evaluate(() => window.scrollTo(0, 0))
    await tap(page.getByRole('button', { name: '人工复核' }))
    await typeIn(page.getByLabel('复核意见'), '差异已逐条核实', 40)
    await tap(page.getByRole('button', { name: '完成复核' }))
    await page.getByText('人工复核完成').waitFor()
  })
  await scene('bill_flow', async () => {
    await login('grid_zhang')
    await page.goto(billUrl)
    await tap(page.getByRole('button', { name: '网格员核对提交' }), 250)
    await page.getByText('已核对').first().waitFor()
    for (const name of ['org_zhao', 'org_gao']) {
      await login(name)
      await page.goto(billUrl)
      await tap(page.getByRole('button', { name: '机构确认' }), 250)
      await sleep(600)
    }
    await login('street_sun')
    await page.goto(billUrl)
    await page.getByText('监管抽查').first().waitFor()
    await page.locator('h2', { hasText: '监管抽查' }).scrollIntoViewIfNeeded()
    await sleep(1000)
    await shot('street-sampling')
    let first = true
    for (let guard = 0; guard < 60; guard += 1) {
      const button = page.getByRole('button', { name: '核查' }).first()
      if (await button.count() === 0) break
      await button.click()
      await sleep(first ? 2200 : 180)
      await page.getByRole('button', { name: '提交抽查结果' }).click()
      await page.locator('.modal').waitFor({ state: 'detached' })
      await sleep(250)
      first = false
    }
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.getByText('已生效').first().waitFor()
    await sleep(400)
    await shot('street-effective')
  })

  await scene('stats', async () => {
    await login('comm_liu')
    await page.goto(`${BASE}/stats/community`)
    await page.locator('canvas').nth(3).waitFor()
    await sleep(3800)
    await shot('comm-dashboard')
    await login('org_zhao')
    await page.goto(`${BASE}/stats/org`)
    await page.locator('canvas').nth(3).waitFor()
    await sleep(3800)
    await shot('org-dashboard')
    await login('street_sun')
    await page.goto(`${BASE}/stats/street`)
    await page.locator('canvas').nth(3).waitFor()
    await sleep(1500)
    await tap(page.getByRole('button', { name: '近一年' }))
    await sleep(2500)
    await shot('street-dashboard')
  })

  await scene('security', async () => {
    await page.goto(`${BASE}/audit`)
    await page.locator('tbody tr').first().waitFor()
    await sleep(2000)
    await tap(page.getByRole('button', { name: '校验哈希链' }))
    await page.getByText('审计链完整').waitFor()
    await sleep(1000)
    await shot('street-audit')
    await sleep(3000)
    await login('admin')
    await page.goto(`${BASE}/admin/roles`)
    await page.locator('table.matrix').waitFor()
    await sleep(1200)
    await shot('admin-roles')
  })

  await scene('backup', async () => {
    await page.goto(`${BASE}/admin/backups`)
    await tap(page.getByRole('button', { name: '立即备份' }))
    await page.getByText('备份完成').waitFor()
    await sleep(1000)
    await shot('admin-backups')
    await tap(page.getByRole('button', { name: '恢复', exact: true }).first())
    await page.getByText('确认恢复数据').waitFor()
    await sleep(2500)
    await page.getByLabel('本人登录密码').fill(PASSWORD)
    await typeIn(page.getByLabel('输入确认词'), '确认恢复', 90)
    await sleep(500)
    await shot('admin-restore-confirm')
    await tap(page.locator('.modal').getByRole('button', { name: '确认恢复' }))
    await page.getByText('恢复核对报告').waitFor({ timeout: 25000 })
    await sleep(800)
    await shot('admin-restore-report')
  })

  await scene('offline', async () => {
    await login('grid_zhang')
    const items = await apiCall('GET', '/catalog/items?on_shelf_only=true')
    const meal = items.find((i) => i.name === '助餐')
    const elders = (await apiCall('GET', '/elders?size=50')).items
    const now = new Date((await apiCall('GET', '/demo/clock')).now)
    const start = new Date(now.getTime() + 3 * 3600e3)
    const pad = (n) => String(n).padStart(2, '0')
    const order = await apiCall('POST', '/orders', {
      elder_id: elders.find((e) => e.name !== '王秀英').id, item_id: meal.id,
      scheduled_start: `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:00:00`,
    })
    await apiCall('POST', `/orders/${order.id}/accept`, { note: '离线演示' })
    const me = (await apiCall('GET', '/caregivers')).find((c) => c.name === '王小红')
    const offer = await apiCall('POST', `/orders/${order.id}/reassign`, { caregiver_id: me.id, reason: '离线演示' })
    await login('cg01')
    await apiCall('POST', `/offers/${offer.offer_id}/accept`)
    await page.reload()
    await page.waitForFunction(() => navigator.serviceWorker.controller !== null, null, { timeout: 20000 }).catch(() => {})
    await sleep(1200)
    await context.setOffline(true)
    await page.reload()
    await page.getByText('离线缓存').waitFor()
    await sleep(900)
    await shot('cg-offline-list')
    await tap(page.locator('.candidate', { hasText: '助餐' }).first(), 250)
    await page.waitForURL(/\/cg\/order\//)
    await page.getByRole('button', { name: '签到', exact: true }).click()
    await page.getByText('第 2 步').waitFor()
    for (const row of await page.locator('.check-row').all()) { await row.click(); await sleep(180) }
    await page.getByLabel('服务小结').fill('断网期间完成服务')
    await page.getByRole('button', { name: '签退', exact: true }).click()
    await page.getByText('签退已保存在本机').waitFor()
    await sleep(800)
    await shot('cg-offline-execute')
    await page.getByRole('button', { name: '返回工单列表' }).click()
    await page.waitForURL(/\/cg$/)
    await context.setOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    await page.getByText('离线操作已同步').waitFor({ timeout: 15000 })
    await shot('cg-offline-synced')
  })

  await scene('outro', async () => {
    clearInterval(captionTimer)
    await page.setContent(`<!doctype html><html><body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#163a6b;color:#fff;font-family:'Microsoft YaHei'">
      <div style="font-size:54px;font-weight:700;letter-spacing:.1em">颐护通</div>
      <div style="font-size:22px;margin-top:18px;color:#d5dfec">预约 · 派单 · 执行 · 预警 · 评价 · 结算，六条流程闭环</div>
      <div style="font-size:18px;margin-top:12px;color:#a9bad2">Docker 部署 · AI 辅助 · 适老化 · 离线同步</div></body></html>`)
    playCaptions(config.scenes.find((s) => s.id === 'outro').text, durations.outro)
  })
  clearInterval(captionTimer)
  await sleep(600)
  await cdp.send('Page.stopScreencast')
  await browser.close()

  // 帧时间线：每帧持续到下一帧，静止画面靠持续时长保留
  const base = frames[0].t
  const list = []
  const realEnd = (Date.now() - startedAt) / 1000
  frames.forEach((frame, index) => {
    const next = index + 1 < frames.length ? frames[index + 1].t - base : Math.max(realEnd, frame.t - base + 0.5)
    list.push(`file '${path.join(FRAMES, `${String(index).padStart(6, '0')}.jpg`).replace(/\\/g, '/')}'`)
    list.push(`duration ${Math.max(next - (frame.t - base), 0.001).toFixed(3)}`)
  })
  list.push(`file '${path.join(FRAMES, `${String(frames.length - 1).padStart(6, '0')}.jpg`).replace(/\\/g, '/')}'`)
  fs.writeFileSync(path.join(WORK, 'frames.ffconcat'), `ffconcat version 1.0\n${list.join('\n')}\n`)
  fs.writeFileSync(path.join(WORK, 'timeline.json'), JSON.stringify({ timeline, firstFrameOffset: 0, frames: frames.length, currentLines }, null, 1))
  console.log(`录制完成：${frames.length} 帧，时长 ${realEnd.toFixed(0)} 秒`)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
