// 全局状态：当前用户、元数据、提示、适老化设置。
import { reactive } from 'vue'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'

export const state = reactive({
  user: null,
  meta: null,
  toasts: [],
  elderMode: false,
  online: navigator.onLine,
})

const ME_KEY = 'yihu.me'

function loadCachedMe() {
  try { return JSON.parse(localStorage.getItem(ME_KEY) || 'null') } catch { return null }
}

function saveCachedMe(me) {
  try {
    if (me) localStorage.setItem(ME_KEY, JSON.stringify(me))
    else localStorage.removeItem(ME_KEY)
  } catch { /* 忽略 */ }
}

let toastId = 0
export function toast(message, type = 'info', ms = 3200) {
  const id = ++toastId
  state.toasts.push({ id, message, type })
  setTimeout(() => {
    state.toasts = state.toasts.filter((item) => item.id !== id)
  }, ms)
}

export function showError(error) {
  toast(error?.message || '操作失败', 'error', 4500)
}

// 包一层：调用失败时统一提示，返回 undefined 让调用方简单判断
export async function run(action, success) {
  try {
    const result = await action()
    if (success) toast(success)
    return result ?? true
  } catch (error) {
    showError(error)
    return undefined
  }
}

export function setElderMode(on) {
  state.elderMode = on
  document.documentElement.dataset.elder = on ? 'on' : 'off'
  try { localStorage.setItem('yihu.elder', on ? '1' : '0') } catch { /* 忽略 */ }
}

export const can = (...permissions) => permissions.some((p) => state.user?.permissions?.includes(p))
export const isRole = (...roles) => roles.includes(state.user?.role)

export function homePath() {
  const role = state.user?.role
  if (role === 'elder_family') return '/family'
  if (role === 'caregiver') return '/cg'
  if (role === 'super_admin') return '/admin/users'
  if (role === 'street_supervisor') return '/stats/street'
  if (role === 'org_admin') return '/stats/org'
  return '/work'
}

export async function loadMeta() {
  if (!state.meta) {
    try {
      state.meta = await api.get('/meta')
      localStorage.setItem('yihu.meta', JSON.stringify(state.meta))
    } catch {
      // 断网时用上次缓存的元数据，界面标签不会变成英文编码
      let cached = null
      try { cached = JSON.parse(localStorage.getItem('yihu.meta') || 'null') } catch { /* 忽略 */ }
      state.meta = cached || { labels: {}, care_tags: [], skills: [], demo_mode: false }
    }
  }
}

export async function login(username, password) {
  const result = await api.post('/auth/login', { username, password })
  setToken(result.access_token)
  state.user = result.user
  saveCachedMe(result.user)
  if (result.user.role === 'elder_family') setElderMode(true)
  return result.user
}

export function logout() {
  setToken(null)
  saveCachedMe(null)
  state.user = null
  setElderMode(false)
}

export async function restoreSession() {
  await loadMeta()
  try {
    const saved = localStorage.getItem('yihu.elder')
    if (saved === '1') setElderMode(true)
  } catch { /* 忽略 */ }
  if (!getToken()) return
  try {
    state.user = await api.get('/auth/me')
    saveCachedMe(state.user)
  } catch (error) {
    // 断网时用缓存身份进入，护理员可以继续离线作业
    if (error.status) logout()
    else state.user = loadCachedMe()
  }
}

setUnauthorizedHandler(() => {
  logout()
  if (location.pathname !== '/login') location.assign('/login')
})

window.addEventListener('online', () => { state.online = true })
window.addEventListener('offline', () => { state.online = false })
