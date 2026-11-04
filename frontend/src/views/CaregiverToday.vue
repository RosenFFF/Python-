<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, NetworkError } from '../api'
import Modal from '../components/Modal.vue'
import { flushQueue, loadBootstrap, offlineState, refreshPending, saveBootstrap, startAutoSync } from '../offline'
import { run, state, toast } from '../store'
import { dt, statusColor } from '../utils'

const router = useRouter()
const orders = ref([])
const offers = ref([])
const rejecting = ref(null)
const reason = ref('')
const fromCache = ref(false)
let stop

async function load() {
  try {
    const data = await api.get('/sync/bootstrap')
    await saveBootstrap(JSON.parse(JSON.stringify(data)))
    orders.value = data.orders
    offers.value = await api.get('/offers/mine')
    fromCache.value = false
  } catch (error) {
    if (!(error instanceof NetworkError)) throw error
    const cached = await loadBootstrap()
    orders.value = cached?.orders || []
    fromCache.value = true
  }
}

async function accept(offer) {
  if (await run(() => api.post(`/offers/${offer.id}/accept`), '接单成功，时段已锁定')) load()
}

async function reject() {
  if (!reason.value.trim()) {
    toast('拒单必须填写理由', 'error')
    return
  }
  if (await run(() => api.post(`/offers/${rejecting.value.id}/reject`, { reason: reason.value }), '已拒单，系统已顺延给下一位')) {
    rejecting.value = null
    reason.value = ''
    load()
  }
}

async function syncNow() {
  const result = await run(() => flushQueue())
  if (result && result !== true) toast(`同步完成：成功 ${result.applied}，重复 ${result.duplicate}，被拒绝 ${result.rejected}`)
  load()
}

const onRefresh = () => load()
onMounted(() => {
  load()
  refreshPending()
  stop = startAutoSync((result) => {
    toast(`离线操作已同步：成功 ${result.applied}，被拒绝 ${result.rejected}`)
    load()
  })
  window.addEventListener('yihu:refresh', onRefresh)
})
onUnmounted(() => {
  stop?.()
  window.removeEventListener('yihu:refresh', onRefresh)
})
</script>

<template>
  <div class="mobile col">
    <div class="row between">
      <h1>今日工单</h1>
      <button v-if="offlineState.pending" class="btn" :disabled="!state.online" @click="syncNow">同步 {{ offlineState.pending }} 条离线操作</button>
    </div>
    <div v-if="fromCache" class="notice warn">当前显示的是离线缓存的工单。</div>
    <div v-if="offlineState.lastResult?.rejected" class="notice error">
      有 {{ offlineState.lastResult.rejected }} 条离线操作被拒绝：
      <div v-for="r in offlineState.lastResult.results.filter((x) => x.status === 'rejected')" :key="r.op_id">{{ r.message }}</div>
    </div>

    <section v-if="offers.length" class="card col">
      <h2>待接单（{{ offers.length }}）</h2>
      <div v-for="o in offers" :key="o.id" class="candidate">
        <div class="row between">
          <b>{{ o.item_name }}</b>
          <span class="small" style="color: var(--red)">请在 {{ dt(o.deadline_at) }} 前响应</span>
        </div>
        <div class="small">{{ dt(o.scheduled_start) }} · {{ o.elder_name }} · {{ o.disability_label }}</div>
        <div class="small muted">{{ o.address }}<template v-if="o.remark"> · {{ o.remark }}</template></div>
        <div class="row">
          <button class="btn primary grow" @click="accept(o)">接单</button>
          <button class="btn" @click="rejecting = o">拒单</button>
        </div>
      </div>
    </section>

    <section class="card col">
      <h2>待上门与服务中</h2>
      <div v-if="!orders.length" class="empty">今明两天没有工单</div>
      <div v-for="o in orders" :key="o.id" class="candidate" style="cursor: pointer" @click="router.push(`/cg/order/${o.id}`)">
        <div class="row between">
          <b>{{ dt(o.scheduled_start) }} {{ o.item_name }}</b>
          <span class="tag" :class="statusColor(o.status)">{{ o.status_label }}</span>
        </div>
        <div>{{ o.elder_name }}<span v-for="t in o.care_tags" :key="t" class="tag" style="margin-left: 4px">{{ t }}</span></div>
        <div class="small muted">{{ o.address }}</div>
        <div v-if="o.allergies" class="small" style="color: var(--red)">过敏：{{ o.allergies }}</div>
      </div>
    </section>

    <Modal v-if="rejecting" title="拒单理由" @close="rejecting = null">
      <label class="field"><span>理由<i>*</i></span><textarea v-model="reason" placeholder="比如：身体不适、距离过远"></textarea></label>
      <p class="small muted">连续拒单会提醒网格员和机构。</p>
      <template #foot><button class="btn" @click="rejecting = null">取消</button><button class="btn danger" @click="reject">确认拒单</button></template>
    </Modal>
  </div>
</template>
