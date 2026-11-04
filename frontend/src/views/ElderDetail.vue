<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import ElderForm from '../components/ElderForm.vue'
import HealthPanel from '../components/HealthPanel.vue'
import Modal from '../components/Modal.vue'
import { can, run, toast } from '../store'
import { dateOnly, dt, statusColor } from '../utils'

const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)
const elder = ref(null)
const logs = ref([])
const accessLogs = ref([])
const orders = ref([])
const grids = ref([])
const tab = ref('profile')
const editing = ref(false)
const revealed = reactive({})
const reveal = reactive({ show: false, password: '', reason: '', fields: ['phone', 'id_card', 'address', 'emergency_phone'] })
const remove = reactive({ show: false, reason: '' })
const followup = reactive({ show: false, systolic: '', diastolic: '', glucose: '', heart: '', spo2: '', note: '' })

const FIELD_NAMES = {
  name: '姓名', gender: '性别', birth_date: '出生日期', id_card: '身份证号', phone: '联系电话', address: '居住地址', lat: '纬度', lng: '经度',
  grid_id: '网格', emergency_name: '紧急联系人', emergency_relation: '关系', emergency_phone: '紧急联系电话', medical_insurance: '医保类型',
  ltc_insurance: '长护险类型', chronic_diseases: '慢性病', medications: '用药清单', allergies: '过敏史', disability_level: '失能等级',
  difficulty_type: '困难类型', care_tags: '照护标签', doctor_id: '签约医生', __create__: '创建档案', __delete__: '删除档案', __restore__: '恢复档案',
}

const sensitive = (key) => revealed[key] ?? elder.value?.[key]

async function load() {
  elder.value = await api.get(`/elders/${id}`)
  if (can('order:read')) orders.value = (await api.get('/orders', { elder_id: id, size: 10 })).items
  if (can('elder:changelog')) {
    logs.value = await api.get(`/elders/${id}/change-logs`)
    accessLogs.value = await api.get(`/elders/${id}/access-logs`)
  }
}

async function doReveal() {
  const result = await run(() => api.post(`/elders/${id}/reveal`, { password: reveal.password, reason: reveal.reason, fields: reveal.fields }))
  if (result) {
    Object.assign(revealed, result)
    reveal.show = false
    reveal.password = ''
    toast('已显示完整信息，本次查看已留痕', 'info', 4000)
    if (can('elder:changelog')) accessLogs.value = await api.get(`/elders/${id}/access-logs`)
  }
}

async function save(payload) {
  if (await run(() => api.patch(`/elders/${id}`, payload), '档案已更新，变更已写入日志')) {
    editing.value = false
    load()
  }
}

async function doDelete() {
  if (await run(() => api.del(`/elders/${id}`, { reason: remove.reason }), '档案已删除（逻辑删除，可恢复）')) router.push('/elders')
}

async function saveFollowup() {
  const map = { systolic: 'SYSTOLIC', diastolic: 'DIASTOLIC', glucose: 'GLUCOSE', heart: 'HEART_RATE', spo2: 'SPO2' }
  const points = Object.entries(map).filter(([k]) => followup[k] !== '').map(([k, metric]) => ({ metric, value: Number(followup[k]) }))
  if (!points.length) {
    toast('请至少录入一项指标', 'error')
    return
  }
  const result = await run(() => api.post(`/elders/${id}/health`, { points, note: followup.note || null }))
  if (result) {
    const errors = result.errors.map((e) => e.reason).join('；')
    if (errors) toast(errors, 'error', 5000)
    else if (result.alerts.length) toast(`已保存，触发 ${result.alerts.length} 条预警`, 'error', 5000)
    else toast('已保存')
    Object.assign(followup, { show: false, systolic: '', diastolic: '', glucose: '', heart: '', spo2: '', note: '' })
    tab.value = 'orders'
    setTimeout(() => { tab.value = 'health' }, 0)
  }
}

onMounted(async () => {
  load()
  const tree = await api.get('/org/tree')
  grids.value = tree.communities.flatMap((c) => c.grids.map((g) => ({ ...g, community: c.name })))
})
</script>

<template>
  <div v-if="elder" class="col">
    <div class="page-head">
      <h1>{{ elder.name }}</h1>
      <span class="sub">{{ elder.gender }} · {{ elder.age }} 岁 · {{ elder.code }} · {{ elder.community_name }} {{ elder.grid_name }}</span>
      <span class="tag" :class="{ red: elder.disability_level === 'SEVERE', yellow: elder.disability_level === 'MODERATE' }">{{ elder.disability_label }}</span>
      <span v-for="t in elder.care_tags" :key="t" class="tag">{{ t }}</span>
      <span class="grow"></span>
      <button v-if="can('elder:reveal')" class="btn" @click="reveal.show = true">查看完整信息</button>
      <button v-if="can('health:write')" class="btn" @click="followup.show = true">录入随访</button>
      <button v-if="can('elder:write')" class="btn" @click="editing = true">编辑</button>
      <button v-if="can('elder:delete')" class="btn" @click="remove.show = true">删除</button>
    </div>

    <div class="row">
      <button v-for="[k, t] in [['profile', '档案'], ['health', '健康'], ['orders', '服务记录'], ['logs', '变更与查看日志']]" :key="k" class="btn" :class="{ primary: tab === k }" @click="tab = k">{{ t }}</button>
    </div>

    <div v-if="tab === 'profile'" class="grid g2">
      <section class="card">
        <h2 class="mb">基本信息</h2>
        <dl class="kv">
          <dt>出生日期</dt><dd class="num">{{ dateOnly(elder.birth_date) }}</dd>
          <dt>身份证号</dt><dd class="num">{{ sensitive('id_card') }}</dd>
          <dt>联系电话</dt><dd class="num">{{ sensitive('phone') }}</dd>
          <dt>居住地址</dt><dd>{{ sensitive('address') }}</dd>
          <dt>紧急联系人</dt><dd>{{ elder.emergency_name }}（{{ elder.emergency_relation }}）<span class="num">{{ sensitive('emergency_phone') }}</span></dd>
          <dt>签约医生</dt><dd>{{ elder.doctor_name || '—' }}</dd>
        </dl>
        <div v-if="Object.keys(revealed).length" class="notice warn mt small">完整信息已显示，本次查看已写入审计日志。离开页面后恢复脱敏。</div>
      </section>
      <section class="card">
        <h2 class="mb">医保与健康档案</h2>
        <dl class="kv">
          <dt>医保类型</dt><dd>{{ elder.medical_insurance || '—' }}</dd>
          <dt>长期护理保险</dt><dd>{{ elder.ltc_insurance || '—' }}</dd>
          <dt>困难类型</dt><dd>{{ elder.difficulty_label }}</dd>
          <dt>慢性病</dt><dd><span v-for="d in elder.chronic_diseases" :key="d" class="tag">{{ d }}</span><span v-if="!elder.chronic_diseases.length" class="muted">无</span></dd>
          <dt>用药清单</dt><dd><div v-for="m in elder.medications" :key="m" class="small">{{ m }}</div><span v-if="!elder.medications.length" class="muted">无</span></dd>
          <dt>过敏史</dt><dd>{{ elder.allergies || '无' }}</dd>
        </dl>
      </section>
    </div>

    <HealthPanel v-if="tab === 'health'" :elder-id="id" />

    <section v-if="tab === 'orders'" class="card">
      <div class="table-wrap">
        <table>
          <thead><tr><th>工单号</th><th>服务</th><th>时间</th><th>状态</th><th>护理员</th></tr></thead>
          <tbody>
            <tr v-for="o in orders" :key="o.id">
              <td class="num small"><router-link :to="`/orders/${o.id}`">{{ o.order_no }}</router-link></td>
              <td>{{ o.item_name }}</td><td class="num">{{ dt(o.scheduled_start) }}</td>
              <td><span class="tag" :class="statusColor(o.status)">{{ o.status_label }}</span></td><td>{{ o.caregiver_name || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!orders.length" class="empty">没有服务记录</div>
      </div>
    </section>

    <div v-if="tab === 'logs'" class="grid g2">
      <section class="card">
        <h2 class="mb">档案变更日志</h2>
        <div v-if="!logs.length" class="empty">没有变更记录</div>
        <ul class="timeline">
          <li v-for="l in logs" :key="l.id">
            <div class="when num">{{ dt(l.changed_at) }} · {{ l.changed_by }}</div>
            <div><b>{{ FIELD_NAMES[l.field] || l.field }}</b> <span class="small"><template v-if="l.before">{{ l.before }} → </template>{{ l.after }}</span></div>
          </li>
        </ul>
      </section>
      <section class="card">
        <h2 class="mb">敏感信息查看记录</h2>
        <div v-if="!accessLogs.length" class="empty">没有查看记录</div>
        <ul class="timeline">
          <li v-for="l in accessLogs" :key="l.id">
            <div class="when num">{{ dt(l.accessed_at) }} · {{ l.user_name }} · {{ l.ip }}</div>
            <div class="small">{{ l.fields.map((f) => FIELD_NAMES[f]).join('、') }}。理由：{{ l.reason }}</div>
          </li>
        </ul>
      </section>
    </div>

    <Modal v-if="reveal.show" title="查看完整敏感信息（二次授权）" @close="reveal.show = false">
      <div class="col">
        <div class="notice warn">依据《个人信息保护法》，查看身份证号、手机号、详细住址须二次授权，操作人、时间和理由全程留痕。</div>
        <label class="field"><span>查看理由<i>*</i></span><input v-model="reveal.reason" placeholder="比如：联系紧急联系人" /></label>
        <label class="field"><span>本人登录密码<i>*</i></span><input v-model="reveal.password" type="password" autocomplete="current-password" /></label>
      </div>
      <template #foot><button class="btn" @click="reveal.show = false">取消</button><button class="btn primary" @click="doReveal">确认查看</button></template>
    </Modal>

    <Modal v-if="editing" title="编辑档案" wide @close="editing = false">
      <ElderForm :grids="grids" :initial="elder" @submit="save" @cancel="editing = false" />
    </Modal>

    <Modal v-if="remove.show" title="删除档案" @close="remove.show = false">
      <p class="small muted">逻辑删除：档案隐藏但不销毁，历史工单和账单仍可追溯。</p>
      <label class="field"><span>删除原因<i>*</i></span><input v-model="remove.reason" placeholder="比如：搬离本社区" /></label>
      <template #foot><button class="btn" @click="remove.show = false">取消</button><button class="btn danger" :disabled="!remove.reason.trim()" @click="doDelete">确认删除</button></template>
    </Modal>

    <Modal v-if="followup.show" title="上门随访录入" @close="followup.show = false">
      <div class="form-grid">
        <label class="field"><span>收缩压 mmHg</span><input v-model="followup.systolic" type="number" /></label>
        <label class="field"><span>舒张压 mmHg</span><input v-model="followup.diastolic" type="number" /></label>
        <label class="field"><span>血糖 mmol/L</span><input v-model="followup.glucose" type="number" step="0.1" /></label>
        <label class="field"><span>心率 次/分</span><input v-model="followup.heart" type="number" /></label>
        <label class="field"><span>血氧 %</span><input v-model="followup.spo2" type="number" /></label>
        <label class="field"><span>备注</span><input v-model="followup.note" /></label>
      </div>
      <template #foot><button class="btn" @click="followup.show = false">取消</button><button class="btn primary" @click="saveFollowup">保存</button></template>
    </Modal>
  </div>
</template>
