<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, query } from '../api'
import EChart from '../components/EChart.vue'
import { can, run } from '../store'
import { daysAgo, money, pct, serverNow, today } from '../utils'

const route = useRoute()
const kind = computed(() => route.params.kind)
const range = reactive({ preset: '30d', start: daysAgo(29), end: today(), granularity: '' })
const data = ref(null)
// 分类色固定顺序，已通过色盲可分性校验；超过 6 类时小项并入“其他”
const PALETTE = ['#2F6FC2', '#D97A1C', '#1E9C8F', '#B25BC4', '#6E9A2F', '#DB5C86']
const TITLES = { community: '社区看板', org: '机构看板', street: '街道看板' }

function setPreset(preset) {
  range.preset = preset
  const now = serverNow()
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  if (preset === '7d') Object.assign(range, { start: daysAgo(6), end: today(), granularity: 'day' })
  if (preset === '30d') Object.assign(range, { start: daysAgo(29), end: today(), granularity: 'day' })
  if (preset === 'month') Object.assign(range, { start: iso(new Date(now.getFullYear(), now.getMonth(), 1)), end: today(), granularity: 'day' })
  if (preset === 'lastMonth') Object.assign(range, { start: iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), end: iso(new Date(now.getFullYear(), now.getMonth(), 0)), granularity: 'week' })
  if (preset === 'year') Object.assign(range, { start: daysAgo(364), end: today(), granularity: 'month' })
  load()
}

async function load() {
  data.value = await api.get(`/stats/dashboard/${kind.value}`, { start: range.start, end: range.end, granularity: range.granularity })
}

const axis = { axisLabel: { color: '#6f7b89' }, axisLine: { lineStyle: { color: '#e3e8ef' } }, splitLine: { lineStyle: { color: '#eef1f5' } } }

const trendOption = computed(() => {
  const chart = data.value?.charts.trend
  if (!chart) return null
  return {
    color: PALETTE, tooltip: { trigger: 'axis' }, legend: chart.series.length > 1 ? { top: 0, textStyle: { color: '#4b5765' } } : { show: false },
    grid: { left: 50, right: 16, top: 32, bottom: 28 },
    xAxis: { type: 'category', data: chart.x, ...axis, axisLabel: { color: '#6f7b89', hideOverlap: true } },
    yAxis: { type: 'value', minInterval: 1, ...axis },
    series: chart.series.map((s) => ({ name: s.name, type: 'line', data: s.data, showSymbol: chart.x.length < 20, symbolSize: 8, smooth: false, lineStyle: { width: 2 } })),
  }
})
const shareOption = computed(() => {
  const chart = data.value?.charts.share
  if (!chart) return null
  const rows = chart.data.length > 6
    ? [...chart.data.slice(0, 5), { name: '其他', value: Number(chart.data.slice(5).reduce((sum, d) => sum + d.value, 0).toFixed(2)) }]
    : chart.data
  return {
    color: PALETTE, tooltip: { trigger: 'item', formatter: '{b}<br/>{c}（{d}%）' },
    legend: { type: 'scroll', orient: 'vertical', right: 0, top: 'middle', textStyle: { color: '#4b5765' } },
    series: [{
      type: 'pie', radius: ['48%', '74%'], center: ['34%', '50%'], label: { show: false },
      itemStyle: { borderColor: '#fff', borderWidth: 2 }, data: rows.map((d) => ({ name: d.name, value: d.value })),
    }],
  }
})
const rankingOption = computed(() => {
  const chart = data.value?.charts.ranking
  if (!chart) return null
  const rows = [...chart.data].reverse()
  return {
    color: ['#2F6FC2'], tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, grid: { left: 92, right: 34, top: 8, bottom: 24 },
    xAxis: { type: 'value', ...axis }, yAxis: { type: 'category', data: rows.map((r) => r.name), axisLabel: { color: '#4b5765' }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{ type: 'bar', data: rows.map((r) => r.value), barMaxWidth: 16, label: { show: true, position: 'right', color: '#4b5765' }, itemStyle: { borderRadius: [0, 4, 4, 0] } }],
  }
})
const LEVEL_COLOR = { RED: '#c62f2b', YELLOW: '#d9a400', BLUE: '#0a7e9c' }
const distributionOption = computed(() => {
  const chart = data.value?.charts.distribution
  if (!chart) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, grid: { left: 40, right: 16, top: 26, bottom: 28 },
    xAxis: { type: 'category', data: chart.data.map((d) => d.name), ...axis }, yAxis: { type: 'value', ...axis, minInterval: 1 },
    series: [{
      type: 'bar', barMaxWidth: 46, label: { show: true, position: 'top', color: '#4b5765' },
      data: chart.data.map((d) => ({ value: d.value, itemStyle: { color: LEVEL_COLOR[d.level] || '#2F6FC2', borderRadius: [4, 4, 0, 0] } })),
    }],
  }
})

function formatKpi(k) {
  if (k.value === null || k.value === undefined) return '—'
  if (k.unit === 'cents') return money(k.value)
  if (k.unit === 'ratio') return pct(k.value)
  return k.value
}
const unitOf = (k) => (['cents', 'ratio'].includes(k.unit) ? '' : k.unit)

function deltaClass(k, value) {
  if (value === null || value === undefined || value === 0) return ''
  const good = k.lower_better ? value < 0 : value > 0
  return good ? 'up' : 'down'
}
const deltaText = (value) => (value === null || value === undefined ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(1)}%`)

const exportFile = (fmt) => run(
  () => api.download(`/stats/dashboard/${kind.value}/export${query({ start: range.start, end: range.end, granularity: range.granularity, fmt })}`, `统计.${fmt}`),
  '已导出，导出操作已写入审计日志',
)

watch(kind, load)
onMounted(() => setPreset('30d'))
</script>

<template>
  <div v-if="data" class="col" data-speak>
    <div class="page-head">
      <h1>{{ TITLES[kind] }}</h1>
      <span class="grow"></span>
      <div class="row">
        <button v-for="[k, t] in [['7d', '近 7 天'], ['30d', '近 30 天'], ['month', '本月'], ['lastMonth', '上月'], ['year', '近一年']]" :key="k" class="btn sm" :class="{ primary: range.preset === k }" @click="setPreset(k)">{{ t }}</button>
      </div>
      <input v-model="range.start" type="date" style="width: 150px" @change="range.preset = ''; load()" />
      <span class="muted">至</span>
      <input v-model="range.end" type="date" style="width: 150px" @change="range.preset = ''; load()" />
      <select v-model="range.granularity" style="width: 90px" @change="load"><option value="">自动</option><option value="day">按日</option><option value="week">按周</option><option value="month">按月</option></select>
      <button v-if="can('stats:export')" class="btn sm" @click="exportFile('xlsx')">导出 Excel</button>
      <button v-if="can('stats:export')" class="btn sm" @click="exportFile('csv')">导出 CSV</button>
    </div>

    <div class="stats">
      <div v-for="k in data.kpis" :key="k.name" class="stat">
        <span class="label">{{ k.name }}</span>
        <span class="value" >{{ formatKpi(k) }}<small v-if="unitOf(k) && k.value !== null">{{ unitOf(k) }}</small></span>
        <span class="delta">
          <span>环比 <b :class="deltaClass(k, k.mom)">{{ deltaText(k.mom) }}</b></span>
          <span>同比 <b :class="deltaClass(k, k.yoy)">{{ deltaText(k.yoy) }}</b></span>
        </span>
        <span v-if="k.hint" class="small muted">{{ k.hint }}</span>
      </div>
    </div>

    <section><h2 class="mb">{{ data.charts.trend.title }}</h2><EChart :option="trendOption" height="300px" /></section>
    <div class="grid g3">
      <section><h2 class="mb">{{ data.charts.share.title }}</h2><EChart :option="shareOption" height="260px" /></section>
      <section><h2 class="mb">{{ data.charts.ranking.title }}</h2><EChart :option="rankingOption" height="260px" /></section>
      <section><h2 class="mb">{{ data.charts.distribution.title }}</h2><EChart :option="distributionOption" height="260px" /></section>
    </div>

    <section v-if="data.alert_timeliness" class="card">
      <h2 class="mb">预警处置时效</h2>
      <div class="row" style="gap: 28px">
        <span>预警总数 <b class="num">{{ data.alert_timeliness.total }}</b></span>
        <span>响应中位数 <b class="num">{{ data.alert_timeliness.median_response_minutes ?? '—' }}</b> 分钟</span>
        <span>红色响应中位数 <b class="num">{{ data.alert_timeliness.red_median_response_minutes ?? '—' }}</b> 分钟</span>
        <span>P90 <b class="num">{{ data.alert_timeliness.p90_response_minutes ?? '—' }}</b> 分钟</span>
        <span>及时率 <b class="num">{{ pct(data.alert_timeliness.on_time_rate) }}</b></span>
        <span>闭环率 <b class="num">{{ pct(data.alert_timeliness.close_rate) }}</b></span>
        <span>红色 5 分钟推送率 <b class="num">{{ pct(data.alert_timeliness.red_push_on_time_rate) }}</b></span>
      </div>
    </section>

    <section v-if="data.performance" class="card">
      <h3 class="mb">护理员绩效（{{ range.end.slice(0, 7) }}）</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>名次</th><th>护理员</th><th class="right">完成量</th><th class="right">评分</th><th class="right">准时率</th><th class="right">异常率</th><th class="right">投诉成立</th></tr></thead>
          <tbody>
            <tr v-for="r in data.performance" :key="r.caregiver_id">
              <td class="num">{{ r.rank }}</td><td>{{ r.name }} <span v-if="r.key_attention" class="tag red">重点关注</span></td><td class="num right">{{ r.completed }}</td>
              <td class="num right">{{ r.avg_rating ?? '—' }}</td><td class="num right">{{ pct(r.on_time_rate) }}</td><td class="num right">{{ pct(r.exception_rate) }}</td><td class="num right">{{ r.complaints_upheld || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="data.community_scores" class="card">
      <h3 class="mb">各社区满意度</h3>
      <div class="row" style="gap: 28px">
        <span v-for="c in data.community_scores" :key="c.name">{{ c.name }} <b class="num">{{ c.value }}</b> 星 <span class="muted small">（{{ c.count }} 条）</span></span>
      </div>
    </section>

    <p class="small muted">
      环比基期 {{ data.range.previous.start }} 至 {{ data.range.previous.end }}，同比基期 {{ data.range.year_ago.start }} 至 {{ data.range.year_ago.end }}。基期为 0 或无数据时显示“—”。
    </p>
  </div>
</template>
