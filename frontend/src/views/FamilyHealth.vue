<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import HealthPanel from '../components/HealthPanel.vue'
import { dt } from '../utils'

const elders = ref([])
const elderId = ref(null)
const alerts = ref([])

onMounted(async () => {
  elders.value = (await api.get('/elders')).items
  elderId.value = elders.value[0]?.id
  alerts.value = (await api.get('/alerts', { size: 10 })).items
})
</script>

<template>
  <div class="col" style="max-width: 860px" data-speak>
    <h1>健康记录</h1>
    <select v-if="elders.length > 1" v-model.number="elderId" style="width: 200px">
      <option v-for="e in elders" :key="e.id" :value="e.id">{{ e.name }}</option>
    </select>
    <HealthPanel v-if="elderId" :key="elderId" :elder-id="elderId" />
    <section class="card">
      <h2 class="mb">预警记录</h2>
      <div v-if="!alerts.length" class="muted">没有预警记录</div>
      <div v-for="a in alerts" :key="a.id" class="alert-card mb" :class="a.level">
        <div class="row between"><b>{{ a.level_label }}预警：{{ a.title }}</b><span class="tag">{{ a.status_label }}</span></div>
        <div class="small">{{ a.message }}</div>
        <div class="small muted">{{ dt(a.triggered_at) }}<template v-if="a.conclusion_label && a.conclusion">　结论：{{ a.conclusion_label }}</template></div>
      </div>
    </section>
  </div>
</template>
