<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { offlineState } from './offline'
import { can, isRole, logout, run, setElderMode, state, toast } from './store'
import { dt, speak, syncClock } from './utils'

const route = useRoute()
const router = useRouter()
const bare = computed(() => route.meta.bare)
const iconUrl = '/icon.svg'

const groups = computed(() => {
  const all = [
    { title: '服务', items: [
      { to: '/family', text: '首页', show: isRole('elder_family') },
      { to: '/family/book', text: '预约服务', show: isRole('elder_family') },
      { to: '/orders', text: isRole('elder_family') ? '我的服务' : '工单管理', show: can('order:read') && !isRole('caregiver') },
      { to: '/family/health', text: '健康记录', show: isRole('elder_family') },
      { to: '/family/bills', text: '我的账单', show: isRole('elder_family') },
      { to: '/cg', text: '今日工单', show: isRole('caregiver') },
      { to: '/work', text: '工作台', show: can('dispatch:manage') },
      { to: '/exceptions', text: '异常处置', show: can('exception:handle') },
      { to: '/alerts', text: '预警看板', show: can('alert:read') && !isRole('elder_family') },
    ] },
    { title: '档案与目录', items: [
      { to: '/elders', text: '老人档案', show: can('elder:read') && !isRole('elder_family', 'caregiver') },
      { to: '/catalog', text: '服务目录', show: can('catalog:read') && !isRole('elder_family', 'caregiver') },
      { to: '/caregivers', text: '护理员管理', show: can('caregiver:write') },
    ] },
    { title: '质量与结算', items: [
      { to: '/quality', text: '回访与投诉', show: can('complaint:read', 'followup:handle') && !isRole('elder_family') },
      { to: '/performance', text: '绩效榜', show: can('perf:read') },
      { to: '/bills', text: '月度账单', show: can('billing:read') },
      { to: '/statements', text: '缴费登记', show: can('billing:payment') },
    ] },
    { title: '统计', items: [
      { to: '/stats/community', text: '社区看板', show: can('stats:community') },
      { to: '/stats/org', text: '机构看板', show: can('stats:org') },
      { to: '/stats/street', text: '街道看板', show: can('stats:street') },
    ] },
    { title: '系统', items: [
      { to: '/rules', text: '预警规则', show: can('rule:manage') },
      { to: '/audit', text: '审计日志', show: can('audit:read') },
      { to: '/admin/users', text: '用户管理', show: can('user:manage') },
      { to: '/admin/roles', text: '角色权限', show: can('role:manage') },
      { to: '/admin/configs', text: '业务参数', show: can('config:manage') },
      { to: '/admin/backups', text: '备份恢复', show: can('backup:manage') },
      { to: '/admin/jobs', text: '定时任务', show: can('job:manage') },
    ] },
  ]
  return all.map((g) => ({ ...g, items: g.items.filter((i) => i.show) })).filter((g) => g.items.length)
})

const notes = ref({ unread: 0, items: [] })
const showNotes = ref(false)
const clockInfo = ref(null)
let timer

async function refreshNotes() {
  if (!state.user || !state.online) return
  try {
    notes.value = await api.get('/notifications', { limit: 30 })
    if (state.meta?.demo_mode) {
      clockInfo.value = await api.get('/demo/clock')
      syncClock(clockInfo.value.now)
    }
  } catch { /* 轮询失败不打扰用户 */ }
}

function openNote(item) {
  showNotes.value = false
  if (!item.read_at) api.post(`/notifications/${item.id}/read`).then(refreshNotes).catch(() => {})
  const targets = {
    order: `/orders/${item.ref_id}`, alert: '/alerts', exception: '/exceptions', bill: `/bills/${item.ref_id}`,
    complaint: '/quality', followup: '/quality', review: `/orders/${item.ref_id}`, offer: '/cg', statement: '/family/bills',
  }
  const path = targets[item.ref_type]
  if (path) router.push(path)
}

async function readAll() {
  await api.post('/notifications/read-all').catch(() => {})
  refreshNotes()
}

const jumping = ref(false)
async function advance(minutes) {
  jumping.value = true
  const result = await run(() => api.post('/demo/clock/advance', { minutes }))
  jumping.value = false
  if (result) {
    clockInfo.value = { now: result.now, offset_minutes: result.offset_minutes }
    syncClock(result.now)
    const worked = Object.values(result.jobs).reduce((a, b) => a + b, 0)
    toast(`时钟已快进 ${minutes} 分钟，定时任务处理 ${worked} 项`)
    refreshNotes()
    window.dispatchEvent(new Event('yihu:refresh'))
  }
}

async function resetClock() {
  const result = await run(() => api.post('/demo/clock/reset'), '演示时钟已恢复')
  if (result) {
    clockInfo.value = result
    syncClock(result.now)
    window.dispatchEvent(new Event('yihu:refresh'))
  }
}

function readAloud() {
  const main = document.querySelector('[data-speak]') || document.querySelector('.content')
  speak((main?.innerText || '').slice(0, 600))
}

function signOut() {
  logout()
  router.push('/login')
}

onMounted(() => {
  refreshNotes()
  timer = setInterval(refreshNotes, 15000)
})
onUnmounted(() => clearInterval(timer))
watch(() => state.user?.id, refreshNotes)
</script>

<template>
  <div class="toasts">
    <div v-for="t in state.toasts" :key="t.id" class="toast" :class="t.type">{{ t.message }}</div>
  </div>

  <router-view v-if="bare" />

  <div v-else-if="state.user" class="shell">
    <aside class="side">
      <div class="brand">
        <img :src="iconUrl" alt="" />
        <div><b>颐护通</b><span>社区智慧养老服务平台</span></div>
      </div>
      <nav class="nav" aria-label="主导航">
        <template v-for="group in groups" :key="group.title">
          <div class="nav-group">{{ group.title }}</div>
          <router-link v-for="item in group.items" :key="item.to" :to="item.to">{{ item.text }}</router-link>
        </template>
      </nav>
    </aside>

    <div class="main">
      <div v-if="!state.online" class="offline-bar">
        当前处于离线状态<span v-if="offlineState.pending">，{{ offlineState.pending }} 条操作待同步，联网后自动提交</span>
      </div>
      <header class="topbar">
        <b>{{ route.meta.title }}</b>
        <span class="grow"></span>

        <span v-if="state.meta?.demo_mode && clockInfo && can('dispatch:manage', 'alert:handle', 'offer:respond')" class="row small" title="演示时钟：快进后立刻运行超时扫描，不用真等">
          <span class="muted num">演示时钟 {{ dt(clockInfo.now) }}</span>
          <button class="btn sm" :disabled="jumping" @click="advance(31)">+31 分钟</button>
          <button class="btn sm" :disabled="jumping" @click="advance(60 * 49)">+49 小时</button>
          <button v-if="clockInfo.offset_minutes" class="btn sm ghost" @click="resetClock">还原</button>
        </span>

        <button class="btn sm" :aria-pressed="state.elderMode" @click="setElderMode(!state.elderMode)">
          {{ state.elderMode ? '标准字体' : '大字模式' }}
        </button>
        <button class="btn sm" @click="readAloud">朗读本页</button>

        <span class="bell">
          <button class="btn sm" @click="showNotes = !showNotes">消息</button>
          <span v-if="notes.unread" class="badge">{{ notes.unread > 99 ? '99+' : notes.unread }}</span>
        </span>
        <div v-if="showNotes" class="drawer">
          <div class="row between" style="padding: 10px 14px; border-bottom: 1px solid var(--line)">
            <b>消息通知</b>
            <button class="btn sm ghost" @click="readAll">全部已读</button>
          </div>
          <div v-if="!notes.items.length" class="empty">暂无消息</div>
          <div v-for="n in notes.items" :key="n.id" class="note-item" :class="{ unread: !n.read_at }" @click="openNote(n)">
            <div class="row between">
              <b class="small" :class="`level-${n.level}`">{{ n.title }}</b>
              <span class="muted small nowrap">{{ dt(n.created_at) }}</span>
            </div>
            <div class="small" style="color: var(--ink-2)">{{ n.body }}</div>
          </div>
        </div>

        <span class="small">{{ state.user.display_name }}<span class="muted">（{{ state.user.role_name }}<template v-if="state.user.grid_name"> · {{ state.user.grid_name }}</template><template v-else-if="state.user.community_name"> · {{ state.user.community_name }}</template><template v-else-if="state.user.org_name"> · {{ state.user.org_name }}</template>）</span></span>
        <button class="btn sm ghost" @click="signOut">退出</button>
      </header>
      <main class="content">
        <router-view :key="route.fullPath" />
      </main>
    </div>
  </div>
</template>
