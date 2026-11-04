<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import Timeline from '../components/Timeline.vue'
import { can, isRole, run, toast } from '../store'
import { dt, label, money, statusColor } from '../utils'

const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)
const order = ref(null)
const photoUrls = ref([])
const cancel = reactive({ show: false, reason: '', preview: null })
const review = reactive({ show: false, punctuality: 5, attitude: 5, professionalism: 5, effectiveness: 5, comment: '' })
const complaint = reactive({ show: false, content: '' })
const spot = reactive({ show: false, passed: true, note: '' })
const reviewed = ref(false)
const CRITERIA = [['punctuality', '准时到达'], ['attitude', '服务态度'], ['professionalism', '专业程度'], ['effectiveness', '服务效果']]

const cancellable = computed(() => ['PENDING_ACCEPT', 'ACCEPTED', 'PENDING_DISPATCH', 'DISPATCHED'].includes(order.value?.status))
const steps = computed(() => {
  const flow = ['PENDING_ACCEPT', 'ACCEPTED', 'PENDING_DISPATCH', 'DISPATCHED', 'IN_SERVICE', 'COMPLETED']
  const current = flow.indexOf(order.value?.status)
  return flow.map((code, index) => ({ code, text: label('OrderStatus', code), done: current >= index, current: current === index }))
})

async function load() {
  order.value = await api.get(`/orders/${id}`)
  reviewed.value = order.value.timeline.some((e) => e.action === 'REVIEW')
  const photos = order.value.execution?.photos || []
  photoUrls.value = await Promise.all(photos.map((p) => api.blobUrl(`/photos/${p.id}`).catch(() => null)))
}

async function openCancel() {
  cancel.preview = await run(() => api.get(`/orders/${id}/cancel-preview`))
  cancel.reason = ''
  cancel.show = true
}

async function doCancel() {
  if (!cancel.reason.trim()) {
    toast('取消必须填写原因', 'error')
    return
  }
  if (await run(() => api.post(`/orders/${id}/cancel`, { reason: cancel.reason }), '预约已取消')) {
    cancel.show = false
    load()
  }
}

async function submitReview() {
  const body = { punctuality: review.punctuality, attitude: review.attitude, professionalism: review.professionalism, effectiveness: review.effectiveness, comment: review.comment || null }
  const result = await run(() => api.post(`/orders/${id}/review`, body))
  if (result) {
    toast(result.overall <= 3 ? `感谢评价（${result.overall} 星），社区将在 48 小时内回访` : `感谢评价（${result.overall} 星）`)
    review.show = false
    load()
  }
}

async function submitComplaint() {
  if (await run(() => api.post('/complaints', { order_id: id, content: complaint.content }), '投诉已提交，社区会尽快处理')) {
    complaint.show = false
    complaint.content = ''
  }
}

async function submitSpot() {
  if (await run(() => api.post(`/orders/${id}/spot-check`, { passed: spot.passed, note: spot.note || null }), '抽查结果已记录')) {
    spot.show = false
    load()
  }
}

onMounted(load)
</script>

<template>
  <div v-if="order" class="col" data-speak>
    <div class="page-head">
      <h1>{{ order.item_name }}</h1>
      <span class="tag" :class="statusColor(order.status)">{{ order.status_label }}</span>
      <span class="sub num">{{ order.order_no }}</span>
      <span class="grow"></span>
      <button v-if="order.status === 'PENDING_DISPATCH' && can('dispatch:manage')" class="btn primary" @click="router.push(`/dispatch/${id}`)">去派单</button>
      <button v-if="order.status === 'DISPATCHED' && can('dispatch:manage')" class="btn" @click="router.push(`/dispatch/${id}`)">改派</button>
      <button v-if="isRole('elder_family') && order.status === 'COMPLETED' && !reviewed" class="btn primary" @click="review.show = true">评价服务</button>
      <button v-if="isRole('elder_family') && order.caregiver_id" class="btn" @click="complaint.show = true">投诉</button>
      <button v-if="order.execution?.spot_check_status === 'PENDING' && can('exec:spotcheck')" class="btn primary" @click="spot.show = true">时长抽查</button>
      <button v-if="cancellable && can('order:cancel')" class="btn" @click="openCancel">取消预约</button>
    </div>

    <div class="card" v-if="!['CANCELLED', 'NO_SHOW'].includes(order.status)">
      <div class="row" style="gap: 4px">
        <template v-for="(s, i) in steps" :key="s.code">
          <span class="tag" :class="s.current ? 'brand' : s.done ? 'green' : ''">{{ s.text }}</span>
          <span v-if="i < steps.length - 1" class="muted">›</span>
        </template>
      </div>
    </div>

    <div v-if="order.needs_manual && order.validation_issues.length" class="notice warn">
      <b>预约校验提示：</b><div v-for="i in order.validation_issues" :key="i.code">{{ i.message }}</div>
    </div>

    <div class="grid g2">
      <section class="card">
        <h2 class="mb">工单信息</h2>
        <dl class="kv">
          <dt>老人</dt><dd>{{ order.elder_name }}　{{ order.disability_label }} <span v-for="t in order.care_tags" :key="t" class="tag">{{ t }}</span></dd>
          <dt>预约时间</dt><dd class="num">{{ dt(order.scheduled_start, true) }}</dd>
          <dt>服务地址</dt><dd>{{ order.address }}</dd>
          <dt>护理员</dt><dd>{{ order.caregiver_name || '尚未派单' }}</dd>
          <dt>单价</dt><dd class="num">{{ money(order.unit_price) }}　价格版本 v{{ order.price_version }}</dd>
          <dt>缴费</dt><dd><span class="tag" :class="statusColor(order.payment_status)">{{ order.payment_label }}</span></dd>
          <dt v-if="order.remark">备注</dt><dd v-if="order.remark">{{ order.remark }}</dd>
          <dt v-if="order.cancel_reason">取消原因</dt><dd v-if="order.cancel_reason">{{ order.cancel_reason }}<span v-if="order.cancel_chargeable" class="tag red"> 计费</span></dd>
        </dl>
      </section>

      <section class="card">
        <h2 class="mb">服务证据链</h2>
        <div v-if="!order.execution" class="empty">护理员签到后这里会显示服务记录</div>
        <template v-else>
          <dl class="kv">
            <dt>签到</dt><dd class="num">{{ dt(order.execution.checkin_at) }}，距服务地址 {{ Math.round(order.execution.checkin_distance_m) }} 米
              <span v-if="!order.execution.is_on_time" class="tag yellow">迟到</span>
              <span v-if="order.execution.offline_synced" class="tag blue">离线同步</span>
            </dd>
            <dt v-if="order.execution.checkin_note">定位说明</dt><dd v-if="order.execution.checkin_note">{{ order.execution.checkin_note }}</dd>
            <dt>签退</dt><dd class="num">{{ dt(order.execution.checkout_at) }}</dd>
            <dt>服务时长</dt><dd class="num">{{ order.execution.duration_minutes ?? '—' }} 分钟（标准 {{ order.standard_minutes }} 分钟）
              <span v-if="order.execution.duration_abnormal" class="tag red">时长异常　{{ order.execution.spot_check_label }}</span>
            </dd>
            <dt>服务小结</dt><dd class="pre">{{ order.execution.summary || '—' }}</dd>
          </dl>
          <div class="row mt">
            <span v-for="c in order.execution.checklist" :key="c.name" class="tag" :class="c.done ? 'green' : 'red'">{{ c.done ? '已完成' : '未完成' }} {{ c.name }}</span>
          </div>
          <div class="row mt">
            <img v-for="(u, i) in photoUrls" :key="i" :src="u" class="photo" alt="现场照片" />
          </div>
        </template>
      </section>
    </div>

    <section class="card">
      <h2 class="mb">全程留痕</h2>
      <Timeline :items="order.timeline" />
    </section>

    <Modal v-if="cancel.show" title="取消预约" @close="cancel.show = false">
      <div class="notice mb" :class="{ warn: cancel.preview?.chargeable }">
        {{ cancel.preview?.explanation }}
        <b v-if="cancel.preview?.chargeable">，需支付 {{ money(cancel.preview.fee) }}</b>
      </div>
      <label class="field"><span>取消原因<i>*</i></span><textarea v-model="cancel.reason"></textarea></label>
      <template #foot><button class="btn" @click="cancel.show = false">不取消</button><button class="btn danger" @click="doCancel">确认取消</button></template>
    </Modal>

    <Modal v-if="review.show" title="服务评价" @close="review.show = false">
      <div class="col">
        <div v-for="[key, name] in CRITERIA" :key="key" class="row between">
          <span>{{ name }}</span>
          <span class="stars" role="radiogroup" :aria-label="name">
            <button v-for="n in 5" :key="n" type="button" :class="{ on: review[key] >= n }" :aria-label="`${n} 星`" @click="review[key] = n">★</button>
          </span>
        </div>
        <label class="field"><span>留言（选填）</span><textarea v-model="review.comment"></textarea></label>
      </div>
      <template #foot><button class="btn primary lg" @click="submitReview">提交评价</button></template>
    </Modal>

    <Modal v-if="complaint.show" title="提交投诉" @close="complaint.show = false">
      <label class="field"><span>投诉内容<i>*</i></span><textarea v-model="complaint.content" rows="4"></textarea></label>
      <template #foot><button class="btn" @click="complaint.show = false">取消</button><button class="btn danger" :disabled="!complaint.content.trim()" @click="submitComplaint">提交</button></template>
    </Modal>

    <Modal v-if="spot.show" title="时长异常抽查" @close="spot.show = false">
      <div class="col">
        <div class="checks">
          <label><input v-model="spot.passed" type="radio" :value="true" /> 通过</label>
          <label><input v-model="spot.passed" type="radio" :value="false" /> 不通过</label>
        </div>
        <label class="field"><span>说明<i v-if="!spot.passed">*</i></span><textarea v-model="spot.note"></textarea></label>
      </div>
      <template #foot><button class="btn primary" @click="submitSpot">提交</button></template>
    </Modal>
  </div>
</template>
