// Service Worker：缓存应用外壳，断网时护理员端仍可打开。
// 接口数据不走这里，工单包存 IndexedDB，由页面代码管理。
// 构建时由 scripts/copy-public.mjs 注入全部构建产物，页面没打开过也能离线使用。
const VERSION = '8bfb0c438a2b'
const CACHE = `yihu-shell-${VERSION}`
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', ...'/assets/AdminBackups-BifE-GR2.js,/assets/AdminConfigs-jnUccREt.js,/assets/AdminJobs-BXOEn77p.js,/assets/AdminRoles-C_JttxI4.js,/assets/AdminUsers-tKCMQeQg.js,/assets/Alerts-DqjsAtV5.js,/assets/Audit-BxRKRYe9.js,/assets/BillDetail-DkbVmn08.js,/assets/Bills-D-s_et72.js,/assets/CaregiverExecute-cymIx4ni.js,/assets/CaregiverToday-D0Yy6VMR.js,/assets/Caregivers-C9K0ATdP.js,/assets/Catalog-DlOizoVS.js,/assets/Dashboard-Dlgc1Cbb.js,/assets/DispatchDesk-Bmz6pVQ6.js,/assets/EChart-BhpLIyYB.js,/assets/ElderDetail-D8uOF2AH.js,/assets/ElderForm-Dq5Hbtxy.js,/assets/Elders-B3tBGtxZ.js,/assets/Exceptions-Ckny9h-u.js,/assets/FamilyBills-Cs9LSaMY.js,/assets/FamilyBook-D1qWSlJv.js,/assets/FamilyHealth-jLSHiMqY.js,/assets/FamilyHome-CPqQkSrr.js,/assets/HealthPanel-Duh2b-Pm.js,/assets/Login-D4QDEZpu.js,/assets/Modal-Dp_JVydC.js,/assets/OrderDetail-gNJz9H9s.js,/assets/Orders-CMEjcVwy.js,/assets/Pager-ecRAVbEj.js,/assets/Performance-CAFG_XSX.js,/assets/Quality-Ddiv69C0.js,/assets/Rules-CRxo2dy4.js,/assets/Statements-CHWxizs9.js,/assets/Workbench-BVa5uAP1.js,/assets/echarts-UrOnYY7y.js,/assets/index-BsVmQLcz.css,/assets/index-DXou3Ni8.js'.split(',').filter((p) => p.startsWith('/'))]

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
