<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { can, run } from '../store'
import { dt, statusColor } from '../utils'

const followups = ref([])
const complaints = ref([])
const dialog = reactive({ kind: null, target: null, outcome: 'IMPROVE', note: '', explanation: '', conclusion: 'UPHELD', basis: '', actions: [], points: 2 })

const canFollow = can('followup:handle')
const canManage = can('complaint:manage')
const canExplain = can('complaint:explain')
const openFollowups = computed(() => followups.value.filter((f) => f.status === 'OPEN'))

async function load() {
  complaints.value = await api.get('/complaints')
  if (canFollow) followups.value = await api.get('/followups')
}

function open(kind, target) {
  Object.assign(dialog, { kind, target, outcome: 'IMPROVE', note: '', explanation: '', conclusion: 'UPHELD', basis: '', actions: [], points: 2 })
}

async function submit() {
  const { kind, target } = dialog
  let action
  if (kind === 'followup') action = () => api.post(`/followups/${target.id}/handle`, { outcome: dialog.outcome, note: dialog.note })
  if (kind === 'explain') action = () => api.post(`/complaints/${target.id}/explain`, { explanation: dialog.explanation })
  if (kind === 'conclude') {
    const body = { conclusion: dialog.conclusion, basis: dialog.basis, actions: dialog.conclusion === 'UPHELD' ? dialog.actions : [] }
    if (body.actions.includes('PENALTY')) body.penalty_points = Number(dialog.points)
    action = () => api.post(`/complaints/${target.id}/conclude`, body)
  }
  if (await run(action, '已保存')) {
    dialog.kind = null
    load()
  }
}

async function assign(row) {
  if (await run(() => api.post(`/complaints/${row.id}/assign`), '已指派给机构，等待其提交说明')) load()
}

onMounted(load)
</script>

<template>
  <div class="col">
    <section v-if="canFollow" class="card">
      <h2 class="mb">低分回访任务（{{ openFollowups.length }} 待处理）</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>工单</th><th>老人</th><th>护理员</th><th>评分</th><th>留言</th><th>时限</th><th>状态</th><th></th></tr></thead>
          <tbody>
            <tr v-for="f in followups" :key="f.id" :class="{ 'flag-red': f.overdue }">
              <td class="num small"><router-link :to="`/orders/${f.order_id}`">{{ f.order_no }}</router-link></td>
              <td>{{ f.elder_name }}</td><td>{{ f.caregiver_name }}</td>
              <td class="num"><b>{{ f.overall }}</b> 星</td><td class="small" style="max-width: 240px">{{ f.comment }}</td>
              <td class="num nowrap">{{ dt(f.due_at) }}<span v-if="f.overdue" class="tag red"> 已超时</span></td>
              <td><span class="tag" :class="statusColor(f.status)">{{ f.status_label }}</span><div v-if="f.outcome" class="small muted">{{ f.outcome_label }}</div></td>
              <td class="right"><button v-if="f.status === 'OPEN'" class="btn sm primary" @click="open('followup', f)">回访</button></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!followups.length" class="empty">没有回访任务</div>
      </div>
    </section>

    <section class="card">
      <h2 class="mb">投诉单</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>编号</th><th>工单</th><th>老人</th><th>护理员</th><th>内容</th><th>状态</th><th>结论</th><th></th></tr></thead>
          <tbody>
            <tr v-for="c in complaints" :key="c.id">
              <td class="num small">{{ c.complaint_no }}</td>
              <td class="num small"><router-link :to="`/orders/${c.order_id}`">{{ c.order_no }}</router-link></td>
              <td>{{ c.elder_name }}</td><td>{{ c.caregiver_name }}</td>
              <td class="small" style="max-width: 260px">{{ c.content }}<div v-if="c.org_explanation" class="muted">机构说明：{{ c.org_explanation }}</div></td>
              <td><span class="tag" :class="statusColor(c.status)">{{ c.status_label }}</span></td>
              <td class="small">
                <template v-if="c.conclusion"><span class="tag" :class="c.conclusion === 'UPHELD' ? 'red' : ''">{{ c.conclusion_label }}</span>
                  <div class="muted">{{ c.basis }}</div><div v-if="c.action_labels.length">{{ c.action_labels.join('、') }}</div></template>
              </td>
              <td class="right nowrap">
                <button v-if="c.status === 'OPEN' && canManage" class="btn sm primary" @click="assign(c)">指派机构</button>
                <button v-if="c.status === 'ASSIGNED' && canExplain" class="btn sm primary" @click="open('explain', c)">提交说明</button>
                <button v-if="c.status === 'EXPLAINED' && canManage" class="btn sm primary" @click="open('conclude', c)">下结论</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!complaints.length" class="empty">没有投诉单</div>
      </div>
    </section>

    <Modal v-if="dialog.kind === 'followup'" title="回访处理" @close="dialog.kind = null">
      <div class="col">
        <div class="notice">{{ dialog.target.elder_name }}，综合 {{ dialog.target.overall }} 星。{{ dialog.target.comment }}</div>
        <div class="checks">
          <label><input v-model="dialog.outcome" type="radio" value="IMPROVE" /> 服务改进</label>
          <label><input v-model="dialog.outcome" type="radio" value="TO_COMPLAINT" /> 转投诉</label>
        </div>
        <label class="field"><span>核实情况<i>*</i></span><textarea v-model="dialog.note"></textarea></label>
      </div>
      <template #foot><button class="btn primary" :disabled="!dialog.note.trim()" @click="submit">保存</button></template>
    </Modal>

    <Modal v-if="dialog.kind === 'explain'" title="机构情况说明" @close="dialog.kind = null">
      <div class="notice mb">{{ dialog.target.content }}</div>
      <label class="field"><span>说明<i>*</i></span><textarea v-model="dialog.explanation" rows="4"></textarea></label>
      <template #foot><button class="btn primary" :disabled="!dialog.explanation.trim()" @click="submit">提交</button></template>
    </Modal>

    <Modal v-if="dialog.kind === 'conclude'" title="投诉结论" @close="dialog.kind = null">
      <div class="col">
        <div class="notice">{{ dialog.target.content }}<div class="small">机构说明：{{ dialog.target.org_explanation }}</div></div>
        <div class="checks">
          <label><input v-model="dialog.conclusion" type="radio" value="UPHELD" /> 投诉成立</label>
          <label><input v-model="dialog.conclusion" type="radio" value="REJECTED" /> 投诉不成立</label>
        </div>
        <label class="field"><span>结论依据<i>*</i></span><textarea v-model="dialog.basis"></textarea></label>
        <div v-if="dialog.conclusion === 'UPHELD'" class="field"><span>处理措施</span>
          <div class="checks">
            <label><input v-model="dialog.actions" type="checkbox" value="REFUND" /> 退费（生成红冲账单）</label>
            <label><input v-model="dialog.actions" type="checkbox" value="REPLACE" /> 更换护理员</label>
            <label><input v-model="dialog.actions" type="checkbox" value="PENALTY" /> 计扣分</label>
          </div>
        </div>
        <label v-if="dialog.conclusion === 'UPHELD' && dialog.actions.includes('PENALTY')" class="field"><span>扣分</span><input v-model="dialog.points" type="number" min="1" /></label>
        <div v-if="dialog.conclusion === 'UPHELD'" class="small muted">同一护理员月度累计 3 次成立，系统自动列入重点关注，派单降权。</div>
      </div>
      <template #foot><button class="btn primary" :disabled="!dialog.basis.trim()" @click="submit">归档结论</button></template>
    </Modal>
  </div>
</template>
