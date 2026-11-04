<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { run, toast } from '../store'
import { dt } from '../utils'

const data = ref({ jobs: [], runs: [] })

async function load() { data.value = await api.get('/admin/jobs') }

function describe(trigger) {
  if (trigger.trigger === 'interval') {
    if (trigger.seconds) return `每 ${trigger.seconds} 秒`
    return `每 ${trigger.minutes} 分钟`
  }
  const time = `${String(trigger.hour).padStart(2, '0')}:${String(trigger.minute).padStart(2, '0')}`
  return trigger.day ? `每月 ${trigger.day} 日 ${time}` : `每日 ${time}`
}

async function trigger(job) {
  const result = await run(() => api.post(`/admin/jobs/${job.name}/run`))
  if (result) {
    toast(result.ok ? `${job.title}：处理 ${result.processed} 项` : `执行失败：${result.message}`, result.ok ? 'info' : 'error')
    load()
  }
}

onMounted(load)
</script>

<template>
  <div class="grid g2">
    <section class="card">
      <h2 class="mb">任务列表</h2>
      <table>
        <tbody>
          <tr v-for="j in data.jobs" :key="j.name">
            <td><b>{{ j.title }}</b><div class="small muted num">{{ j.name }}</div></td>
            <td class="small">{{ describe(j.trigger) }}</td>
            <td class="right"><button class="btn sm" @click="trigger(j)">立即执行</button></td>
          </tr>
        </tbody>
      </table>
    </section>
    <section class="card">
      <h2 class="mb">最近运行（有处理量或失败才记录）</h2>
      <table>
        <thead><tr><th>时间</th><th>任务</th><th class="right">处理量</th><th class="right">耗时</th><th>结果</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in data.runs" :key="i">
            <td class="num small nowrap">{{ dt(r.started_at) }}</td><td class="small">{{ r.title }}</td><td class="num right">{{ r.processed }}</td><td class="num right small">{{ r.duration_ms }} ms</td>
            <td><span class="tag" :class="r.ok ? 'green' : 'red'" :title="r.message">{{ r.ok ? '成功' : '失败' }}</span></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!data.runs.length" class="empty">暂无记录</div>
    </section>
  </div>
</template>
