// Service Worker：缓存应用外壳，断网时护理员端仍可打开。
// 接口数据不走这里，工单包存 IndexedDB，由页面代码管理。
// 构建时由 scripts/copy-public.mjs 注入全部构建产物，页面没打开过也能离线使用。
const VERSION = 'cc941929ec6c'
const CACHE = `yihu-shell-${VERSION}`
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', ...'/assets/AdminBackups-DRzR1cJq.js,/assets/AdminConfigs-C7sgf7ws.js,/assets/AdminJobs-DsrNGJ9H.js,/assets/AdminRoles-BpLGVCC-.js,/assets/AdminUsers-xjkcEcoQ.js,/assets/Alerts-CSlSH8GD.js,/assets/Audit-CVdRCYa5.js,/assets/BillDetail-CxYZFJOw.js,/assets/Bills-of98AHhc.js,/assets/CaregiverExecute-DFhb0jws.js,/assets/CaregiverToday-CVBIGbnU.js,/assets/Caregivers-Lv0fQMQ1.js,/assets/Catalog-BwssfhWf.js,/assets/Dashboard-CUNmVWWv.js,/assets/DispatchDesk-DcGSQaf7.js,/assets/EChart-Ry7wZ0po.js,/assets/ElderDetail-B78V5ZPk.js,/assets/ElderForm-DJC4APvV.js,/assets/Elders-B7swMxVu.js,/assets/Exceptions-CJo1oDpY.js,/assets/FamilyBills-icn5cxS7.js,/assets/FamilyBook-D9vYucLM.js,/assets/FamilyHealth-BBXSk_qT.js,/assets/FamilyHome-DllH67r1.js,/assets/HealthPanel-B7lK9nux.js,/assets/Login-DPCEHT8s.js,/assets/Modal-BN7GBszy.js,/assets/OrderDetail-K6_IVW6b.js,/assets/Orders-Cj5SqVou.js,/assets/Pager-B4fRanMe.js,/assets/Performance-CRBNUPnW.js,/assets/Quality-DSn5i6BN.js,/assets/Rules-BfrhNVrt.js,/assets/Statements-CK48EApv.js,/assets/Workbench-DjF9pMSr.js,/assets/display-DUlhfVTT.woff2,/assets/echarts-UrOnYY7y.js,/assets/index-5sk5dIYN.css,/assets/index-D_aLafD0.js'.split(',').filter((p) => p.startsWith('/'))]

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)
  if (request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return

  // 带哈希的构建产物内容不变，缓存优先
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        const copy = response.clone()
        caches.open(CACHE).then((cache) => cache.put(request, copy))
        return response
      })),
    )
    return
  }

  // 页面网络优先，断网时回退到缓存的外壳
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).then((response) => {
        const copy = response.clone()
        caches.open(CACHE).then((cache) => cache.put('/', copy))
        return response
      }).catch(() => caches.match('/')),
    )
    return
  }

  event.respondWith(caches.match(request).then((hit) => hit || fetch(request)))
})
