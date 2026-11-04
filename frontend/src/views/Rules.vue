<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { run, state } from '../store'
import { levelColor } from '../utils'

const rows = ref([])
const edit = reactive({ show: false, id: null, code: '', name: '', rule_type: 'THRESHOLD', metric: 'SYSTOLIC', op: '>=', threshold: 180, window: 3, hours: 48, level: 'RED', is_active: true })

async function load() { rows.value = await api.get('/alert-rules') }

function describe(r) {
  if (r.rule_type === 'THRESHOLD') return `${r.metric_label} ${r.op} ${r.threshold}`
  if (r.rule_type === 'CONSECUTIVE') return `${r.metric_label} 连续 ${r.window} 次 ${r.op} ${r.threshold}`
  if (r.rule_type === 'NO_DATA') return `${r.hours} 小时未上报任何指标`
  if (r.rule_type === 'BASELINE') return `${r.metric_label} 偏离个人基线（稳健 Z ≥ ${r.threshold}，至少 ${r.window} 个样本）`
  return '由人工操作触发'
}

function startEdit(rule) {
  Object.assign(edit, { show: true, id: rule?.id ?? null, code: rule?.code ?? '', name: rule?.name ?? '', rule_type: rule?.rule_type ?? 'THRESHOLD',
    metric: rule?.metric ?? 'SYSTOLIC', op: rule?.op ?? '>=', threshold: rule?.threshold ?? 180, window: rule?.window ?? 3, hours: rule?.hours ?? 48,
    level: rule?.level ?? 'RED', is_active: rule?.is_active ?? true })
}

async function save() {
  const body = { name: edit.name, level: edit.level, is_active: edit.is_active }
  if (edit.rule_type !== 'NO_DATA') Object.assign(body, { metric: edit.metric, threshold: Number(edit.threshold) })
  if (['THRESHOLD', 'CONSECUTIVE'].includes(edit.rule_type)) body.op = edit.op
  if (['CONSECUTIVE', 'BASELINE'].includes(edit.rule_type)) body.window = Number(edit.window)
  if (edit.rule_type === 'NO_DATA') body.hours = Number(edit.hours)
  const action = edit.id
    ? () => api.patch(`/alert-rules/${edit.id}`, body)
    : () => api.post('/alert-rules', { ...body, code: edit.code, rule_type: edit.rule_type })
  if (await run(action, '规则已保存，立即生效')) {
    edit.show = false
    load()
  }
}

async function toggle(rule) {
  if (await run(() => api.patch(`/alert-rules/${rule.id}`, { is_active: !rule.is_active }), rule.is_active ? '已停用' : '已启用')) load()
}

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="filters"><span class="grow"></span><button class="btn primary" @click="startEdit(null)">新建规则</button></div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>编码</th><th>名称</th><th>类型</th><th>判定条件</th><th>级别</th><th>版本</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td class="num small">{{ r.code }}</td><td><b>{{ r.name }}</b></td><td><span class="tag">{{ r.rule_type_label }}</span></td>
            <td class="small">{{ describe(r) }}</td><td><span class="tag" :class="levelColor(r.level)">{{ r.level_label }}</span></td>
            <td class="num small">v{{ r.version }}</td><td><span class="tag" :class="r.is_active ? 'green' : ''">{{ r.is_active ? '启用' : '停用' }}</span></td>
            <td class="right nowrap" v-if="r.rule_type !== 'MANUAL'">
              <button class="btn sm" @click="startEdit(r)">编辑</button>
              <button class="btn sm" @click="toggle(r)">{{ r.is_active ? '停用' : '启用' }}</button>
            </td><td v-else></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="small muted mt">修改规则立即生效。已产生的预警保留当时的规则快照，判定依据可追溯。</p>

    <Modal v-if="edit.show" :title="edit.id ? '编辑规则' : '新建规则'" @close="edit.show = false">
      <div class="form-grid">
        <label v-if="!edit.id" class="field"><span>编码<i>*</i></span><input v-model="edit.code" /></label>
        <label class="field"><span>名称<i>*</i></span><input v-model="edit.name" /></label>
        <label class="field"><span>类型</span>
          <select v-model="edit.rule_type" :disabled="Boolean(edit.id)">
            <option value="THRESHOLD">单项越界</option><option value="CONSECUTIVE">连续异常</option><option value="NO_DATA">长时间无数据</option><option value="BASELINE">个体基线偏离</option>
          </select>
        </label>
        <label class="field"><span>级别</span><select v-model="edit.level"><option value="RED">红色</option><option value="YELLOW">黄色</option><option value="BLUE">蓝色</option></select></label>
        <label v-if="edit.rule_type !== 'NO_DATA'" class="field"><span>指标</span>
          <select v-model="edit.metric"><option v-for="(t, c) in state.meta?.labels?.Metric" :key="c" :value="c">{{ t }}</option></select>
        </label>
        <label v-if="['THRESHOLD', 'CONSECUTIVE'].includes(edit.rule_type)" class="field"><span>比较</span>
          <select v-model="edit.op"><option>&gt;=</option><option>&lt;=</option><option>&gt;</option><option>&lt;</option></select>
        </label>
        <label v-if="edit.rule_type !== 'NO_DATA'" class="field"><span>{{ edit.rule_type === 'BASELINE' ? '稳健 Z 阈值' : '阈值' }}</span><input v-model="edit.threshold" type="number" step="0.1" /></label>
        <label v-if="['CONSECUTIVE', 'BASELINE'].includes(edit.rule_type)" class="field"><span>{{ edit.rule_type === 'BASELINE' ? '最少样本数' : '连续次数' }}</span><input v-model="edit.window" type="number" min="2" /></label>
        <label v-if="edit.rule_type === 'NO_DATA'" class="field"><span>时长（小时）</span><input v-model="edit.hours" type="number" /></label>
      </div>
      <template #foot><button class="btn primary" @click="save">保存</button></template>
    </Modal>
  </div>
</template>
