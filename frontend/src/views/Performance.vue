<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { monthOf, pct } from '../utils'

const month = ref(monthOf())
const sort = ref('completed')
const rows = ref([])

async function load() { rows.value = await api.get('/performance', { month: month.value, sort: sort.value }) }

watch([month, sort], load)
onMounted(load)
</script>

<template>
  <div class="card">
    <div class="filters">
      <label class="field"><span>月份</span><input v-model="month" type="month" /></label>
      <label class="field"><span>排序</span>
        <select v-model="sort">
          <option value="completed">工单完成量</option><option value="rating">平均评分</option>
          <option value="on_time">准时率</option><option value="exception">异常率（低到高）</option>
        </select>
      </label>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>名次</th><th>护理员</th><th>机构</th><th class="right">完成量</th><th class="right">平均评分</th><th class="right">准时率</th><th class="right">异常率</th><th class="right">投诉成立</th><th class="right">扣分</th><th class="right">拒单</th><th></th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.caregiver_id" :class="{ 'flag-red': r.key_attention }">
            <td class="num"><span class="rank" v-if="r.rank <= 3">{{ r.rank }}</span><span v-else>{{ r.rank }}</span></td>
            <td><b>{{ r.name }}</b></td><td class="small muted">{{ r.org_name }}</td>
            <td class="num right">{{ r.completed }}</td>
            <td class="num right">{{ r.avg_rating ?? '—' }}<span v-if="r.rating_count" class="small muted">（{{ r.rating_count }}）</span></td>
            <td class="num right">{{ pct(r.on_time_rate) }}</td><td class="num right">{{ pct(r.exception_rate) }}</td>
            <td class="num right">{{ r.complaints_upheld || '—' }}</td><td class="num right">{{ r.penalty_points || '—' }}</td><td class="num right">{{ r.rejects || '—' }}</td>
            <td><span v-if="r.key_attention" class="tag red">重点关注</span></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty">没有数据</div>
    </div>
    <p class="small muted mt">准时率 = 准时签到工单 / 已签到工单；异常率 = 异常单 / (完成工单 + 异常单)。样本为 0 时显示“—”。</p>
  </div>
</template>
