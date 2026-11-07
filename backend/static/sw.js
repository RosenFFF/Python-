// Service Worker：缓存应用外壳，断网时护理员端仍可打开。
// 接口数据不走这里，工单包存 IndexedDB，由页面代码管理。
// 构建时由 scripts/copy-public.mjs 注入全部构建产物，页面没打开过也能离线使用。
const VERSION = '81402fcaf952'
const CACHE = `yihu-shell-${VERSION}`
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', ...'/assets/AdminBackups-BuTx2-bA.js,/assets/AdminConfigs-BGCOQpHi.js,/assets/AdminJobs-CAg1acn1.js,/assets/AdminRoles-DffO3i08.js,/assets/AdminUsers-C7SM5-ot.js,/assets/Alerts-DL2o2U5E.js,/assets/Audit-CaAMbgwi.js,/assets/BillDetail-3vKGl3__.js,/assets/Bills-BukFh7Ef.js,/assets/CaregiverExecute-hxI3Gabj.js,/assets/CaregiverToday-CXKpkBUq.js,/assets/Caregivers-DcQKG-Cc.js,/assets/Catalog-COBwspnX.js,/assets/Dashboard-c-KR8zqB.js,/assets/DispatchDesk-XTLac65G.js,/assets/EChart-CWR4F-zy.js,/assets/ElderDetail-f6sv4xc1.js,/assets/ElderForm-D7bOztY5.js,/assets/Elders-Ch_dQUMd.js,/assets/Exceptions-CCp19umo.js,/assets/FamilyBills-DHFVdjNH.js,/assets/FamilyBook-HsghO6fe.js,/assets/FamilyHealth-ghQT9Jx1.js,/assets/FamilyHome-B2SJk5nt.js,/assets/HealthPanel-B3c5c0Lg.js,/assets/Login-Cdh2uDZ7.js,/assets/Modal-DTYgTebZ.js,/assets/OrderDetail-C8qymgRg.js,/assets/Orders-yQ3_qcyu.js,/assets/Pager-CE3LMIUa.js,/assets/Performance-jRszFsp3.js,/assets/Quality-CwU1g7fr.js,/assets/Rules-BBlGpdkv.js,/assets/Statements-Bgb9KS8C.js,/assets/Workbench-CdkMpPqE.js,/assets/display-DUlhfVTT.woff2,/assets/echarts-UrOnYY7y.js,/assets/index-BNgy9iQW.js,/assets/index-DBZ__eDX.css'.split(',').filter((p) => p.startsWith('/'))]

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
