<script setup>
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import Pager from '../components/Pager.vue'
import { run, toast } from '../store'
import { dt, statusColor } from '../utils'

const filters = reactive({ status: '', page: 1, size: 20 })
const data = ref({ items: [], total: 0 })
const closing = ref(null)
const form = reactive({ resolution: '', order_action: 'CONTINUE' })
const ACTIONS = { CONTINUE: '继续服务', NO_SHOW: '判定爽约（计费）', CANCEL: '取消工单（不计费）' }

async function load() { data.value = await api.get('/exceptions', filters) }

async function pick(row) {
  if (await run(() => api.post(`/exceptions/${row.id}/pick`), '已接手处置')) load()
}

async function close() {
  if (!form.resolution.trim()) {
    toast('请填写处置结果', 'error')
    return
  }
  const result = await run(() => api.post(`/exceptions/${closing.value.id}/close`, form))
  if (result) {
    toast(`异常已闭环，处置时长 ${result.handle_minutes} 分钟`)
    closing.value = null
    form.resolution = ''
    load()
  }
}

const refresh = () => load()
watch(() => [filters.status, filters.page], load)
onMounted(() => { load(); window.addEventListener('yihu:refresh', refresh) })
onUnmounted(() => window.removeEventListener('yihu:refresh', refresh))
</script>

<template>
  <div class="card">
    <div class="filters">
      <label class="field"><span>状态</span>
        <select v-model="filters.status"><option value="">全部</option><option value="OPEN">待处置</option><option value="PROCESSING">处置中</option><option value="CLOSED">已闭环</option></select>
      </label>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>类型</th><th>工单</th><th>老人</th><th>护理员</th><th>说明</th><th>上报时间</th><th>状态</th><th>时长</th><th></th></tr></thead>
        <tbody>
          <tr v-for="e in data.items" :key="e.id" :class="{ 'flag-red': e.exception_type === 'UNWELL' && e.status !== 'CLOSED' }">
            <td><span class="tag" :class="e.exception_type === 'UNWELL' ? 'red' : 'yellow'">{{ e.type_label }}</span></td>
            <td class="num small"><router-link :to="`/orders/${e.order_id}`">{{ e.order_no }}</router-link></td>
            <td>{{ e.elder_name }}</td><td>{{ e.caregiver_name }}</td>
            <td class="small" style="max-width: 260px">{{ e.description }}<div v-if="e.resolution" class="muted">处置：{{ e.resolution }}</div></td>
            <td class="num nowrap">{{ dt(e.reported_at) }}</td>
            <td><span class="tag" :class="statusColor(e.status)">{{ e.status_label }}</span><div v-if="e.handler_name" class="small muted">{{ e.handler_name }}</div></td>
            <td class="num nowrap">{{ e.status === 'CLOSED' ? e.handle_minutes : e.open_minutes }} 分钟</td>
            <td class="nowrap right">
              <button v-if="e.status === 'OPEN'" class="btn sm" @click="pick(e)">接手</button>
              <button v-if="e.status !== 'CLOSED'" class="btn sm primary" @click="closing = e">闭环</button>
              <router-link v-if="e.alert_id" class="btn sm ghost" to="/alerts">预警</router-link>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!data.items.length" class="empty">没有异常单</div>
    </div>
    <Pager v-model:page="filters.page" :size="filters.size" :total="data.total" />

    <Modal v-if="closing" title="闭环异常" @close="closing = null">
      <div class="col">
        <div class="notice">{{ closing.type_label }}：{{ closing.description || '无说明' }}</div>
        <label class="field"><span>工单处理</span>
          <select v-model="form.order_action"><option v-for="(text, code) in ACTIONS" :key="code" :value="code">{{ text }}</option></select>
        </label>
        <label class="field"><span>处置结果<i>*</i></span><textarea v-model="form.resolution"></textarea></label>
      </div>
      <template #foot><button class="btn" @click="closing = null">取消</button><button class="btn primary" @click="close">确认闭环</button></template>
    </Modal>
  </div>
</template>
