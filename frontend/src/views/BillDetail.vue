<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import Pager from '../components/Pager.vue'
import { can, run, toast } from '../store'
import { dt, money, pct, statusColor } from '../utils'

const route = useRoute()
const id = Number(route.params.id)
const bill = ref(null)
const page = ref(1)
const resolve = reactive({ target: null, resolution: '', explanation: '' })
const review = reactive({ show: false, note: '' })
const sample = reactive({ target: null, passed: true, note: '', photos: [] })

const RESOLUTIONS = {
  DUPLICATE: [['EXCLUDE', '剔除重复'], ['APPROVE', '核实无误，照常计费']],
  OVER_CAP: [['CLIP', '截断至上限，超出转个人自付'], ['EXCLUDE', '剔除']],
  NOT_COMPLETED: [['EXCLUDE', '剔除']],
  DURATION_ABNORMAL: [['APPROVE', '核实无误'], ['EXCLUDE', '剔除']],
  SAMPLE_FAILED: [['APPROVE', '整改后放行'], ['EXCLUDE', '剔除']],
}
const FLOW = [['DRAFT', '差异处理'], ['NEEDS_REVIEW', '人工复核'], ['GRID_CONFIRMED', '网格核对'], ['ORG_CONFIRMED', '机构确认 / 抽查'], ['EFFECTIVE', '生效']]
const openCount = computed(() => bill.value?.discrepancies.filter((d) => d.status === 'OPEN').length ?? 0)

async function load() { bill.value = await api.get(`/bills/${id}`, { page: page.value, size: 30 }) }

function startResolve(row) {
  resolve.target = row
  resolve.resolution = RESOLUTIONS[row.kind][0][0]
  resolve.explanation = ''
}

async function submitResolve() {
  if (!resolve.explanation.trim()) {
    toast('请填写处理说明', 'error')
    return
  }
  if (await run(() => api.post(`/discrepancies/${resolve.target.id}/resolve`, { resolution: resolve.resolution, explanation: resolve.explanation }), '差异已处理')) {
    resolve.target = null
    load()
  }
}

async function act(path, message, body) {
  if (await run(() => api.post(`/bills/${id}/${path}`, body), message)) load()
}

async function submitReview() {
  if (await run(() => api.post(`/bills/${id}/review`, { note: review.note }), '人工复核完成，可以提交')) {
    review.show = false
    load()
  }
}

async function openSample(row) {
  Object.assign(sample, { target: row, passed: true, note: '', photos: [] })
  sample.photos = await Promise.all((row.evidence.photo_ids || []).map((pid) => api.blobUrl(`/photos/${pid}`).catch(() => null)))
}

async function submitSample() {
  if (await run(() => api.post(`/samples/${sample.target.id}/check`, { passed: sample.passed, note: sample.note || null }), '抽查结果已记录')) {
    sample.target = null
    load()
  }
}

const exportBill = (fmt) => run(() => api.download(`/bills/${id}/export?fmt=${fmt}`, `账单.${fmt}`), '已导出，导出操作已写入审计日志')

watch(page, load)
onMounted(load)
</script>

<template>
  <div v-if="bill" class="col">
    <div class="page-head">
      <h1>{{ bill.community_name }} {{ bill.month }} 账单</h1>
      <span class="tag" :class="statusColor(bill.status)">{{ bill.status_label }}</span>
      <span class="grow"></span>
      <button v-if="can('billing:export')" class="btn" @click="exportBill('xlsx')">导出 Excel</button>
      <button v-if="can('billing:export')" class="btn" @click="exportBill('csv')">导出 CSV</button>
      <button v-if="bill.status === 'NEEDS_REVIEW' && can('billing:review')" class="btn primary" @click="review.show = true">人工复核</button>
      <button v-if="bill.status === 'DRAFT' && can('billing:grid_confirm')" class="btn primary" @click="act('grid-confirm', '已核对，转机构确认')">网格员核对提交</button>
      <button v-if="bill.status === 'GRID_CONFIRMED' && can('billing:org_confirm')" class="btn primary" @click="act('org-confirm', '已确认')">机构确认</button>
    </div>

    <div class="card">
      <div class="row small" style="gap: 6px">
        <template v-for="([code, text], i) in FLOW" :key="code">
          <span class="tag" :class="bill.status === code ? 'brand' : ''">{{ text }}</span>
          <span v-if="i < FLOW.length - 1" class="muted">›</span>
        </template>
      </div>
    </div>

    <div class="stats">
      <div class="stat"><span class="label">账单总额</span><span class="value">{{ money(bill.total_amount) }}</span></div>
      <div class="stat"><span class="label">政府补贴</span><span class="value">{{ money(bill.gov_amount) }}</span></div>
      <div class="stat"><span class="label">长护险支付</span><span class="value">{{ money(bill.ltci_amount) }}</span></div>
      <div class="stat"><span class="label">个人自付</span><span class="value">{{ money(bill.self_amount) }}</span></div>
      <div class="stat"><span class="label">已拦截金额</span><span class="value">{{ money(bill.intercepted_amount) }}</span></div>
      <div class="stat"><span class="label">差异比例（阈值 1%）</span><span class="value" :class="{ 'level-RED': bill.needs_review }">{{ pct(bill.discrepancy_ratio, 2) }}</span></div>
    </div>

    <div v-if="bill.needs_review && !bill.reviewed_at" class="notice error">差异金额占账单总额超过 1%，须处理全部差异并完成人工复核后才能提交。</div>
    <div v-if="bill.review_note" class="notice">人工复核意见：{{ bill.review_note }}</div>

    <section class="card">
      <h2 class="mb">对账差异清单（待处理 {{ openCount }} / 共 {{ bill.discrepancies.length }}）</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>类型</th><th>工单</th><th>老人</th><th class="right">金额</th><th>说明</th><th>状态</th><th>处理</th><th></th></tr></thead>
          <tbody>
            <tr v-for="d in bill.discrepancies" :key="d.id" :class="{ 'flag-yellow': d.status === 'OPEN' }">
              <td><span class="tag" :class="d.status === 'OPEN' ? 'red' : ''">{{ d.kind_label }}</span></td>
              <td class="num small">{{ d.order_no }}</td><td>{{ d.elder_name }}</td>
              <td class="num right">{{ money(d.amount) }}</td><td class="small" style="max-width: 320px">{{ d.detail }}</td>
              <td><span class="tag" :class="statusColor(d.status)">{{ d.status_label }}</span></td>
              <td class="small">
                <template v-if="d.status === 'RESOLVED'">{{ d.resolution_label }}　{{ d.handled_by }}<div class="muted">{{ d.explanation }}</div></template>
              </td>
              <td class="right"><button v-if="d.status === 'OPEN' && can('billing:discrepancy') && ['DRAFT', 'NEEDS_REVIEW'].includes(bill.status)" class="btn sm primary" @click="startResolve(d)">处理</button></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!bill.discrepancies.length" class="empty">没有差异，全部明细通过校验</div>
      </div>
    </section>

    <section v-if="bill.samples.length" class="card">
      <h2 class="mb">监管抽查（{{ bill.samples.length }} 单，随机种子 {{ bill.sample_seed }}）</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>工单</th><th>服务</th><th>护理员</th><th>签到</th><th>时长</th><th>结果</th><th></th></tr></thead>
          <tbody>
            <tr v-for="s in bill.samples" :key="s.id">
              <td class="num small"><router-link :to="`/orders/${s.order_id}`">{{ s.evidence.order_no }}</router-link></td>
              <td>{{ s.evidence.item }}</td><td>{{ s.evidence.caregiver }}</td>
              <td class="num">{{ dt(s.evidence.checkin_at) }}</td>
              <td class="num">{{ s.evidence.duration_minutes }} / {{ s.evidence.standard_minutes }} 分钟</td>
              <td><span class="tag" :class="statusColor(s.result)">{{ s.result_label }}</span></td>
              <td class="right"><button v-if="s.result === 'PENDING' && can('billing:sample')" class="btn sm primary" @click="openSample(s)">核查</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <h2 class="mb">费用明细</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>工单</th><th>老人</th><th>类型</th><th>项目</th><th class="right">单价</th><th class="right">数量</th><th class="right">金额</th><th class="right">补贴</th><th class="right">长护险</th><th class="right">自付</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="f in bill.fees" :key="f.id">
              <td class="num small">{{ f.order_no }}</td><td>{{ f.elder_name }}</td><td class="small">{{ f.fee_type_label }}</td><td>{{ f.item_name }}</td>
              <td class="num right">{{ money(f.unit_price) }}</td><td class="num right">{{ f.quantity }}</td><td class="num right">{{ money(f.amount) }}</td>
              <td class="num right">{{ money(f.gov_amount) }}</td><td class="num right">{{ money(f.ltci_amount) }}</td><td class="num right">{{ money(f.self_amount) }}</td>
              <td><span v-if="f.excluded" class="tag">已剔除</span><span v-else-if="f.intercepted" class="tag red">已拦截</span><span v-else class="tag green">正常</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <Pager v-model:page="page" :size="30" :total="bill.fee_total" />
    </section>

    <Modal v-if="resolve.target" title="处理差异" @close="resolve.target = null">
      <div class="col">
        <div class="notice warn"><b>{{ resolve.target.kind_label }}</b>：{{ resolve.target.detail }}</div>
        <div class="checks">
          <label v-for="[code, text] in RESOLUTIONS[resolve.target.kind]" :key="code"><input v-model="resolve.resolution" type="radio" :value="code" /> {{ text }}</label>
        </div>
        <label class="field"><span>说明<i>*</i></span><textarea v-model="resolve.explanation"></textarea></label>
      </div>
      <template #foot><button class="btn primary" @click="submitResolve">保存</button></template>
    </Modal>

    <Modal v-if="review.show" title="人工复核" @close="review.show = false">
      <div class="notice mb">差异比例 {{ pct(bill.discrepancy_ratio, 2) }}，差异金额 {{ money(bill.discrepancy_amount) }}。待处理差异 {{ openCount }} 条。</div>
      <label class="field"><span>复核意见<i>*</i></span><textarea v-model="review.note"></textarea></label>
      <template #foot><button class="btn primary" :disabled="!review.note.trim()" @click="submitReview">完成复核</button></template>
    </Modal>

    <Modal v-if="sample.target" title="抽查工单留痕" wide @close="sample.target = null">
      <div class="grid g2">
        <dl class="kv">
          <dt>工单</dt><dd class="num">{{ sample.target.evidence.order_no }}</dd>
          <dt>护理员</dt><dd>{{ sample.target.evidence.caregiver }}</dd>
          <dt>签到</dt><dd class="num">{{ dt(sample.target.evidence.checkin_at) }}，距服务地址 {{ sample.target.evidence.checkin_distance_m }} 米</dd>
          <dt>签退</dt><dd class="num">{{ dt(sample.target.evidence.checkout_at) }}</dd>
          <dt>时长</dt><dd class="num">{{ sample.target.evidence.duration_minutes }} / {{ sample.target.evidence.standard_minutes }} 分钟</dd>
          <dt>小结</dt><dd>{{ sample.target.evidence.summary }}</dd>
        </dl>
        <div>
          <div class="row"><span v-for="c in sample.target.evidence.checklist" :key="c.name" class="tag" :class="c.done ? 'green' : 'red'">{{ c.name }}</span></div>
          <div class="row mt"><img v-for="(u, i) in sample.photos" :key="i" :src="u" class="photo" alt="现场照片" /></div>
        </div>
      </div>
      <div class="col mt">
        <div class="checks">
          <label><input v-model="sample.passed" type="radio" :value="true" /> 通过</label>
          <label><input v-model="sample.passed" type="radio" :value="false" /> 不通过（账单退回，明细转入差异清单）</label>
        </div>
        <label class="field"><span>说明<i v-if="!sample.passed">*</i></span><textarea v-model="sample.note"></textarea></label>
      </div>
      <template #foot><button class="btn primary" @click="submitSample">提交抽查结果</button></template>
    </Modal>
  </div>
</template>
