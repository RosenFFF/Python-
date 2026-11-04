<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { api, query } from '../api'
import Pager from '../components/Pager.vue'
import { run } from '../store'
import { dt } from '../utils'

const filters = reactive({ action: '', actor_name: '', page: 1, size: 30 })
const data = ref({ items: [], total: 0 })
const verify = ref(null)
const access = ref([])
const tab = ref('logs')
const ACTIONS = {
  LOGIN: '登录', LOGIN_FAILED: '登录失败', ACCOUNT_LOCKED: '账号锁定', ELDER_CREATE: '新建档案', ELDER_UPDATE: '修改档案', ELDER_DELETE: '删除档案',
  ELDER_RESTORE: '恢复档案', ELDER_IMPORT: '批量导入', REVEAL_SENSITIVE: '查看敏感信息', REVEAL_DENIED: '敏感信息授权失败', ORDER_REASSIGN: '改派',
  REFUND: '退费', EXPORT: '导出', PRICE_ADJUST: '调价', CONFIG_UPDATE: '修改配置', ROLE_UPDATE: '修改权限', USER_CREATE: '新建用户', USER_UPDATE: '修改用户',
  BACKUP: '备份', RESTORE: '恢复', RULE_UPDATE: '修改规则', ALERT_ESCALATE: '预警升级', BILL_GENERATE: '生成账单', BILL_EFFECTIVE: '账单生效',
  DISCREPANCY_RESOLVE: '处理差异', SAMPLE_CHECK: '监管抽查', PAYMENT_REGISTER: '登记缴费', COMPLAINT_CONCLUDE: '投诉结论', SOS: '一键呼救',
}

async function load() { data.value = await api.get('/audit/logs', filters) }
async function checkChain() { verify.value = await run(() => api.get('/audit/verify')) }
const exportFile = (fmt) => run(() => api.download(`/audit/export${query({ action: filters.action, actor_name: filters.actor_name, fmt })}`, `审计.${fmt}`), '已导出')

watch(() => [filters.action, filters.page], load)
watch(tab, async (value) => { if (value === 'access') access.value = await api.get('/audit/sensitive-access') })
onMounted(load)
</script>

<template>
  <div class="col">
    <div class="row">
      <button class="btn" :class="{ primary: tab === 'logs' }" @click="tab = 'logs'">操作审计</button>
      <button class="btn" :class="{ primary: tab === 'access' }" @click="tab = 'access'">敏感信息查看记录</button>
      <span class="grow"></span>
      <button class="btn" @click="checkChain">校验哈希链</button>
      <button class="btn" @click="exportFile('xlsx')">导出</button>
    </div>

    <div v-if="verify" class="notice" :class="{ error: !verify.ok }">
      <template v-if="verify.ok">审计链完整：已校验 {{ verify.checked }} 条记录，未发现篡改或删除。</template>
      <template v-else>审计链异常：记录 #{{ verify.broken_at }}，{{ verify.reason }}。</template>
    </div>

    <section v-if="tab === 'logs'" class="card">
      <div class="filters">
        <label class="field"><span>动作</span>
          <select v-model="filters.action" @change="filters.page = 1"><option value="">全部</option><option v-for="(t, c) in ACTIONS" :key="c" :value="c">{{ t }}</option></select>
        </label>
        <label class="field"><span>操作人</span><input v-model="filters.actor_name" @keyup.enter="filters.page = 1; load()" /></label>
        <button class="btn" @click="filters.page = 1; load()">查询</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>操作人</th><th>角色</th><th>动作</th><th>对象</th><th>详情</th><th>IP</th><th>哈希</th></tr></thead>
          <tbody>
            <tr v-for="l in data.items" :key="l.id">
              <td class="num nowrap small">{{ dt(l.at, true) }}</td><td>{{ l.actor_name }}</td><td class="small muted">{{ l.actor_role_label }}</td>
              <td><span class="tag">{{ ACTIONS[l.action] || l.action }}</span></td>
              <td class="small muted nowrap">{{ l.target_type }} {{ l.target_id }}</td>
              <td class="small" style="max-width: 340px; word-break: break-all">{{ l.detail }}</td>
              <td class="small muted">{{ l.ip }}</td><td class="num small muted">{{ l.hash }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <Pager v-model:page="filters.page" :size="filters.size" :total="data.total" />
      <p class="small muted">日志只增不改：数据库触发器拒绝修改和保留期内删除，HMAC 哈希链可发现绕过触发器的改动，保留不少于 180 天。</p>
    </section>

    <section v-else class="card">
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>查看人</th><th>老人档案</th><th>字段</th><th>理由</th><th>IP</th></tr></thead>
          <tbody>
            <tr v-for="l in access" :key="l.id">
              <td class="num small nowrap">{{ dt(l.accessed_at, true) }}</td><td>{{ l.user_name }}</td>
              <td><router-link :to="`/elders/${l.elder_id}`">#{{ l.elder_id }}</router-link></td>
              <td class="small">{{ l.fields.join('、') }}</td><td class="small">{{ l.reason }}</td><td class="small muted">{{ l.ip }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!access.length" class="empty">没有记录</div>
      </div>
    </section>
  </div>
</template>
