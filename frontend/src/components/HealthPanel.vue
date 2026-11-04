<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import EChart from './EChart.vue'

const props = defineProps({ elderId: Number })
const data = ref(null)
const summary = ref(null)
const days = ref(30)
const selected = ref('SYSTOLIC')

const THRESHOLDS = { SYSTOLIC: [90, 180], HEART_RATE: [40, 100], SPO2: [90], GLUCOSE: [3.9, 16.7] }

async function load() {
  data.value = await api.get(`/elders/${props.elderId}/health`, { days: days.value })
  summary.value = await api.get(`/ai/elders/${props.elderId}/summary`).catch(() => null)
  const names = Object.keys(data.value.summary)
  if (names.length && !names.includes(selected.value)) selected.value = names[0]
}

const option = computed(() => {
  const points = data.value?.series?.[selected.value] || []
  const info = data.value?.summary?.[selected.value]
  const lines = (THRESHOLDS[selected.value] || []).map((y) => ({ yAxis: y, label: { formatter: `阈值 ${y}` } }))
  return {
    grid: { left: 44, right: 24, top: 24, bottom: 30 },
    tooltip: { trigger: 'axis', formatter: (ps) => `${ps[0].axisValueLabel}<br/>${info?.label ?? ''}：<b>${ps[0].value[1]}</b> ${info?.unit ?? ''}` },
    xAxis: { type: 'time', axisLabel: { color: '#7c8885' } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#7c8885' }, splitLine: { lineStyle: { color: '#efece3' } } },
    series: [{
      type: 'line', showSymbol: points.length < 40, symbolSize: 6, smooth: false, lineStyle: { color: '#1f5e57', width: 2 },
      itemStyle: { color: '#1f5e57' }, data: points.map((p) => [p.t, p.v]),
      markLine: { silent: true, symbol: 'none', lineStyle: { color: '#c0352b', type: 'dashed' }, data: lines },
    }],
  }
})

onMounted(load)
watch([days], load)
</script>

<template>
  <div v-if="data" class="col">
    <div v-if="summary" class="card">
      <div class="row between mb">
        <h3>健康摘要 <span class="tag pine">系统生成</span></h3>
        <span v-if="summary.risk" class="small">巡访参考分 <b class="num">{{ summary.risk.score }}</b>（{{ summary.risk.tier }}）</span>
      </div>
      <p class="pre" style="margin: 0 0 8px">{{ summary.summary }}</p>
      <div v-for="s in summary.suggestions" :key="s" class="notice">{{ s }}</div>
      <div v-if="summary.risk?.factors?.length" class="row mt small">
        <span class="muted">依据：</span>
        <span v-for="f in summary.risk.factors" :key="f.name" class="tag" :title="f.detail">{{ f.name }} +{{ f.points }}</span>
      </div>
      <div class="small muted mt">{{ summary.disclaimer }}</div>
    </div>

    <div class="card">
      <div class="row mb">
        <button v-for="(info, key) in data.summary" :key="key" class="btn sm" :class="{ primary: selected === key }" @click="selected = key">{{ info.label }}</button>
        <span class="grow"></span>
        <select v-model.number="days" style="width: 110px"><option :value="7">近 7 天</option><option :value="30">近 30 天</option><option :value="90">近 90 天</option></select>
      </div>
      <div v-if="data.summary[selected]" class="row small muted mb">
        最新 <b class="num" style="color: var(--ink)">{{ data.summary[selected].latest }}</b> {{ data.summary[selected].unit }}
        · 平均 {{ data.summary[selected].avg }} · 最低 {{ data.summary[selected].min }} · 最高 {{ data.summary[selected].max }} · 共 {{ data.summary[selected].count }} 次
      </div>
      <div v-if="!Object.keys(data.summary).length" class="empty">暂无数据</div>
      <EChart v-else :option="option" height="260px" />
    </div>
  </div>
</template>
