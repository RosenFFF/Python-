<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { can, run, state } from '../store'
import { money, pct, previousMonth, statusColor } from '../utils'

const router = useRouter()
const rows = ref([])
const communities = ref([])
const filters = reactive({ month: '', status: '' })
const gen = reactive({ show: false, community_id: null, month: previousMonth() })

async function load() { rows.value = await api.get('/bills', filters) }

async function generate() {
  const bill = await run(() => api.post('/bills/generate', { community_id: gen.community_id, month: gen.month }), '账单已生成并完成差异校验')
  if (bill) {
    gen.show = false
    router.push(`/bills/${bill.id}`)
  }
}

onMounted(async () => {
  load()
  const tree = await api.get('/org/tree')
  communities.value = tree.communities
  gen.community_id = state.user.community_id || communities.value[0]?.id
})
</script>

<template>
  <div class="card">
    <div class="filters">
      <label class="field"><span>月份</span><input v-model="filters.month" type="month" @change="load" /></label>
      <label class="field"><span>状态</span>
        <select v-model="filters.status" @change="load"><option value="">全部</option><option v-for="(t, c) in state.meta?.labels?.BillStatus" :key="c" :value="c">{{ t }}</option></select>
      </label>
      <span class="grow"></span>
      <button v-if="can('billing:generate')" class="btn primary" @click="gen.show = true">生成账单</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>月份</th><th>社区</th><th class="right">账单总额</th><th class="right">政府补贴</th><th class="right">长护险</th><th class="right">个人自付</th><th class="right">差异比例</th><th>待处理差异</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="b in rows" :key="b.id" :class="{ 'flag-red': b.status === 'NEEDS_REVIEW' }">
            <td class="num">{{ b.month }}</td><td>{{ b.community_name }}</td>
            <td class="num right">{{ money(b.total_amount) }}</td><td class="num right">{{ money(b.gov_amount) }}</td>
            <td class="num right">{{ money(b.ltci_amount) }}</td><td class="num right">{{ money(b.self_amount) }}</td>
            <td class="num right" :class="{ 'level-RED': b.discrepancy_ratio > 0.01 }">{{ pct(b.discrepancy_ratio, 2) }}</td>
            <td class="num">{{ b.open_discrepancies || '—' }}</td>
            <td><span class="tag" :class="statusColor(b.status)">{{ b.status_label }}</span></td>
            <td class="right"><button class="btn sm" @click="router.push(`/bills/${b.id}`)">详情</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty">没有账单</div>
    </div>

    <Modal v-if="gen.show" title="生成月度账单" @close="gen.show = false">
      <div class="col">
        <label class="field"><span>社区</span>
          <select v-model.number="gen.community_id" :disabled="Boolean(state.user.community_id)"><option v-for="c in communities" :key="c.id" :value="c.id">{{ c.name }}</option></select>
        </label>
        <label class="field"><span>月份</span><input v-model="gen.month" type="month" /></label>
        <div class="small muted">系统每月 1 日凌晨自动生成上月账单。未进入确认流程的账单可以重新生成。</div>
      </div>
      <template #foot><button class="btn primary" @click="generate">生成</button></template>
    </Modal>
  </div>
</template>
