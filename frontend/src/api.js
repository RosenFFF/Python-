// 接口封装：统一鉴权、错误转换、文件下载。
const TOKEN_KEY = 'yihu.token'

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `请求失败（${status}）`)
    this.status = status
    this.code = body?.code
    this.details = body?.details
  }
}

export class NetworkError extends Error {
  constructor() {
    super('网络不可用')
  }
}

export const getToken = () => {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export const setToken = (token) => {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* 隐私模式下 localStorage 可能不可用 */ }
}

let onUnauthorized = () => {}
export const setUnauthorizedHandler = (handler) => { onUnauthorized = handler }

async function request(method, url, { body, form, raw } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let response
  try {
    response = await fetch(`/api${url}`, {
      method, headers, body: form || (body !== undefined ? JSON.stringify(body) : undefined),
    })
  } catch {
    throw new NetworkError()
  }
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    if (response.status === 401 && !url.startsWith('/auth/login')) onUnauthorized()
    throw new ApiError(response.status, data)
  }
  if (raw) return response
  return response.json()
}

export function query(params) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const api = {
  get: (url, params) => request('GET', url + query(params)),
  post: (url, body = {}) => request('POST', url, { body }),
  put: (url, body = {}) => request('PUT', url, { body }),
  patch: (url, body = {}) => request('PATCH', url, { body }),
  del: (url, body) => request('DELETE', url, { body }),
  upload: (url, file) => {
    const form = new FormData()
    form.append('file', file)
    return request('POST', url, { form })
  },
  async download(url, fallbackName) {
    const response = await request('POST', url, { raw: true })
    const disposition = response.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename\*=UTF-8''([^;]+)/)
    const name = match ? decodeURIComponent(match[1]) : fallbackName
    const blobUrl = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = name
    link.click()
    setTimeout(() => URL.revokeObjectURL(blobUrl), 2000)
  },
  async blobUrl(url) {
    const response = await request('GET', url, { raw: true })
    return URL.createObjectURL(await response.blob())
  },
}
