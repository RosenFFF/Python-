<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { can, run, state, toast } from '../store'
import { dt, label, money, serverNow, toLocalInput } from '../utils'

const items = ref([])
const editable = can('catalog:write')
const price = reactive({ target: null, yuan: '', ratio: '', effective_from: '', reason: '' })
const versions = ref(null)
const creating = reactive({ show: false, code: '', name: '', pricing_mode: 'PER_TIME', standard_minutes: 60, skills: [], levels: ['INTACT', 'MILD', 'MODERATE', 'SEVERE'], subsidy_eligible: true, ratio: 0.5, yuan: '', checklist: '' })

async function load() { items.value = await api.get('/catalog/items') }

async function toggleShelf(item) {
  if (await run(() => api.post(`/catalog/items/${item.id}/shelf`, { on_shelf: !item.is_on_shelf }), item.is_on_shelf ? '已下架' : '已上架')) load()
}

function startPrice(item) {
  const start = new Date(serverNow().getTime() + 864e5)
  start.setHours(0, 0, 0, 0)
  Object.assign(price, { target: item, yuan: (item.unit_price / 100).toFixed(2), ratio: item.subsidy_ratio, effective_from: toLocalInput(start), reason: '' })
}

async function savePrice() {
  if (!price.reason.trim()) {
    toast('调价必须填写原因', 'error')
    return
  }
  const body = { unit_price: Math.round(Number(price.yuan) * 100), subsidy_ratio: Number(price.ratio), effective_from: `${price.effective_from}:00`, reason: price.reason }
  if (await run(() => api.post(`/catalog/items/${price.target.id}/price`, body), '价格已调整，新版本到期生效，历史工单不受影响')) {
    price.target = null
    load()
  }
}

async function create() {
  const body = {
    code: creating.code, name: creating.name, pricing_mode: creating.pricing_mode, standard_minutes: Number(creating.standard_minutes),
    required_skills: creating.skills, applicable_levels: creating.levels, subsidy_eligible: creating.subsidy_eligible,
    checklist: creating.checklist.split(/[、,，]/).map((s) => s.trim()).filter(Boolean), unit_price: Math.round(Number(creating.yuan) * 100), subsidy_ratio: Number(creating.ratio),
  }
  if (await run(() => api.post('/catalog/items', body), '服务项目已创建')) {
    creating.show = false
    load()
  }
}

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="filters">
      <span class="grow"></span>
      <button v-if="editable" class="btn primary" @click="creating.show = true">新建项目</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>编码</th><th>名称</th><th>计价</th><th class="right">当前单价</th><th class="right">标准时长</th><th>所需技能</th><th>适用等级</th><th>补贴</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="i in items" :key="i.id">
            <td class="num small">{{ i.code }}</td><td><b>{{ i.name }}</b></td><td class="small">{{ i.pricing_label }}</td>
            <td class="num right">{{ money(i.unit_price) }} <span class="small muted">v{{ i.current_version }}</span></td>
            <td class="num right">{{ i.standard_minutes }} 分钟</td>
            <td><span class="tags"><span v-for="s in i.required_skills" :key="s" class="tag">{{ s }}</span></span></td>
            <td class="small">{{ i.applicable_levels.map((l) => label('DisabilityLevel', l).replace('失能', '')).join(' ') }}</td>
            <td class="small">{{ i.subsidy_eligible ? `纳入 ${(i.subsidy_ratio * 100).toFixed(0)}%` : '不纳入' }}</td>
            <td><span class="tag" :class="i.is_on_shelf ? 'green' : ''">{{ i.is_on_shelf ? '上架' : '下架' }}</span></td>
            <td class="right nowrap">
              <button class="btn sm ghost" @click="versions = i">价格版本</button>
              <template v-if="editable">
                <button class="btn sm" @click="startPrice(i)">调价</button>
                <button class="btn sm" @click="toggleShelf(i)">{{ i.is_on_shelf ? '下架' : '上架' }}</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Modal v-if="versions" :title="`${versions.name} 价格版本`" @close="versions = null">
      <table>
        <thead><tr><th>版本</th><th class="right">单价</th><th class="right">补贴比例</th><th>生效</th><th>失效</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="v in versions.versions" :key="v.id">
            <td>v{{ v.version_no }}</td><td class="num right">{{ money(v.unit_price) }}</td><td class="num right">{{ (v.subsidy_ratio * 100).toFixed(0) }}%</td>
            <td class="num small">{{ dt(v.effective_from, true) }}</td><td class="num small">{{ v.effective_to ? dt(v.effective_to, true) : '至今' }}</td><td class="small">{{ v.reason }}</td>
          </tr>
        </tbody>
      </table>
      <p class="small muted mt">价格只增不改。工单按服务发生时点绑定版本，调价不影响已结算金额。</p>
    </Modal>

    <Modal v-if="price.target" :title="`调整 ${price.target.name} 价格`" @close="price.target = null">
      <div class="form-grid">
        <label class="field"><span>新单价（元）</span><input v-model="price.yuan" type="number" step="0.01" /></label>
        <label class="field"><span>补贴比例（0 到 1）</span><input v-model="price.ratio" type="number" step="0.05" min="0" max="1" /></label>
        <label class="field"><span>生效时间</span><input v-model="price.effective_from" type="datetime-local" /></label>
        <label class="field"><span>调价原因<i>*</i></span><input v-model="price.reason" /></label>
      </div>
      <template #foot><button class="btn primary" @click="savePrice">保存新版本</button></template>
    </Modal>

    <Modal v-if="creating.show" title="新建服务项目" wide @close="creating.show = false">
      <div class="form-grid">
        <label class="field"><span>编码<i>*</i></span><input v-model="creating.code" /></label>
        <label class="field"><span>名称<i>*</i></span><input v-model="creating.name" /></label>
        <label class="field"><span>计价方式</span><select v-model="creating.pricing_mode"><option value="PER_TIME">按次</option><option value="PER_HOUR">按小时</option></select></label>
        <label class="field"><span>标准时长（分钟）</span><input v-model="creating.standard_minutes" type="number" /></label>
        <label class="field"><span>单价（元）<i>*</i></span><input v-model="creating.yuan" type="number" step="0.01" /></label>
        <label class="field"><span>补贴比例</span><input v-model="creating.ratio" type="number" step="0.05" min="0" max="1" /></label>
        <div class="field full"><span>所需技能</span><div class="checks"><label v-for="s in state.meta?.skills" :key="s"><input v-model="creating.skills" type="checkbox" :value="s" /> {{ s }}</label></div></div>
        <div class="field full"><span>适用能力等级</span><div class="checks"><label v-for="(t, c) in state.meta?.labels?.DisabilityLevel" :key="c"><input v-model="creating.levels" type="checkbox" :value="c" /> {{ t }}</label></div></div>
        <label class="checks full"><label><input v-model="creating.subsidy_eligible" type="checkbox" /> 纳入补贴</label></label>
        <label class="field full"><span>服务清单（顿号分隔）</span><input v-model="creating.checklist" /></label>
      </div>
      <template #foot><button class="btn primary" @click="create">创建</button></template>
    </Modal>
  </div>
</template>
