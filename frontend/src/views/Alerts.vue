<script setup>
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import Pager from '../components/Pager.vue'
import { can, run, toast } from '../store'
import { dt, levelColor, serverNow, statusColor, toLocalInput } from '../utils'

const filters = reactive({ only_open: true, level: '', escalated: false, page: 1, size: 20 })
const data = ref({ items: [], total: 0 })
const detail = ref(null)
const respondNote = ref('')
const closeForm = reactive({ show: false, conclusion: 'HANDLED', measures: '', false_reason: '', hospital: '', referred_at: '' })

const KIND = { TRIGGER: '触发', REPEAT: '再次触发', PUSH: '推送', RESPOND: '响应', ESCALATE: '升级', CLOSE: '闭环' }
const handle = can('alert:handle')

async function load() { data.value = await api.get('/alerts', filters) }
async function open(alert) {
  detail.value = await api.get(`/alerts/${alert.id}`)
  respondNote.value = ''
}

async function respond() {
  if (!respondNote.value.trim()) {
    toast('请填写处置记录', 'error')
    return
  }
  if (await run(() => api.post(`/alerts/${detail.value.id}/respond`, { note: respondNote.value }), '已记录响应')) {
    await open(detail.value)
    load()
  }
}

function startClose() {
  Object.assign(closeForm, { show: true, conclusion: 'HANDLED', measures: '', false_reason: '', hospital: '', referred_at: toLocalInput(serverNow()) })
}

async function close() {
  const body = { conclusion: closeForm.conclusion }
  if (closeForm.conclusion === 'HANDLED') body.measures = closeForm.measures
  if (closeForm.conclusion === 'FALSE_ALARM') body.false_reason = closeForm.false_reason
  if (closeForm.conclusion === 'REFERRED') {
    body.hospital = closeForm.hospital
    body.referred_at = closeForm.referred_at ? `${closeForm.referred_at}:00` : null
  }
  const result = await run(() => api.post(`/alerts/${detail.value.id}/close`, body))
  if (result) {
    toast(`预警已闭环，处置时长 ${result.handle_minutes} 分钟`)
    closeForm.show = false
    detail.value = null
    load()
  }
}

const refresh = () => load()
watch(() => [filters.only_open, filters.level, filters.escalated, filters.page], load)
onMounted(() => { load(); window.addEventListener('yihu:refresh', refresh) })
onUnmounted(() => window.removeEventListener('yihu:refresh', refresh))
</script>

<template>
  <div class="col">
    <div class="filters">
      <label class="field"><span>级别</span>
        <select v-model="filters.level"><option value="">全部</option><option value="RED">红色</option><option value="YELLOW">黄色</option><option value="BLUE">蓝色</option></select>
      </label>
      <div class="checks" style="padding-bottom: 8px">
        <label><input v-model="filters.only_open" type="checkbox" /> 只看未闭环</label>
        <label><input v-model="filters.escalated" type="checkbox" /> 只看已升级</label>
      </div>
    </div>

    <div v-if="!data.items.length" class="card empty">当前没有符合条件的预警</div>
    <article v-for="a in data.items" :key="a.id" class="alert-card" :class="[a.level, { pulse: a.level === 'RED' && a.status === 'OPEN' }]" style="cursor: pointer" @click="open(a)">
      <div class="row between">
        <div class="row">
          <span class="tag" :class="levelColor(a.level)">{{ a.level_label }}</span>
          <b>{{ a.elder_name }}</b><span class="muted small">{{ a.grid_name }}</span>
          <span>{{ a.title }}</span>
          <span class="tag">{{ a.rule_type_label }}</span>
          <span v-if="a.occurrences > 1" class="tag">{{ a.occurrences }} 次</span>
          <span v-if="a.escalated_at" class="tag red">已升级</span>
        </div>
        <div class="row">
          <span v-if="a.open_minutes !== null" class="num small" :class="{ 'level-RED': a.level === 'RED' && a.open_minutes >= 20 }">已等待 {{ a.open_minutes }} 分钟</span>
          <span class="tag" :class="statusColor(a.status)">{{ a.status_label }}</span>
        </div>
      </div>
      <div class="small" style="color: var(--ink-2); margin-top: 4px">{{ a.message }}</div>
      <div class="small muted num">{{ dt(a.triggered_at) }} · {{ a.alert_no }}<template v-if="a.conclusion"> · {{ a.conclusion_label }} · {{ a.closed_by }} · 处置 {{ a.handle_minutes }} 分钟</template></div>
    </article>
    <Pager v-model:page="filters.page" :size="filters.size" :total="data.total" />

    <Modal v-if="detail" :title="`${detail.level_label}预警：${detail.title}`" wide @close="detail = null">
      <div class="grid g2">
        <div>
          <dl class="kv">
            <dt>老人</dt><dd>{{ detail.elder_name }}（{{ detail.elder_code }}）· {{ detail.grid_name }}</dd>
            <dt>预警内容</dt><dd>{{ detail.message }}</dd>
            <dt>规则类型</dt><dd>{{ detail.rule_type_label }}</dd>
            <dt>触发时间</dt><dd class="num">{{ dt(detail.triggered_at) }}</dd>
            <dt>状态</dt><dd><span class="tag" :class="statusColor(detail.status)">{{ detail.status_label }}</span></dd>
            <dt v-if="detail.conclusion">结论</dt><dd v-if="detail.conclusion">{{ detail.conclusion_label }}：{{ detail.measures || detail.false_reason || detail.hospital }}</dd>
          </dl>
          <template v-if="handle && detail.status !== 'CLOSED'">
            <label v-if="detail.status === 'OPEN'" class="field mt"><span>到场或电话确认情况<i>*</i></span><textarea v-model="respondNote"></textarea></label>
            <div class="row mt">
              <button v-if="detail.status === 'OPEN'" class="btn" @click="respond">记录响应</button>
              <button class="btn primary" @click="startClose">闭环预警</button>
            </div>
          </template>
        </div>
        <div>
          <h3 class="mb">处置时间线</h3>
          <ul class="timeline">
            <li v-for="(e, i) in detail.events" :key="i">
              <div class="when num">{{ dt(e.at) }} · {{ e.actor }}</div>
              <div><b>{{ KIND[e.kind] || e.kind }}</b> <span class="small">{{ e.detail }}</span></div>
            </li>
          </ul>
        </div>
      </div>
    </Modal>

    <Modal v-if="closeForm.show" title="闭环预警" @close="closeForm.show = false">
      <div class="col">
        <div class="checks">
          <label><input v-model="closeForm.conclusion" type="radio" value="HANDLED" /> 已处置</label>
          <label><input v-model="closeForm.conclusion" type="radio" value="FALSE_ALARM" /> 误报</label>
          <label><input v-model="closeForm.conclusion" type="radio" value="REFERRED" /> 已转诊</label>
        </div>
        <label v-if="closeForm.conclusion === 'HANDLED'" class="field"><span>处置措施<i>*</i></span><textarea v-model="closeForm.measures"></textarea></label>
        <label v-if="closeForm.conclusion === 'FALSE_ALARM'" class="field"><span>误报原因<i>*</i></span><input v-model="closeForm.false_reason" placeholder="比如：设备脱落" /></label>
        <template v-if="closeForm.conclusion === 'REFERRED'">
          <label class="field"><span>转诊医院<i>*</i></span><input v-model="closeForm.hospital" /></label>
          <label class="field"><span>转诊时间<i>*</i></span><input v-model="closeForm.referred_at" type="datetime-local" /></label>
        </template>
      </div>
      <template #foot><button class="btn" @click="closeForm.show = false">取消</button><button class="btn primary" @click="close">确认闭环</button></template>
    </Modal>
  </div>
</template>
