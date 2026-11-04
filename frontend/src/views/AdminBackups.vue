<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { run, toast } from '../store'
import { dt, serverNow, toLocalInput } from '../utils'

const backups = ref([])
const reports = ref([])
const busy = ref(false)
const target = ref(toLocalInput(serverNow()))
const wizard = reactive({ preview: null, password: '', word: '', report: null })
const KIND = { AUTO: '每日自动', MANUAL: '手动', PRE_RESTORE: '恢复前快照', SEED: '初始数据' }

async function load() {
  const data = await api.get('/admin/backups')
  backups.value = data.backups
  reports.value = data.reports
}

async function create() {
  busy.value = true
  if (await run(() => api.post('/admin/backups'), '备份完成')) await load()
  busy.value = false
}

async function verify(row) {
  const result = await run(() => api.post(`/admin/backups/${row.name}/verify`))
  if (result) toast(result.ok ? '校验通过：哈希一致，数据库完整' : `校验失败：${result.problems.join('；')}`, result.ok ? 'info' : 'error', 5000)
}

async function startRestore(body) {
  const preview = await run(() => api.post('/admin/backups/restore/preview', body))
  if (preview) Object.assign(wizard, { preview, password: '', word: '', report: null })
}

async function confirm() {
  busy.value = true
  const report = await run(() => api.post('/admin/backups/restore/confirm', {
    confirm_token: wizard.preview.confirm_token, password: wizard.password, confirm_word: wizard.word,
  }))
  busy.value = false
  if (report) {
    wizard.report = report
    wizard.preview = null
    toast('恢复完成')
    load()
  }
}

const size = (bytes) => (bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`)
onMounted(load)
</script>

<template>
  <div class="col">
    <section class="card">
      <div class="row between mb">
        <div>
          <h2>备份列表</h2>
          <div class="small muted">每日凌晨 2:00 自动全量备份数据库和附件，存放在与生产库分离的目录，保留最近 30 个版本。</div>
        </div>
        <button class="btn primary" :disabled="busy" @click="create">立即备份</button>
      </div>
      <div class="row mb">
        <label class="field"><span>按时间点恢复</span><input v-model="target" type="datetime-local" /></label>
        <button class="btn" style="align-self: flex-end" @click="startRestore({ target_time: `${target}:00` })">查找并预览</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>文件</th><th>时间</th><th>类型</th><th>操作人</th><th class="right">大小</th><th class="right">工单数</th><th class="right">有效档案</th><th></th></tr></thead>
          <tbody>
            <tr v-for="b in backups" :key="b.name">
              <td class="num small">{{ b.name }}</td>
              <template v-if="b.corrupted"><td colspan="6"><span class="tag red">文件已损坏</span></td></template>
              <template v-else>
                <td class="num small">{{ dt(b.created_at, true) }}</td><td><span class="tag">{{ KIND[b.kind] || b.kind }}</span></td><td class="small">{{ b.created_by }}</td>
                <td class="num right">{{ size(b.size) }}</td><td class="num right">{{ b.key_metrics['工单总数'] }}</td><td class="num right">{{ b.key_metrics['有效老人档案'] }}</td>
              </template>
              <td class="right nowrap">
                <button class="btn sm" @click="verify(b)">校验</button>
                <button class="btn sm" :disabled="b.corrupted" @click="startRestore({ name: b.name })">恢复</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!backups.length" class="empty">还没有备份</div>
      </div>
    </section>

    <section v-if="wizard.report" class="card">
      <h2 class="mb">恢复核对报告 <span class="tag" :class="wizard.report.all_match ? 'green' : 'red'">{{ wizard.report.all_match ? '全部一致' : '存在差异' }}</span></h2>
      <div class="small muted mb">恢复到 {{ dt(wizard.report.restored_to, true) }}，操作人 {{ wizard.report.operator }}，恢复前快照 {{ wizard.report.pre_restore_backup }}，接回审计记录 {{ wizard.report.audit_rows_carried }} 条，恢复附件 {{ wizard.report.files_restored }} 个。</div>
      <table>
        <thead><tr><th>关键指标</th><th class="right">恢复前</th><th class="right">备份值</th><th class="right">恢复后</th><th>核对</th></tr></thead>
        <tbody>
          <tr v-for="m in wizard.report.metrics" :key="m.metric">
            <td>{{ m.metric }}</td><td class="num right">{{ m.before_restore }}</td><td class="num right">{{ m.backup }}</td><td class="num right">{{ m.after_restore }}</td>
            <td><span class="tag" :class="m.match ? 'green' : 'red'">{{ m.match ? '一致' : '不一致' }}</span></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="reports.length" class="card">
      <h2 class="mb">历史恢复记录</h2>
      <table>
        <thead><tr><th>执行时间</th><th>操作人</th><th>恢复到</th><th>备份文件</th><th>核对</th></tr></thead>
        <tbody>
          <tr v-for="r in reports" :key="r.report_file">
            <td class="num small">{{ dt(r.executed_at, true) }}</td><td>{{ r.operator }}</td><td class="num small">{{ dt(r.restored_to, true) }}</td><td class="num small">{{ r.backup }}</td>
            <td><span class="tag" :class="r.all_match ? 'green' : 'red'">{{ r.all_match ? '全部一致' : '存在差异' }}</span></td>
          </tr>
        </tbody>
      </table>
    </section>

    <Modal v-if="wizard.preview" title="确认恢复数据" wide @close="wizard.preview = null">
      <div class="col">
        <div class="notice">校验通过：{{ wizard.preview.name }}（{{ dt(wizard.preview.created_at, true) }}）</div>
        <div class="notice error">恢复后，该时间点之后的业务数据都会回退。系统会先自动做一次恢复前快照，审计日志不回退。</div>
        <h3>影响范围（{{ wizard.preview.impact.length }} 张表有变化）</h3>
        <div class="table-wrap" style="max-height: 260px; overflow-y: auto">
          <table>
            <thead><tr><th>表</th><th class="right">当前</th><th class="right">恢复后</th><th class="right">变化</th></tr></thead>
            <tbody>
              <tr v-for="i in wizard.preview.impact" :key="i.table">
                <td class="small num">{{ i.table }}</td><td class="num right">{{ i.current }}</td><td class="num right">{{ i.after }}</td>
                <td class="num right" :class="i.delta < 0 ? 'down' : 'up'">{{ i.delta > 0 ? '+' : '' }}{{ i.delta }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="!wizard.preview.impact.length" class="empty">行数没有变化</div>
        </div>
        <div class="form-grid">
          <label class="field"><span>本人登录密码<i>*</i></span><input v-model="wizard.password" type="password" autocomplete="current-password" /></label>
          <label class="field"><span>输入确认词「{{ wizard.preview.confirm_word }}」<i>*</i></span><input v-model="wizard.word" /></label>
        </div>
      </div>
      <template #foot>
        <button class="btn" @click="wizard.preview = null">取消</button>
        <button class="btn danger" :disabled="busy || wizard.word !== wizard.preview.confirm_word || !wizard.password" @click="confirm">{{ busy ? '恢复中…' : '确认恢复' }}</button>
      </template>
    </Modal>
  </div>
</template>
