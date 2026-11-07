<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import Pager from '../components/Pager.vue'
import { can, isRole, state } from '../store'
import { dt, statusColor } from '../utils'

const route = useRoute()
const router = useRouter()
const filters = reactive({ status: route.query.status || '', keyword: '', page: 1, size: 20 })
const data = ref({ items: [], total: 0 })

async function load() {
  data.value = await api.get('/orders', filters)
}

function open(order) {
  if (order.status === 'PENDING_DISPATCH' && can('dispatch:manage')) router.push(`/dispatch/${order.id}`)
  else router.push(`/orders/${order.id}`)
}

watch(() => [filters.status, filters.page], load)
onMounted(load)
</script>

<template>
  <div class="card">
    <h1 v-if="isRole('elder_family')" class="mb">我的服务</h1>
    <div class="filters">
      <label class="field"><span>状态</span>
        <select v-model="filters.status" @change="filters.page = 1">
          <option value="">全部</option>
          <option v-for="(text, code) in state.meta?.labels?.OrderStatus" :key="code" :value="code">{{ text }}</option>
        </select>
      </label>
      <label v-if="!isRole('elder_family')" class="field"><span>工单号或老人姓名</span><input v-model="filters.keyword" @keyup.enter="filters.page = 1; load()" /></label>
      <button v-if="!isRole('elder_family')" class="btn" @click="filters.page = 1; load()">查询</button>
    </div>
    <div v-if="isRole('elder_family')" class="order-cards">
      <div v-for="o in data.items" :key="o.id" class="order-card" @click="open(o)">
        <div class="row between">
          <b>{{ o.item_name }}</b>
          <span class="tag" :class="statusColor(o.status)">{{ o.status_label }}</span>
        </div>
        <div class="num">{{ dt(o.scheduled_start) }}</div>
        <div class="row between">
          <span class="small muted">{{ o.elder_name }}<template v-if="o.caregiver_name">，护理员 {{ o.caregiver_name }}</template></span>
          <button class="btn sm" @click.stop="open(o)">查看</button>
        </div>
      </div>
      <div v-if="!data.items.length" class="empty">还没有服务记录</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>工单号</th><th>老人</th><th>服务</th><th>预约时间</th><th>状态</th><th>护理员</th><th>来源</th><th></th></tr></thead>
        <tbody>
          <tr v-for="o in data.items" :key="o.id">
            <td class="num small">{{ o.order_no }}</td>
            <td>{{ o.elder_name }} <span class="muted small">{{ o.grid_name }}</span></td>
            <td>{{ o.item_name }}</td>
            <td class="num nowrap">{{ dt(o.scheduled_start) }}</td>
            <td>
              <span class="tag" :class="statusColor(o.status)">{{ o.status_label }}</span>
              <span v-if="o.needs_manual && o.status === 'PENDING_ACCEPT'" class="tag red">人工</span>
              <span v-if="o.manual_coordination" class="tag red">需协调</span>
            </td>
            <td>{{ o.caregiver_name || '—' }}</td>
            <td class="small muted">{{ o.source_label }}</td>
            <td class="right"><button class="btn sm" @click="open(o)">{{ o.status === 'PENDING_DISPATCH' && can('dispatch:manage') ? '派单' : '查看' }}</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!data.items.length" class="empty">没有符合条件的工单</div>
    </div>
    <Pager v-model:page="filters.page" :size="filters.size" :total="data.total" />
  </div>
</template>
