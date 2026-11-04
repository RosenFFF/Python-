<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { run } from '../store'
import { money, statusColor } from '../utils'

const rows = ref([])
const method = ref('微信')

async function load() { rows.value = await api.get('/statements') }

async function pay(row) {
  if (await run(() => api.post(`/statements/${row.id}/pay`, { method: method.value }), '已登记缴费，状态已回写到工单')) load()
}

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="filters">
      <label class="field"><span>缴费方式</span><select v-model="method"><option>微信</option><option>支付宝</option><option>现金</option><option>银行转账</option></select></label>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>月份</th><th>老人</th><th class="right">服务总额</th><th class="right">政府补贴</th><th class="right">长护险</th><th class="right">个人自付</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td class="num">{{ s.month }}</td><td>{{ s.elder_name }}</td>
            <td class="num right">{{ money(s.total_amount) }}</td><td class="num right">{{ money(s.gov_amount) }}</td>
            <td class="num right">{{ money(s.ltci_amount) }}</td><td class="num right"><b>{{ money(s.self_amount) }}</b></td>
            <td><span class="tag" :class="statusColor(s.payment_status)">{{ s.payment_label }}</span><span v-if="s.payment_method" class="small muted"> {{ s.payment_method }}</span></td>
            <td class="right"><button v-if="s.payment_status === 'UNPAID'" class="btn sm primary" @click="pay(s)">登记缴费</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty">账单生效后，这里会显示老人个人缴费单</div>
    </div>
  </div>
</template>
