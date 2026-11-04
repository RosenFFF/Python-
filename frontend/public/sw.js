// Service Worker：缓存应用外壳，断网时护理员端仍可打开。
// 接口数据不走这里，工单包存 IndexedDB，由页面代码管理。
// 构建时由 scripts/copy-public.mjs 注入全部构建产物，页面没打开过也能离线使用。
const VERSION = '__BUILD_VERSION__'
const CACHE = `yihu-shell-${VERSION}`
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', ...'__ASSET_LIST__'.split(',').filter((p) => p.startsWith('/'))]

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
