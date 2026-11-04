// Service Worker：缓存应用外壳，断网时护理员端仍可打开。
// 接口数据不走这里，工单包存 IndexedDB，由页面代码管理。
// 构建时由 scripts/copy-public.mjs 注入全部构建产物，页面没打开过也能离线使用。
const VERSION = '2265ac93833f'
const CACHE = `yihu-shell-${VERSION}`
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', ...'/assets/AdminBackups-CXZTpXV6.js,/assets/AdminConfigs-ffFHzUyM.js,/assets/AdminJobs-XpYFxLit.js,/assets/AdminRoles-BNZ1LG1z.js,/assets/AdminUsers-DsNUY3CL.js,/assets/Alerts-DoRf0J0v.js,/assets/Audit-DTNbsO6k.js,/assets/BillDetail-CqBaQ3A8.js,/assets/Bills-6iq5aTAP.js,/assets/CaregiverExecute-BKj58i3C.js,/assets/CaregiverToday-CIvJ-NDL.js,/assets/Caregivers-Dcuw0ReA.js,/assets/Catalog-BLf1Y8hp.js,/assets/Dashboard-CJzL-hjK.js,/assets/DispatchDesk-DYNf-SPf.js,/assets/EChart-B8dzKN5d.js,/assets/ElderDetail-B-pKy7IH.js,/assets/ElderForm-Cb31bKIE.js,/assets/Elders-DlL6oJ4r.js,/assets/Exceptions-MF38nRfw.js,/assets/FamilyBills-DPRl6DnP.js,/assets/FamilyBook-D7FUYE2G.js,/assets/FamilyHealth-2tRKWfzp.js,/assets/FamilyHome-zagcW9sg.js,/assets/HealthPanel-ZS0MNs5l.js,/assets/Login-DwBMCtKs.js,/assets/Modal-DUmkgqZT.js,/assets/OrderDetail-CwjTvSvm.js,/assets/Orders-BMTa3BnQ.js,/assets/Pager-C4r4QRWl.js,/assets/Performance-C1PFABmI.js,/assets/Quality-CQ3sEUrL.js,/assets/Rules-WLVWCbRG.js,/assets/Statements-Bst7OoIR.js,/assets/Workbench-BzRqXx47.js,/assets/display-DUlhfVTT.woff2,/assets/echarts-UrOnYY7y.js,/assets/index-Cu_i1n2s.js,/assets/index-CxSIHrlP.css'.split(',').filter((p) => p.startsWith('/'))]

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
