<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { can, run, toast } from '../store'
import { dt, levelColor, statusColor } from '../utils'

const router = useRouter()
const data = reactive({ accept: [], dispatch: [], alerts: [], exceptions: [], spot: [], risk: [], followups: [] })
const accepting = ref(null)
const acceptNote = ref('')

async function load() {
  const [accept, dispatch, alerts, exceptions, spot, risk] = await Promise.all([
    api.get('/orders', { status: 'PENDING_ACCEPT', sort: 'priority', size: 10 }),
    api.get('/orders', { status: 'PENDING_DISPATCH', sort: 'priority', size: 10 }),
    api.get('/alerts', { only_open: true, size: 8 }),
    api.get('/exceptions', { size: 30 }),
    api.get('/spot-checks'),
    api.get('/ai/top-risk', { limit: 6 }),
  ])
  Object.assign(data, {
    accept: accept.items, dispatch: dispatch.items, alerts: alerts.items,
    exceptions: exceptions.items.filter((e) => e.status !== 'CLOSED'), spot: spot.items, risk,
  })
  if (can('followup:handle')) data.followups = await api.get('/followups', { status: 'OPEN' })
}

function startAccept(order) {
  accepting.value = order
  acceptNote.value = ''
}

async function confirmAccept() {
  const order = accepting.value
  if (order.needs_manual && !acceptNote.value.trim()) {
    toast('该预约未通过校验，人工受理必须填写依据', 'error')
    return
  }
  if (await run(() => api.post(`/orders/${order.id}/accept`, { note: acceptNote.value || null }), '已受理，进入待派单')) {
    accepting.value = null
    load()
  }
}

const refresh = () => load()
onMounted(() => {
  load()
  window.addEventListener('yihu:refresh', refresh)
})
onUnmounted(() => window.removeEventListener('yihu:refresh', refresh))
</script>

<template>
  <div class="col">
    <div class="grid g4">
      <div class="card kpi"><span class="label">待受理预约</span><span class="value">{{ data.accept.length }}</span></div>
      <div class="card kpi"><span class="label">待派单工单</span><span class="value">{{ data.dispatch.length }}</span></div>
      <div class="card kpi"><span class="label">未闭环预警</span><span class="value" :class="{ 'level-RED': data.alerts.some((a) => a.level === 'RED') }">{{ data.alerts.length }}</span></div>
      <div class="card kpi"><span class="label">待处置异常 / 时长抽查</span><span class="value">{{ data.exceptions.length }} / {{ data.spot.length }}</span></div>
    </div>

    <div class="grid g2">
      <section class="card">
        <div class="card-head"><h2 class="grow">待受理预约</h2><router-link class="small" to="/orders?status=PENDING_ACCEPT">全部</router-link></div>
        <div v-if="!data.accept.length" class="empty">没有待受理的预约</div>
        <div v-for="o in data.accept" :key="o.id" class="row between" style="padding: 8px 0; border-bottom: 1px solid var(--line-2)">
          <div class="grow">
            <router-link :to="`/orders/${o.id}`"><b>{{ o.elder_name }}</b></router-link> {{ o.item_name }} <span class="muted num">{{ dt(o.scheduled_start) }}</span>
            <div class="tags"><span class="tag">{{ o.disability_label }}</span><span v-for="t in o.care_tags" :key="t" class="tag">{{ t }}</span><span v-if="o.needs_manual" class="tag red">需人工受理</span></div>
            <div v-for="i in o.validation_issues" :key="i.code" class="small" style="color: var(--red)">{{ i.message }}</div>
          </div>
          <button class="btn sm primary" @click="startAccept(o)">受理</button>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><h2 class="grow">待派单（按优先级）</h2><router-link class="small" to="/orders?status=PENDING_DISPATCH">全部</router-link></div>
        <div v-if="!data.dispatch.length" class="empty">没有待派单的工单</div>
        <div v-for="o in data.dispatch" :key="o.id" class="row between" style="padding: 8px 0; border-bottom: 1px solid var(--line-2)">
          <div class="grow">
            <span class="tag pine num" title="优先级：失能等级、独居等综合">P{{ o.priority }}</span>
            <b> {{ o.elder_name }}</b> {{ o.item_name }} <span class="muted num">{{ dt(o.scheduled_start) }}</span>
            <span v-if="o.manual_coordination" class="tag red">需人工协调</span>
          </div>
          <button class="btn sm primary" @click="router.push(`/dispatch/${o.id}`)">派单</button>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><h2 class="grow">预警（红色置顶）</h2><router-link class="small" to="/alerts">预警看板</router-link></div>
        <div v-if="!data.alerts.length" class="empty">没有未闭环预警</div>
        <div v-for="a in data.alerts" :key="a.id" class="alert-card mb" :class="a.level">
          <div class="row between">
            <b><span class="tag" :class="levelColor(a.level)">{{ a.level_label }}</span> {{ a.elder_name }}：{{ a.title }}</b>
            <span v-if="a.open_minutes !== null" class="small num" :class="{ 'level-RED': a.level === 'RED' && a.open_minutes >= 20 }">已 {{ a.open_minutes }} 分钟</span>
          </div>
          <div class="small muted">{{ a.message }}</div>
          <div v-if="a.escalated_at" class="small level-RED">已升级</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><h2 class="grow">优先巡访参考</h2><span class="tag pine">系统生成</span></div>
        <div v-for="p in data.risk" :key="p.elder_id" class="row between" style="padding: 6px 0; border-bottom: 1px solid var(--line-2)">
          <router-link class="grow" :to="`/elders/${p.elder_id}`">{{ p.elder_name }}</router-link>
          <span class="small muted" style="max-width: 60%">{{ p.factors.slice(0, 3).map((f) => f.name + (f.detail ? '：' + f.detail : '')).join('；') }}</span>
          <b class="num" :class="{ 'level-RED': p.tier === '高' }" style="width: 34px; text-align: right">{{ p.score }}</b>
        </div>
        <div class="small muted mt">依据能力等级、标签、年龄、慢病和预警记录加权，不是医学诊断。</div>
      </section>

      <section class="card">
        <div class="card-head"><h2 class="grow">服务异常</h2><router-link class="small" to="/exceptions">处置队列</router-link></div>
        <div v-if="!data.exceptions.length" class="empty">没有待处置异常</div>
        <div v-for="e in data.exceptions" :key="e.id" class="row between" style="padding: 6px 0">
          <span class="grow"><span class="tag" :class="statusColor(e.status)">{{ e.type_label }}</span> {{ e.elder_name }} · {{ e.description }}</span>
          <span class="small muted num">{{ e.open_minutes }} 分钟</span>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><h2 class="grow">时长异常待抽查</h2></div>
        <div v-if="!data.spot.length" class="empty">没有待抽查工单</div>
        <div v-for="s in data.spot" :key="s.order_id" class="row between" style="padding: 6px 0">
          <router-link class="grow" :to="`/orders/${s.order_id}`">{{ s.order_no }} {{ s.item_name }}</router-link>
          <span class="small num">实际 {{ s.duration_minutes }} / 标准 {{ s.standard_minutes }} 分钟</span>
        </div>
      </section>
    </div>

    <Modal v-if="accepting" title="受理预约" @close="accepting = null">
      <p><b>{{ accepting.elder_name }}</b>，{{ accepting.item_name }}，{{ dt(accepting.scheduled_start) }}</p>
      <div v-if="accepting.needs_manual" class="notice warn mb">
        <div v-for="i in accepting.validation_issues" :key="i.code">{{ i.message }}</div>
      </div>
      <label class="field"><span>受理依据<i v-if="accepting.needs_manual">*</i></span><textarea v-model="acceptNote" placeholder="校验未通过时，请写明人工核实情况"></textarea></label>
      <template #foot><button class="btn" @click="accepting = null">取消</button><button class="btn primary" @click="confirmAccept">确认受理</button></template>
    </Modal>
  </div>
</template>
