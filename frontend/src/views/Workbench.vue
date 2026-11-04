<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { can, run, state, toast } from '../store'
import { dt, levelColor, serverNow } from '../utils'

const router = useRouter()
const data = reactive({ accept: [], dispatch: [], alerts: [], exceptions: [], spot: [], risk: [], followups: [], loaded: false })
const accepting = ref(null)
const acceptNote = ref('')

const greeting = computed(() => {
  const hour = serverNow().getHours()
  const word = hour < 6 ? '夜里好' : hour < 11 ? '早上好' : hour < 13 ? '中午好' : hour < 18 ? '下午好' : '晚上好'
  return `${state.user.display_name}，${word}`
})
const redAlerts = computed(() => data.alerts.filter((a) => a.level === 'RED'))
const otherAlerts = computed(() => data.alerts.filter((a) => a.level !== 'RED'))
const nothingToDo = computed(() => data.loaded && !data.accept.length && !data.dispatch.length && !redAlerts.value.length && !data.exceptions.length)

async function load() {
  const [accept, dispatch, alerts, exceptions, spot, risk] = await Promise.all([
    api.get('/orders', { status: 'PENDING_ACCEPT', sort: 'priority', size: 10 }),
    api.get('/orders', { status: 'PENDING_DISPATCH', sort: 'priority', size: 10 }),
    api.get('/alerts', { only_open: true, size: 12 }),
    api.get('/exceptions', { size: 30 }),
    api.get('/spot-checks'),
    api.get('/ai/top-risk', { limit: 5 }),
  ])
  Object.assign(data, {
    accept: accept.items, dispatch: dispatch.items, alerts: alerts.items,
    exceptions: exceptions.items.filter((e) => e.status !== 'CLOSED'), spot: spot.items, risk, loaded: true,
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
    toast('这张预约没有通过校验，请写明人工核实的情况', 'error')
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
  <div>
    <header class="handover-head">
      <h1>{{ greeting }}</h1>
      <p v-if="nothingToDo">今天的工单都处理完了，可以看看右边的巡访参考。</p>
      <p v-else-if="data.loaded">
        还有 <a href="#accept">{{ data.accept.length }} 张预约</a>等你受理，<a href="#dispatch">{{ data.dispatch.length }} 张工单</a>等你派单<template v-if="data.alerts.length">，<router-link to="/alerts">{{ data.alerts.length }} 条预警</router-link>没有闭环</template><template v-if="data.exceptions.length">，<router-link to="/exceptions">{{ data.exceptions.length }} 个服务异常</router-link>待处置</template>。
      </p>
    </header>

    <section v-if="redAlerts.length" class="urgent" aria-label="红色预警">
      <div v-for="a in redAlerts" :key="a.id" class="urgent-row">
        <span class="tag red">红色预警</span>
        <span class="grow"><b>{{ a.elder_name }}</b>，{{ a.message }}</span>
        <span class="small num" :class="{ 'level-RED': a.open_minutes >= 20 }">
          <template v-if="a.open_minutes !== null">已等待 {{ a.open_minutes }} 分钟</template>
          <template v-else>{{ a.status_label }}</template>
          <template v-if="a.escalated_at">，已升级到街道</template>
        </span>
        <router-link class="btn sm danger" to="/alerts">去处置</router-link>
      </div>
    </section>

    <div class="handover">
      <div>
        <section id="accept">
          <h2 class="mb">待受理预约</h2>
          <div v-if="!data.accept.length" class="muted">没有待受理的预约。</div>
          <div v-for="o in data.accept" :key="o.id" class="task row between">
            <div class="grow task-title">
              <router-link :to="`/orders/${o.id}`"><b>{{ o.elder_name }}</b></router-link> {{ o.item_name }}
              <span class="muted num">{{ dt(o.scheduled_start) }}</span>
              <div class="small muted">{{ o.disability_label }}<template v-for="t in o.care_tags" :key="t">，{{ t }}</template></div>
              <div v-for="i in o.validation_issues" :key="i.code" class="small" style="color: var(--red)">{{ i.message }}</div>
            </div>
            <button class="btn sm primary" @click="startAccept(o)">{{ o.needs_manual ? '人工受理' : '受理' }}</button>
          </div>
        </section>

        <section id="dispatch">
          <h2 class="mb">待派单</h2>
          <div v-if="!data.dispatch.length" class="muted">没有待派单的工单。</div>
          <div v-for="o in data.dispatch" :key="o.id" class="task row between">
            <div class="grow task-title">
              <b>{{ o.elder_name }}</b> {{ o.item_name }}
              <span class="muted num">{{ dt(o.scheduled_start) }}</span>
              <span v-if="o.manual_coordination" class="tag red" style="margin-left: 8px">候选已用尽</span>
              <div class="small muted">优先级 {{ o.priority }}，{{ o.disability_label }}<template v-for="t in o.care_tags" :key="t">，{{ t }}</template></div>
            </div>
            <button class="btn sm primary" @click="router.push(`/dispatch/${o.id}`)">派单</button>
          </div>
        </section>
      </div>

      <aside>
        <div v-if="otherAlerts.length" class="aside-block">
          <div class="card-head"><h3 class="grow">其他预警</h3><router-link class="small" to="/alerts">看全部</router-link></div>
          <div v-for="a in otherAlerts.slice(0, 4)" :key="a.id" class="mb">
            <span class="tag" :class="levelColor(a.level)">{{ a.level_label }}</span>
            <b> {{ a.elder_name }}</b>
            <div class="small muted">{{ a.title }}，已等待 {{ a.open_minutes ?? 0 }} 分钟</div>
          </div>
        </div>

        <div v-if="data.exceptions.length" class="aside-block">
          <div class="card-head"><h3 class="grow">服务异常</h3><router-link class="small" to="/exceptions">去处置</router-link></div>
          <div v-for="e in data.exceptions.slice(0, 3)" :key="e.id" class="mb">
            <b>{{ e.elder_name }}</b>，{{ e.type_label }}
            <div class="small muted">{{ e.description }}，已等待 {{ e.open_minutes }} 分钟</div>
          </div>
        </div>

        <div v-if="data.spot.length" class="aside-block">
          <h3 class="mb">时长待抽查</h3>
          <div v-for="s in data.spot" :key="s.order_id" class="mb">
            <router-link :to="`/orders/${s.order_id}`">{{ s.elder_name }} {{ s.item_name }}</router-link>
            <div class="small muted num">实际 {{ s.duration_minutes }} 分钟，标准 {{ s.standard_minutes }} 分钟</div>
          </div>
        </div>

        <div class="aside-block">
          <h3 class="mb">建议优先巡访</h3>
          <div v-for="p in data.risk" :key="p.elder_id" class="mb">
            <div class="row between">
              <router-link :to="`/elders/${p.elder_id}`">{{ p.elder_name }}</router-link>
              <b class="num" :class="{ 'level-RED': p.tier === '高' }">{{ p.score }}</b>
            </div>
            <div class="small muted">{{ p.factors.slice(0, 2).map((f) => f.detail || f.name).join('，') }}</div>
          </div>
          <div class="small muted">按能力等级、照护标签、年龄、慢性病和预警记录排序，不是医学诊断。</div>
        </div>
      </aside>
    </div>

    <Modal v-if="accepting" title="受理预约" @close="accepting = null">
      <p style="margin-top: 0"><b>{{ accepting.elder_name }}</b>，{{ accepting.item_name }}，{{ dt(accepting.scheduled_start) }}</p>
      <div v-if="accepting.needs_manual" class="notice warn mb">
        <div v-for="i in accepting.validation_issues" :key="i.code">{{ i.message }}</div>
      </div>
      <label class="field"><span>受理依据<i v-if="accepting.needs_manual">*</i></span><textarea v-model="acceptNote" :placeholder="accepting.needs_manual ? '写明人工核实的情况' : '选填'"></textarea></label>
      <template #foot><button class="btn" @click="accepting = null">取消</button><button class="btn primary" @click="confirmAccept">确认受理</button></template>
    </Modal>
  </div>
</template>
