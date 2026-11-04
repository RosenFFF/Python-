<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { run, toast } from '../store'
import { dateOnly, label, listen, serverNow, toLocalInput, voiceSupported, yuan } from '../utils'

const router = useRouter()
const elders = ref([])
const items = ref([])
const plans = ref([])
const form = reactive({ elder_id: null, item_id: null, scheduled_start: '', remark: '', source: 'ONLINE' })
const weekly = reactive({ enabled: false, weekday: 0, time: '09:00' })
const sentence = ref('')
const parsed = ref(null)
const listening = ref(false)
const result = ref(null)
const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

const currentItem = computed(() => items.value.find((i) => i.id === form.item_id))

async function load() {
  elders.value = (await api.get('/elders')).items
  items.value = await api.get('/catalog/items', { on_shelf_only: true })
  form.elder_id ||= elders.value[0]?.id
  plans.value = await api.get('/plans')
  const start = new Date(serverNow().getTime() + 864e5)
  start.setHours(14, 0, 0, 0)
  form.scheduled_start ||= toLocalInput(start)
}

async function parseSentence() {
  if (!sentence.value.trim()) return
  const data = await run(() => api.post('/ai/parse-booking', { text: sentence.value }))
  if (!data) return
  parsed.value = data
  if (data.item_id) form.item_id = data.item_id
  if (data.scheduled_at) form.scheduled_start = toLocalInput(data.scheduled_at)
  if (data.weekly) {
    weekly.enabled = true
    weekly.weekday = data.weekday
    if (data.scheduled_at) weekly.time = toLocalInput(data.scheduled_at).slice(11, 16)
  }
  form.source = 'VOICE'
}

function startVoice() {
  listening.value = true
  listen((text) => {
    sentence.value = text
    parseSentence()
  }, () => { listening.value = false })
}

async function submit() {
  if (!form.elder_id || !form.item_id) {
    toast('请选择老人和服务项目', 'error')
    return
  }
  if (weekly.enabled) {
    const today = dateOnly(serverNow())
    const ok = await run(() => api.post('/plans', {
      elder_id: form.elder_id, item_id: form.item_id, weekday: weekly.weekday, time_of_day: weekly.time,
      start_date: today, remark: form.remark || null,
    }), '周期预约已创建，系统会自动滚动生成未来 7 天的工单')
    if (ok) plans.value = await api.get('/plans')
    return
  }
  const order = await run(() => api.post('/orders', { ...form, remark: form.remark || null, scheduled_start: `${form.scheduled_start}:00` }))
  if (order) result.value = order
}

async function stopPlan(plan) {
  if (await run(() => api.del(`/plans/${plan.id}`), '周期预约已停止')) plans.value = await api.get('/plans')
}

onMounted(load)
</script>

<template>
  <div class="mobile col">
    <h1>预约服务</h1>

    <section class="card col">
      <h2>说一句话就能预约</h2>
      <div class="row">
        <input v-model="sentence" class="grow" style="width: auto" placeholder="比如：帮我约明天下午两点助浴" @keyup.enter="parseSentence" />
        <button v-if="voiceSupported()" class="btn" :disabled="listening" @click="startVoice">{{ listening ? '正在听…' : '语音' }}</button>
        <button class="btn" @click="parseSentence">识别</button>
      </div>
      <div v-if="parsed" class="notice" :class="{ warn: parsed.missing.length }">
        <template v-if="parsed.item_name">服务项目：<b>{{ parsed.item_name }}</b>　</template>
        <template v-if="parsed.scheduled_at">时间：<b>{{ parsed.scheduled_at.replace('T', ' ').slice(0, 16) }}</b>　</template>
        <template v-if="parsed.weekly">每周固定　</template>
        <template v-if="parsed.missing.length">还需要补充：{{ parsed.missing.join('、') }}。</template>
        <template v-else>已填入下方，请确认后提交。</template>
        <div v-for="hint in parsed.hints" :key="hint">{{ hint }}</div>
      </div>
    </section>

    <section v-if="result" class="card col">
      <h2>{{ result.needs_manual ? '预约已提交，需人工受理' : '预约已提交' }}</h2>
      <div v-if="result.needs_manual" class="notice warn">
        <div v-for="issue in result.validation_issues" :key="issue.code">{{ issue.message }}</div>
        <div class="mt">网格员会尽快与您联系确认。</div>
      </div>
      <div v-else class="notice">校验通过，正在等待网格员受理。工单号 {{ result.order_no }}</div>
      <div class="row">
        <button class="btn primary" @click="router.push(`/orders/${result.id}`)">查看进度</button>
        <button class="btn" @click="result = null">再约一单</button>
      </div>
    </section>

    <section v-else class="card col">
      <label v-if="elders.length > 1" class="field"><span>为谁预约</span>
        <select v-model="form.elder_id"><option v-for="e in elders" :key="e.id" :value="e.id">{{ e.name }}</option></select>
      </label>
      <div v-else-if="elders[0]" class="muted">为 <b>{{ elders[0].name }}</b> 预约（{{ elders[0].disability_label }}）</div>

      <div class="field"><span>服务项目</span></div>
      <div class="elder-tiles">
        <button v-for="i in items" :key="i.id" type="button" class="tile" :style="form.item_id === i.id ? 'border-color: var(--pine-700); background: var(--pine-50)' : ''" @click="form.item_id = i.id">
          {{ i.name }}
          <small>¥{{ yuan(i.unit_price) }} / {{ i.pricing_label }}，约 {{ i.standard_minutes }} 分钟</small>
        </button>
      </div>
      <div v-if="currentItem && !currentItem.applicable_levels.includes(elders.find((e) => e.id === form.elder_id)?.disability_level)" class="notice warn">
        「{{ currentItem.name }}」适用于{{ currentItem.applicable_levels.map((l) => label('DisabilityLevel', l)).join('、') }}，提交后将转人工受理。
      </div>

      <label class="checks"><label><input v-model="weekly.enabled" type="checkbox" /> 每周固定</label></label>
      <div v-if="weekly.enabled" class="form-grid">
        <label class="field"><span>星期</span><select v-model="weekly.weekday"><option v-for="(w, i) in WEEKDAYS" :key="w" :value="i">{{ w }}</option></select></label>
        <label class="field"><span>时间</span><input v-model="weekly.time" type="time" /></label>
      </div>
      <label v-else class="field"><span>期望上门时间</span><input v-model="form.scheduled_start" type="datetime-local" /></label>
      <label class="field"><span>备注（选填）</span><textarea v-model="form.remark" maxlength="255" placeholder="比如：腿脚不便，请提前联系"></textarea></label>
      <button class="btn primary lg" @click="submit">提交预约</button>
    </section>

    <section v-if="plans.length" class="card">
      <h2 class="mb">我的周期预约</h2>
      <div v-for="p in plans" :key="p.id" class="row between mb">
        <span>{{ p.weekday_label }} {{ p.time_of_day }} {{ p.item_name }}（{{ p.elder_name }}）</span>
        <button class="btn sm" @click="stopPlan(p)">停止</button>
      </div>
    </section>
  </div>
</template>
