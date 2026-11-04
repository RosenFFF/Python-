<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { run, state, toast } from '../store'
import { dt, serverNow, speak } from '../utils'

const router = useRouter()
const elders = ref([])
const upcoming = ref([])
const toReview = ref([])
const alerts = ref([])
const confirmSos = ref(false)
const sosElder = ref(null)

async function load() {
  elders.value = (await api.get('/elders')).items
  sosElder.value = elders.value[0]?.id
  const orders = (await api.get('/orders', { size: 30 })).items
  upcoming.value = orders.filter((o) => ['PENDING_ACCEPT', 'ACCEPTED', 'PENDING_DISPATCH', 'DISPATCHED', 'IN_SERVICE'].includes(o.status))
    .sort((a, b) => a.scheduled_start.localeCompare(b.scheduled_start))
  toReview.value = orders.filter((o) => o.status === 'COMPLETED' && serverNow().getTime() - new Date(o.completed_at).getTime() < 864e5).slice(0, 3)
  alerts.value = (await api.get('/alerts', { only_open: true, size: 5 })).items
}

async function sendSos() {
  const result = await run(() => api.post('/sos', { elder_id: sosElder.value, note: '从首页一键呼救' }))
  confirmSos.value = false
  if (result) {
    const message = '呼救已发出，社区网格员、紧急联系人和签约医生都已收到通知。请保持电话畅通。'
    toast(message, 'info', 6000)
    speak(message)
    load()
  }
}

onMounted(load)
</script>

<template>
  <div class="mobile col" data-speak>
    <h1>您好，{{ state.user.display_name }}</h1>

    <div v-if="alerts.length" class="notice error">
      <b>健康提醒：</b>{{ alerts[0].title }}，社区正在处理。{{ alerts[0].status_label }}。
    </div>

    <button class="sos" @click="confirmSos = true">一键呼救</button>

    <div class="elder-tiles">
      <router-link class="tile" to="/family/book">预约服务<small>助餐、助浴、陪同就医……</small></router-link>
      <router-link class="tile" to="/orders">我的服务<small>谁来、几点到、做了什么</small></router-link>
      <router-link class="tile" to="/family/health">健康记录<small>血压、血糖、心率趋势</small></router-link>
      <router-link class="tile" to="/family/bills">我的账单<small>补贴多少、自己付多少</small></router-link>
    </div>

    <section v-if="toReview.length" class="card">
      <h2 class="mb">请评价刚完成的服务</h2>
      <div v-for="o in toReview" :key="o.id" class="row between mb">
        <span>{{ o.item_name }}，{{ o.caregiver_name }}，{{ dt(o.completed_at) }}</span>
        <button class="btn primary" @click="router.push(`/orders/${o.id}`)">去评价</button>
      </div>
    </section>

    <section class="card">
      <h2 class="mb">接下来的服务</h2>
      <div v-if="!upcoming.length" class="muted">暂无安排</div>
      <div v-for="o in upcoming" :key="o.id" class="row between mb">
        <div>
          <b>{{ dt(o.scheduled_start) }}</b> {{ o.item_name }}
          <div class="small muted">{{ o.status_label }}<template v-if="o.caregiver_name">，护理员 {{ o.caregiver_name }}</template></div>
        </div>
        <button class="btn" @click="router.push(`/orders/${o.id}`)">查看</button>
      </div>
    </section>

    <Modal v-if="confirmSos" title="确认一键呼救？" @close="confirmSos = false">
      <p>确认后会立刻通知社区网格员、紧急联系人和签约医生。</p>
      <label v-if="elders.length > 1" class="field">
        <span>为谁呼救</span>
        <select v-model="sosElder"><option v-for="e in elders" :key="e.id" :value="e.id">{{ e.name }}</option></select>
      </label>
      <template #foot>
        <button class="btn" @click="confirmSos = false">取消</button>
        <button class="btn danger lg" @click="sendSos">确认呼救</button>
      </template>
    </Modal>
  </div>
</template>
