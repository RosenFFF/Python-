<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { money, statusColor } from '../utils'

const rows = ref([])
onMounted(async () => { rows.value = await api.get('/statements') })
</script>

<template>
  <div class="mobile col" data-speak>
    <h1>我的账单</h1>
    <div v-if="!rows.length" class="empty card">暂无已生效的账单</div>
    <section v-for="s in rows" :key="s.id" class="card">
      <div class="row between mb">
        <h2>{{ s.month }}<span v-if="s.elder_name" class="muted small">（{{ s.elder_name }}）</span></h2>
        <span class="tag" :class="statusColor(s.payment_status)">{{ s.payment_label }}</span>
      </div>
      <dl class="kv">
        <dt>服务总额</dt><dd class="num">{{ money(s.total_amount) }}</dd>
        <dt>政府补贴</dt><dd class="num">{{ money(s.gov_amount) }}</dd>
        <dt>长护险支付</dt><dd class="num">{{ money(s.ltci_amount) }}</dd>
        <dt>个人自付</dt><dd class="num"><b style="font-size: 1.25rem">{{ money(s.self_amount) }}</b></dd>
      </dl>
    </section>
  </div>
</template>
