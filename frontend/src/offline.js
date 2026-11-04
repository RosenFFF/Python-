// 护理员端离线队列：断网时把操作存进 IndexedDB，联网后批量同步。
// 服务端按 op_id 幂等，重复提交安全。
import { reactive } from 'vue'
import { api, NetworkError } from './api'
import { serverNow, toLocalInput } from './utils'

const DB_NAME = 'yihu-offline'
const STORE_QUEUE = 'queue'
const STORE_KV = 'kv'

export const offlineState = reactive({ pending: 0, syncing: false, lastResult: null })

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => {
      const db = request.result
      db.createObjectStore(STORE_QUEUE, { keyPath: 'op_id' })
      db.createObjectStore(STORE_KV)
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function tx(store, mode, action) {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(store, mode)
    const result = action(transaction.objectStore(store))
    transaction.oncomplete = () => { db.close(); resolve(result.result) }
    transaction.onerror = () => { db.close(); reject(transaction.error) }
  })
}

const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : `op-${Date.now()}-${Math.random().toString(16).slice(2)}`)

// 操作的实际发生时间，服务端据此计算服务时长
const occurredAt = () => `${toLocalInput(serverNow())}:${String(serverNow().getSeconds()).padStart(2, '0')}`

export async function refreshPending() {
  offlineState.pending = (await tx(STORE_QUEUE, 'readonly', (s) => s.count())) || 0
}

// 同一秒内的操作靠序号保序。取毫秒时间戳保证跨页面刷新后仍然递增
let lastSeq = 0
function nextSeq() {
  lastSeq = Math.max(Date.now(), lastSeq + 1)
  return lastSeq
}

export async function enqueue(kind, orderId, payload) {
  const op = { op_id: uuid(), kind, order_id: orderId, occurred_at: occurredAt(), seq: nextSeq(), payload }
  await tx(STORE_QUEUE, 'readwrite', (s) => s.put(op))
  await refreshPending()
  return op
}

export const listQueue = () => tx(STORE_QUEUE, 'readonly', (s) => s.getAll())

export async function saveBootstrap(data) {
  await tx(STORE_KV, 'readwrite', (s) => s.put(data, 'bootstrap'))
}

export const loadBootstrap = () => tx(STORE_KV, 'readonly', (s) => s.get('bootstrap'))

// 把离线操作也应用到本地工单包，界面能立刻看到进度
export async function updateCachedOrder(orderId, patch) {
  const data = await loadBootstrap()
  if (!data) return
  const order = data.orders.find((item) => item.id === orderId)
  if (order) {
    Object.assign(order, patch)
    await saveBootstrap(JSON.parse(JSON.stringify(data)))
  }
}

export async function flushQueue() {
  if (offlineState.syncing) return null
  const ops = await listQueue()
  if (!ops.length) return null
  offlineState.syncing = true
  try {
    const result = await api.post('/sync/batch', { ops })
    // 成功和重复的条目出队；被拒绝的也出队并展示给护理员，避免卡死队列
    await Promise.all(result.results.map((item) => tx(STORE_QUEUE, 'readwrite', (s) => s.delete(item.op_id))))
    offlineState.lastResult = result
    await refreshPending()
    return result
  } catch (error) {
    if (error instanceof NetworkError) return null
    throw error
  } finally {
    offlineState.syncing = false
  }
}

export function startAutoSync(onSynced) {
  const sync = async () => {
    try {
      const result = await flushQueue()
      if (result) onSynced?.(result)
    } catch { /* 下次联网再试 */ }
  }
  window.addEventListener('online', sync)
  refreshPending().then(sync)
  return () => window.removeEventListener('online', sync)
}

export function registerServiceWorker() {
  if ('serviceWorker' in navigator && import.meta.env.PROD) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* 不支持时只是没有离线能力 */ })
  }
}
