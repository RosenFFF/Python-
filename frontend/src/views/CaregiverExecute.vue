<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, NetworkError } from '../api'
import Modal from '../components/Modal.vue'
import { enqueue, loadBootstrap, updateCachedOrder } from '../offline'
import { showError, state, toast } from '../store'
import { dt, label } from '../utils'

const route = useRoute()
const router = useRouter()
const orderId = Number(route.params.id)
const order = ref(null)
const checklist = ref([])
const status = ref('')
const checkinInfo = ref(null)
const photos = ref(0)
const offlineMode = ref(false)
const position = reactive({ lat: null, lng: null, source: '' })
const farDetail = ref(null)
const farNote = ref('')
const out = reactive({ summary: '', systolic: '', diastolic: '', glucose: '', incomplete: '' })
const exception = reactive({ show: false, type: 'NOT_HOME', description: '' })
const done = ref(null)

const allDone = computed(() => checklist.value.every((i) => i.done))
const doneCount = computed(() => checklist.value.filter((i) => i.done).length)

async function load() {
  try {
    const detail = await api.get(`/orders/${orderId}`)
    order.value = detail
    status.value = detail.status
    checklist.value = detail.execution?.checklist?.length ? detail.execution.checklist : (await itemChecklist(detail))
    photos.value = detail.execution?.photos?.length || 0
    checkinInfo.value = detail.execution
  } catch (error) {
    if (!(error instanceof NetworkError)) {
      showError(error)
      return
    }
    const cached = await loadBootstrap()
    const found = cached?.orders.find((o) => o.id === orderId)
    if (!found) {
      toast('离线缓存里没有这张工单', 'error')
      return
    }
    offlineMode.value = true
    order.value = found
    status.value = found.status
    checklist.value = found.checklist
  }
  position.lat = order.value.lat
  position.lng = order.value.lng
  position.source = '服务地址坐标（演示）'
}

async function itemChecklist(detail) {
  const boot = await api.get('/sync/bootstrap').catch(() => null)
  return boot?.orders.find((o) => o.id === detail.id)?.checklist || []
}

function locate() {
  if (!navigator.geolocation) {
    toast('当前设备不支持定位', 'error')
    return
  }
  navigator.geolocation.getCurrentPosition(
    (p) => {
      position.lat = p.coords.latitude
      position.lng = p.coords.longitude
      position.source = `设备定位，精度约 ${Math.round(p.coords.accuracy)} 米`
    },
    () => toast('定位失败，请检查定位权限', 'error'),
    { enableHighAccuracy: true, timeout: 8000 },
  )
}

// 演示用：模拟定位偏差，展示超 200 米须说明
function simulateFar() {
  position.lat = order.value.lat + 0.004
  position.lng = order.value.lng
  position.source = '模拟定位（距服务地址约 440 米）'
}

async function offlineFallback(kind, payload, apply) {
  await enqueue(kind, orderId, payload)
  offlineMode.value = true
  apply()
  toast('当前无网络，操作已保存，联网后自动同步')
}

async function checkIn(note) {
  const body = { lat: position.lat, lng: position.lng, note: note || null }
  try {
    const result = await api.post(`/orders/${orderId}/check-in`, body)
    status.value = 'IN_SERVICE'
    checklist.value = result.checklist
    farDetail.value = null
    toast(`已签到，距服务地址 ${Math.round(result.distance_m)} 米`)
    load()
  } catch (error) {
    if (error instanceof NetworkError) {
      await offlineFallback('CHECKIN', body, () => {
        status.value = 'IN_SERVICE'
        updateCachedOrder(orderId, { status: 'IN_SERVICE', status_label: '服务中' })
      })
    } else if (error.code === 'CHECKIN_TOO_FAR') {
      farDetail.value = error.details
    } else {
      showError(error)
    }
  }
}

async function toggle(item) {
  item.done = !item.done
  const items = checklist.value.map((i) => ({ name: i.name, done: i.done }))
  try {
    await api.put(`/orders/${orderId}/checklist`, { items })
  } catch (error) {
    if (error instanceof NetworkError) {
      await enqueue('CHECKLIST', orderId, { items })
      updateCachedOrder(orderId, { checklist: items })
    } else {
      item.done = !item.done
      showError(error)
    }
  }
}

async function upload(event) {
  const file = event.target.files[0]
  if (!file) return
  try {
    const result = await api.upload(`/orders/${orderId}/photos`, file)
    photos.value += 1
    toast(`已上传，压缩 ${Math.round(result.original_bytes / 1024)}KB → ${Math.round(result.size_bytes / 1024)}KB`)
  } catch (error) {
    showError(error)
  }
  event.target.value = ''
}

function metrics() {
  const list = []
  for (const [key, metric] of [['systolic', 'SYSTOLIC'], ['diastolic', 'DIASTOLIC'], ['glucose', 'GLUCOSE']]) {
    if (out[key] !== '') list.push({ metric, value: Number(out[key]) })
  }
  return list
}

async function checkOut() {
  if (!out.summary.trim()) {
    toast('请填写服务小结', 'error')
    return
  }
  const body = { summary: out.summary, metrics: metrics(), incomplete_reason: out.incomplete || null }
  try {
    done.value = await api.post(`/orders/${orderId}/check-out`, body)
    status.value = 'COMPLETED'
  } catch (error) {
    if (error instanceof NetworkError) {
      await offlineFallback('CHECKOUT', body, () => {
        status.value = 'COMPLETED'
        done.value = { offline: true }
        updateCachedOrder(orderId, { status: 'COMPLETED', status_label: '已完成' })
      })
    } else {
      showError(error)
    }
  }
}

async function report() {
  const body = { exception_type: exception.type, description: exception.description || null }
  try {
    await api.post(`/orders/${orderId}/exceptions`, body)
    toast(exception.type === 'UNWELL' ? '已上报，并触发红色健康预警' : '已上报，进入人工处置队列')
  } catch (error) {
    if (error instanceof NetworkError) {
      await enqueue('EXCEPTION', orderId, body)
      toast('当前无网络，异常已保存，联网后同步。紧急情况请直接拨打电话！', 'error', 6000)
    } else {
      showError(error)
      return
    }
  }
  exception.show = false
}

onMounted(load)
</script>

<template>
  <div v-if="order" class="mobile col">
    <div class="row between">
      <h1>{{ order.item_name }}</h1>
      <button class="btn sm" @click="router.push('/cg')">返回</button>
    </div>
    <div v-if="offlineMode || !state.online" class="notice warn">离线作业中：操作保存在本机，并记录实际发生时间，联网后自动同步。</div>

    <section class="card">
      <dl class="kv">
        <dt>老人</dt><dd>{{ order.elder_name }} <span v-for="t in order.care_tags" :key="t" class="tag">{{ t }}</span></dd>
        <dt>预约时间</dt><dd>{{ dt(order.scheduled_start) }}（标准 {{ order.standard_minutes }} 分钟）</dd>
        <dt>服务地址</dt><dd>{{ order.address }}</dd>
        <dt v-if="order.remark">备注</dt><dd v-if="order.remark">{{ order.remark }}</dd>
      </dl>
    </section>

    <!-- 第一步 签到 -->
    <section v-if="status === 'DISPATCHED'" class="card col">
      <h2>第 1 步：到达后签到</h2>
      <div class="small muted">当前定位：{{ position.source }}</div>
      <div class="row">
        <button class="btn" @click="locate">获取设备定位</button>
        <button class="btn" @click="simulateFar">模拟定位偏差</button>
      </div>
      <button class="btn primary lg" @click="checkIn()">签到</button>
    </section>

    <!-- 第二步 服务 -->
    <template v-if="status === 'IN_SERVICE'">
      <section class="card col">
        <div class="row between"><h2>第 2 步：逐项勾选</h2><span class="muted num">{{ doneCount }}/{{ checklist.length }}</span></div>
        <label v-for="item in checklist" :key="item.name" class="check-row" :class="{ done: item.done }">
          <input type="checkbox" :checked="item.done" @change="toggle(item)" /> {{ item.name }}
        </label>
      </section>

      <section class="card col">
        <div class="row between"><h2>现场照片</h2><span class="muted num">{{ photos }}/3</span></div>
        <input type="file" accept="image/*" capture="environment" :disabled="photos >= 3 || !state.online" @change="upload" />
        <span class="small muted">自动压缩并清除定位信息。离线时请联网后补传。</span>
      </section>

      <section class="card col">
        <h2>第 3 步：签退</h2>
        <div class="form-grid">
          <label class="field"><span>收缩压 mmHg</span><input v-model="out.systolic" type="number" inputmode="numeric" /></label>
          <label class="field"><span>舒张压 mmHg</span><input v-model="out.diastolic" type="number" inputmode="numeric" /></label>
          <label class="field"><span>血糖 mmol/L</span><input v-model="out.glucose" type="number" step="0.1" inputmode="decimal" /></label>
        </div>
        <label class="field"><span>服务小结<i>*</i></span><textarea v-model="out.summary" placeholder="老人状态、完成情况"></textarea></label>
        <label v-if="!allDone" class="field"><span>未完成条目说明<i>*</i></span><input v-model="out.incomplete" placeholder="比如：老人拒绝修剪指甲" /></label>
        <button class="btn primary lg" @click="checkOut">签退</button>
      </section>
    </template>

    <section v-if="status === 'COMPLETED'" class="card col">
      <h2>服务已完成</h2>
      <div v-if="done?.offline" class="notice warn">签退已保存在本机，联网后同步，服务时长按实际时间计算。</div>
      <div v-else-if="done" class="notice" :class="{ warn: done.duration_abnormal }">
        服务时长 {{ done.duration_minutes }} 分钟<template v-if="done.duration_abnormal">，与标准时长偏差较大，已标记时长异常待抽查</template>。
        <div v-if="done.alerts?.length">本次测量触发了健康预警，已通知网格员。</div>
      </div>
      <button class="btn primary" @click="router.push('/cg')">返回工单列表</button>
    </section>

    <button v-if="['DISPATCHED', 'IN_SERVICE'].includes(status)" class="btn danger" @click="exception.show = true">上报异常（老人不在家、突发不适等）</button>

    <Modal v-if="farDetail" title="定位偏差超出范围" @close="farDetail = null">
      <p>当前定位距服务地址 <b>{{ Math.round(farDetail.distance_m) }}</b> 米，超过 {{ farDetail.limit_m }} 米，请填写说明后继续。</p>
      <label class="field"><span>说明<i>*</i></span><input v-model="farNote" placeholder="比如：小区门禁、地址标注偏差" /></label>
      <template #foot>
        <button class="btn" @click="farDetail = null">取消</button>
        <button class="btn primary" :disabled="!farNote.trim()" @click="checkIn(farNote)">继续签到</button>
      </template>
    </Modal>

    <Modal v-if="exception.show" title="上报异常" @close="exception.show = false">
      <div class="col">
        <label class="field"><span>异常类型</span>
          <select v-model="exception.type">
            <option v-for="t in ['NOT_HOME', 'REFUSED', 'UNWELL', 'OTHER']" :key="t" :value="t">{{ label('ExceptionType', t) }}</option>
          </select>
        </label>
        <div v-if="exception.type === 'UNWELL'" class="notice error">选择「突发不适」会立刻触发红色预警，通知网格员、家属和签约医生。</div>
        <label class="field"><span>情况说明</span><textarea v-model="exception.description" placeholder="比如：老人精神萎靡，无法唤醒"></textarea></label>
      </div>
      <template #foot><button class="btn" @click="exception.show = false">取消</button><button class="btn danger" @click="report">提交</button></template>
    </Modal>
  </div>
</template>
