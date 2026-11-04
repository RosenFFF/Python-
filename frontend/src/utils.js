// 展示用的格式化函数。
import { state } from './store'

const pad = (n) => String(n).padStart(2, '0')

export function dt(value, withYear = false) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const head = withYear ? `${d.getFullYear()}-` : ''
  return `${head}${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function dateOnly(value) {
  if (!value) return '—'
  const d = new Date(value)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function toLocalInput(value) {
  const d = value instanceof Date ? value : new Date(value)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export const yuan = (cents) => (cents === null || cents === undefined ? '—' : (cents / 100).toFixed(2))
export const pct = (value, digits = 1) => (value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`)
export const money = (cents) => (cents === null || cents === undefined ? '—' : `¥${(cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)

export const label = (group, value) => state.meta?.labels?.[group]?.[value] ?? value ?? '—'

const STATUS_COLOR = {
  PENDING_ACCEPT: 'yellow', ACCEPTED: 'yellow', PENDING_DISPATCH: 'yellow', DISPATCHED: 'blue', IN_SERVICE: 'pine',
  COMPLETED: 'green', CANCELLED: '', NO_SHOW: 'red', OPEN: 'yellow', RESPONDED: 'blue', CLOSED: 'green',
  PROCESSING: 'blue', DRAFT: 'yellow', NEEDS_REVIEW: 'red', GRID_CONFIRMED: 'blue', ORG_CONFIRMED: 'blue', EFFECTIVE: 'green',
  RESOLVED: 'green', UNPAID: 'yellow', PAID: 'green', ASSIGNED: 'yellow', EXPLAINED: 'blue', CONCLUDED: 'green',
  PASS: 'green', FAIL: 'red', PENDING: 'yellow', DONE: 'green',
}
export const statusColor = (status) => STATUS_COLOR[status] ?? ''
export const levelColor = (level) => ({ RED: 'red', YELLOW: 'yellow', BLUE: 'blue' }[level] || '')

// 语音播报（适老化）：浏览器不支持时静默跳过
export function speak(text) {
  if (!('speechSynthesis' in window)) return false
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  utterance.rate = 0.9
  window.speechSynthesis.speak(utterance)
  return true
}

export function voiceSupported() {
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)
}

export function listen(onText, onEnd) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!Recognition) return null
  const recognizer = new Recognition()
  recognizer.lang = 'zh-CN'
  recognizer.interimResults = false
  recognizer.onresult = (event) => onText(event.results[0][0].transcript)
  recognizer.onend = () => onEnd?.()
  recognizer.onerror = () => onEnd?.()
  recognizer.start()
  return recognizer
}

// 服务器统一用东八区本地时间。前端所有“当前时间”都以服务器为准，
// 这样浏览器时区不同或演示时钟快进时，日期默认值和离线操作时间都不会错。
let clockSkew = 0
export function syncClock(serverIso) {
  if (serverIso) clockSkew = new Date(serverIso).getTime() - Date.now()
}
export const serverNow = () => new Date(Date.now() + clockSkew)

export const today = () => dateOnly(serverNow())
export const daysAgo = (n) => {
  const d = serverNow()
  d.setDate(d.getDate() - n)
  return dateOnly(d)
}
export const monthOf = (date = serverNow()) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}`
export const previousMonth = () => {
  const d = serverNow()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return monthOf(d)
}
