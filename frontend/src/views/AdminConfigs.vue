<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { run, toast } from '../store'
import { dt, money, pct } from '../utils'

const configs = ref([])
const policies = ref([])
const edit = reactive({ target: null, text: '' })
const weights = reactive({ skill: 0.4, distance: 0.3, load: 0.2, rating: 0.1 })
const policy = reactive({ target: null, gov: 0, ltci: 0, cap: 0 })

const groups = computed(() => {
  const names = { dispatch: '智能派单', order: '预约受理', exec: '上门执行', alert: '健康预警', quality: '服务质量', billing: '费用结算', backup: '备份', audit: '审计' }
  const result = {}
  for (const c of configs.value) {
    if (c.key === 'dispatch.weights') continue
    ;(result[names[c.key.split('.')[0]] || '其他'] ||= []).push(c)
  }
  return result
})
const weightSum = computed(() => Number(weights.skill) + Number(weights.distance) + Number(weights.load) + Number(weights.rating))

async function load() {
  configs.value = await api.get('/admin/configs')
  policies.value = await api.get('/subsidy-policies')
  Object.assign(weights, configs.value.find((c) => c.key === 'dispatch.weights').value)
}

async function saveWeights() {
  const value = { skill: Number(weights.skill), distance: Number(weights.distance), load: Number(weights.load), rating: Number(weights.rating) }
  if (await run(() => api.put('/admin/configs/dispatch.weights', { value }), '权重已保存，下一次推荐立即生效')) load()
}

function start(config) {
  edit.target = config
  edit.text = typeof config.value === 'object' ? JSON.stringify(config.value, null, 2) : String(config.value)
}

async function save() {
  let value
  try {
    value = JSON.parse(edit.text)
  } catch {
    toast('请输入合法的数字或 JSON', 'error')
    return
  }
  if (await run(() => api.put(`/admin/configs/${edit.target.key}`, { value }), '已保存')) {
    edit.target = null
    load()
  }
}

function startPolicy(row) {
  Object.assign(policy, { target: row, gov: row.gov_ratio, ltci: row.ltci_ratio, cap: row.monthly_cap / 100 })
}

async function savePolicy() {
  const body = { gov_ratio: Number(policy.gov), ltci_ratio: Number(policy.ltci), monthly_cap: Math.round(Number(policy.cap) * 100) }
  if (await run(() => api.patch(`/subsidy-policies/${policy.target.id}`, body), '补贴政策已更新')) {
    policy.target = null
    load()
  }
}

const show = (value) => (typeof value === 'object' ? JSON.stringify(value) : value)
onMounted(load)
</script>

<template>
  <div class="col">
    <section class="card">
      <h2 class="mb">派单权重</h2>
      <div class="row">
        <label class="field" style="width: 120px"><span>技能匹配</span><input v-model="weights.skill" type="number" step="0.05" min="0" max="1" /></label>
        <label class="field" style="width: 120px"><span>直线距离</span><input v-model="weights.distance" type="number" step="0.05" min="0" max="1" /></label>
        <label class="field" style="width: 120px"><span>当日负荷</span><input v-model="weights.load" type="number" step="0.05" min="0" max="1" /></label>
        <label class="field" style="width: 120px"><span>近30天评分</span><input v-model="weights.rating" type="number" step="0.05" min="0" max="1" /></label>
        <span class="small" :class="Math.abs(weightSum - 1) > 1e-6 ? 'level-YELLOW' : 'muted'">合计 {{ weightSum.toFixed(2) }}<template v-if="Math.abs(weightSum - 1) > 1e-6">，计算时自动归一</template></span>
        <button class="btn primary" @click="saveWeights">保存</button>
      </div>
    </section>

    <div class="grid g2">
      <section v-for="(items, group) in groups" :key="group" class="card">
        <h2 class="mb">{{ group }}</h2>
        <table>
          <tbody>
            <tr v-for="c in items" :key="c.key">
              <td>{{ c.description }}<div class="small muted">{{ c.key }}<template v-if="c.version"> · v{{ c.version }} · {{ dt(c.updated_at) }}</template></div></td>
              <td class="num right" style="max-width: 180px; word-break: break-all"><span :class="typeof c.value === 'object' ? 'small' : ''">{{ show(c.value) }}</span></td>
              <td class="right"><button class="btn sm" @click="start(c)">修改</button></td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <section class="card">
      <h2 class="mb">补贴政策（失能等级 × 困难类型）</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>失能等级</th><th>困难类型</th><th class="right">政府补贴比例</th><th class="right">长护险比例</th><th class="right">月度补贴上限</th><th></th></tr></thead>
          <tbody>
            <tr v-for="p in policies" :key="p.id">
              <td>{{ p.disability_label }}</td><td>{{ p.difficulty_label }}</td><td class="num right">{{ pct(p.gov_ratio, 0) }}</td><td class="num right">{{ pct(p.ltci_ratio, 0) }}</td>
              <td class="num right">{{ money(p.monthly_cap) }}</td><td class="right"><button class="btn sm" @click="startPolicy(p)">修改</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="small muted mt">政府补贴比例取项目比例和政策比例的较小值；长护险只对参保老人生效；个人自付为总额减去前两项。</p>
    </section>

    <Modal v-if="edit.target" :title="edit.target.description" @close="edit.target = null">
      <label class="field"><span>{{ edit.target.key }}</span><textarea v-model="edit.text" rows="8" class="num"></textarea></label>
      <p class="small muted">默认值：{{ show(edit.target.default) }}</p>
      <template #foot><button class="btn primary" @click="save">保存</button></template>
    </Modal>

    <Modal v-if="policy.target" :title="`${policy.target.disability_label} · ${policy.target.difficulty_label}`" @close="policy.target = null">
      <div class="form-grid">
        <label class="field"><span>政府补贴比例</span><input v-model="policy.gov" type="number" step="0.05" min="0" max="1" /></label>
        <label class="field"><span>长护险比例</span><input v-model="policy.ltci" type="number" step="0.05" min="0" max="1" /></label>
        <label class="field"><span>月度补贴上限（元）</span><input v-model="policy.cap" type="number" /></label>
      </div>
      <template #foot><button class="btn primary" @click="savePolicy">保存</button></template>
    </Modal>
  </div>
</template>
