<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { run, toast } from '../store'
import { dt, statusColor } from '../utils'

const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)
const order = ref(null)
const rec = ref(null)
const history = ref({ offers: [], rounds: [] })
const caregivers = ref([])
const showExcluded = ref(false)
const reassign = reactive({ show: false, caregiver_id: null, reason: '' })

const FACTOR_COLOR = { skill: '#1f5e57', distance: '#3f9b8f', load: '#c79a3b', rating: '#8c6bb1' }
const activeOffer = computed(() => history.value.offers.find((o) => o.status === 'OFFERED'))

async function load() {
  order.value = await api.get(`/orders/${id}`)
  history.value = await api.get(`/orders/${id}/dispatch-history`)
  if (order.value.status === 'PENDING_DISPATCH' && !activeOffer.value) {
    rec.value = await run(() => api.post(`/orders/${id}/recommend`))
  } else {
    rec.value = null
  }
}

async function assign(caregiverId) {
  const body = { round_id: rec.value?.round_id, caregiver_id: caregiverId ?? null }
  const result = await run(() => api.post(`/orders/${id}/assign`, body))
  if (result) {
    toast(`已派给 ${result.caregiver_name}，请等待其在 30 分钟内接单`)
    load()
  }
}

async function openReassign() {
  caregivers.value = await api.get('/caregivers')
  reassign.show = true
}

async function doReassign() {
  if (!reassign.reason.trim() || !reassign.caregiver_id) {
    toast('改派必须选择护理员并填写原因', 'error')
    return
  }
  const result = await run(() => api.post(`/orders/${id}/reassign`, { caregiver_id: reassign.caregiver_id, reason: reassign.reason }))
  if (result) {
    toast(`已改派给 ${result.caregiver_name}`)
    reassign.show = false
    load()
  }
}

// 简易调度图：以服务地址为中心，半径圈按最大服务距离绘制
const map = computed(() => {
  if (!rec.value) return null
  const { lat, lng, max_distance_km: maxKm } = rec.value.order
  const size = 300
  const center = size / 2
  const kmToPx = (center - 22) / maxKm
  const project = (pLat, pLng) => ({
    x: center + (pLng - lng) * 111 * Math.cos((lat * Math.PI) / 180) * kmToPx,
    y: center - (pLat - lat) * 111 * kmToPx,
  })
  return {
    size, center, radius: maxKm * kmToPx,
    points: rec.value.candidates.map((c) => ({ ...project(c.lat, c.lng), rank: c.rank, name: c.name })),
  }
})

onMounted(load)
</script>

<template>
  <div v-if="order" class="col">
    <div class="page-head">
      <h1>{{ order.elder_name }} · {{ order.item_name }}</h1>
      <span class="tag" :class="statusColor(order.status)">{{ order.status_label }}</span>
      <span class="sub num">{{ dt(order.scheduled_start) }} · 优先级 P{{ order.priority }}</span>
      <span class="grow"></span>
      <button v-if="['PENDING_DISPATCH', 'DISPATCHED'].includes(order.status)" class="btn" @click="openReassign">手工改派</button>
      <button class="btn ghost" @click="router.push(`/orders/${id}`)">工单详情</button>
    </div>

    <div v-if="['PENDING_ACCEPT', 'ACCEPTED'].includes(order.status)" class="notice warn">这张工单还没有受理，受理后才能派单。请先到工作台受理。</div>
    <div v-if="['IN_SERVICE', 'COMPLETED', 'CANCELLED', 'NO_SHOW'].includes(order.status)" class="notice">这张工单已{{ order.status_label }}，不能再派单或改派。</div>
    <div v-if="order.manual_coordination" class="notice error">候选护理员已用尽，需人工协调。可手工改派或联系机构增派人手。</div>
    <div v-if="activeOffer" class="notice">
      已邀约 <b>{{ activeOffer.caregiver_name }}</b>，等待接单，时限至 <b class="num">{{ dt(activeOffer.deadline_at) }}</b>。超时后自动顺延给下一位候选。
    </div>

    <template v-if="rec">
      <div class="row between">
        <h2>推荐候选（前 {{ rec.candidates.length }} 名）</h2>
        <span class="small muted">
          权重：技能 {{ rec.weights.skill }} · 距离 {{ rec.weights.distance }} · 负荷 {{ rec.weights.load }} · 评分 {{ rec.weights.rating }}
          <template v-if="rec.backup_count">，另有 {{ rec.backup_count }} 名备选用于超时顺延</template>
        </span>
      </div>
      <div class="grid" style="grid-template-columns: minmax(0, 1fr) 320px">
        <div class="grid g3" style="align-items: start">
          <div v-if="!rec.candidates.length" class="card empty" style="grid-column: 1 / -1">没有符合条件的护理员，请查看过滤原因或手工改派。</div>
          <article v-for="c in rec.candidates" :key="c.caregiver_id" class="candidate" :class="{ first: c.rank === 1 }">
            <div class="row between">
              <span class="row"><span class="rank">{{ c.rank }}</span><b>{{ c.name }}</b></span>
              <b class="num" style="font-size: 1.3rem">{{ (c.score * 100).toFixed(1) }}</b>
            </div>
            <div class="score-bar" :title="`综合得分 ${c.score}`">
              <i v-for="(f, key) in c.factors" :key="key" :style="{ width: `${f.contribution * 100}%`, background: FACTOR_COLOR[key] }"></i>
            </div>
            <div>
              <div v-for="(f, key) in c.factors" :key="key" class="factor">
                <div class="row between">
                  <span><span class="dot" :style="{ background: FACTOR_COLOR[key] }"></span>{{ f.label }}</span>
                  <span class="num small" title="归一值 × 权重 = 贡献分">{{ f.normalized.toFixed(2) }} × {{ f.weight }} = {{ f.contribution.toFixed(3) }}</span>
                </div>
                <div class="small muted">{{ f.raw }}</div>
              </div>
            </div>
            <div v-if="c.factors.skill.missing?.length" class="small" style="color: var(--yellow)">缺少技能：{{ c.factors.skill.missing.join('、') }}</div>
            <div v-if="c.key_attention" class="small" style="color: var(--red)">重点关注对象，降权 ×{{ c.penalty_factor }}（原始分 {{ c.raw_score }}）</div>
            <button class="btn" :class="{ primary: c.rank === 1 }" @click="assign(c.caregiver_id)">{{ c.rank === 1 ? '一键派单' : '派给 TA' }}</button>
          </article>
        </div>
        <div class="card">
          <h3 class="mb">位置示意</h3>
          <svg v-if="map" :viewBox="`0 0 ${map.size} ${map.size}`" width="100%" role="img" aria-label="候选护理员与服务地址的相对位置">
            <circle :cx="map.center" :cy="map.center" :r="map.radius" fill="#eef5f2" stroke="#2f8077" stroke-dasharray="5 4" />
            <circle :cx="map.center" :cy="map.center" :r="map.radius / 3" fill="none" stroke="#cfe0da" />
            <circle :cx="map.center" :cy="map.center" :r="(map.radius * 2) / 3" fill="none" stroke="#cfe0da" />
            <line v-for="p in map.points" :key="`l${p.rank}`" :x1="map.center" :y1="map.center" :x2="p.x" :y2="p.y" stroke="#9fbab3" />
            <g v-for="p in map.points" :key="p.rank">
              <circle :cx="p.x" :cy="p.y" r="11" fill="#1f5e57" />
              <text :x="p.x" :y="p.y + 4" text-anchor="middle" fill="#fff" font-size="12" font-weight="600">{{ p.rank }}</text>
            </g>
            <rect :x="map.center - 6" :y="map.center - 6" width="12" height="12" rx="3" fill="#c0352b" stroke="#fff" stroke-width="1.5" />
            <text :x="map.center" :y="map.center - map.radius - 4" text-anchor="middle" font-size="11" fill="#7c8885">{{ rec.order.max_distance_km }} 公里</text>
          </svg>
          <div class="small muted mt">红色方块为服务地址，数字为候选名次。</div>
        </div>
      </div>
      <section class="card">
        <button class="btn sm ghost" @click="showExcluded = !showExcluded">{{ showExcluded ? '收起' : '查看' }}被过滤的护理员（{{ rec.excluded.length }}）</button>
        <div v-if="showExcluded" class="table-wrap mt">
          <table><tbody>
            <tr v-for="e in rec.excluded" :key="e.caregiver_id"><td>{{ e.name }}</td><td class="muted">{{ e.reason }}</td></tr>
          </tbody></table>
        </div>
      </section>
    </template>

    <section v-if="history.offers.length" class="card">
      <h2 class="mb">派单记录</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>护理员</th><th>名次</th><th>推荐分</th><th>状态</th><th>发出时间</th><th>时限</th><th>响应</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="o in history.offers" :key="o.id">
              <td>{{ o.caregiver_name }}</td><td class="num">{{ o.rank ?? '手工' }}</td><td class="num">{{ o.score ?? '—' }}</td>
              <td><span class="tag">{{ o.status_label }}</span></td>
              <td class="num">{{ dt(o.offered_at) }}</td><td class="num">{{ dt(o.deadline_at) }}</td><td class="num">{{ dt(o.responded_at) }}</td>
              <td class="small">{{ o.reject_reason || o.reassign_reason || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <Modal v-if="reassign.show" title="手工改派" @close="reassign.show = false">
      <div class="col">
        <label class="field"><span>改派给<i>*</i></span>
          <select v-model.number="reassign.caregiver_id">
            <option :value="null">请选择</option>
            <option v-for="c in caregivers" :key="c.id" :value="c.id">{{ c.name }}（{{ c.org_name }}）</option>
          </select>
        </label>
        <label class="field"><span>改派原因<i>*</i></span><textarea v-model="reassign.reason" placeholder="原因写入审计日志"></textarea></label>
        <div class="small muted">系统会做时段冲突检测，冲突时拦截。</div>
      </div>
      <template #foot><button class="btn" @click="reassign.show = false">取消</button><button class="btn primary" @click="doReassign">确认改派</button></template>
    </Modal>
  </div>
</template>
