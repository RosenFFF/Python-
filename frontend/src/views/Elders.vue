<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import ElderForm from '../components/ElderForm.vue'
import Modal from '../components/Modal.vue'
import Pager from '../components/Pager.vue'
import { can, run, state, toast } from '../store'

const router = useRouter()
const filters = reactive({ keyword: '', level: '', tag: '', grid_id: '', page: 1, size: 20 })
const data = ref({ items: [], total: 0 })
const grids = ref([])
const creating = ref(false)
const importing = reactive({ show: false, file: null, result: null, busy: false })

async function load() { data.value = await api.get('/elders', filters) }

async function create(payload) {
  if (await run(() => api.post('/elders', payload), '档案已创建，敏感字段已加密存储')) {
    creating.value = false
    load()
  }
}

async function runImport(dryRun) {
  if (!importing.file) {
    toast('请选择文件', 'error')
    return
  }
  importing.busy = true
  const result = await run(() => api.upload(`/elders/import?dry_run=${dryRun}`, importing.file))
  importing.busy = false
  if (result) {
    importing.result = result
    if (!dryRun) load()
  }
}

watch(() => [filters.level, filters.tag, filters.grid_id, filters.page], load)
onMounted(async () => {
  load()
  const tree = await api.get('/org/tree')
  grids.value = tree.communities.flatMap((c) => c.grids.map((g) => ({ ...g, community: c.name })))
})
</script>

<template>
  <div class="card">
    <div class="filters">
      <label class="field"><span>姓名、编号、完整手机号或身份证</span><input v-model="filters.keyword" style="width: 240px" @keyup.enter="filters.page = 1; load()" /></label>
      <label class="field"><span>能力等级</span>
        <select v-model="filters.level"><option value="">全部</option><option v-for="(t, c) in state.meta?.labels?.DisabilityLevel" :key="c" :value="c">{{ t }}</option></select>
      </label>
      <label class="field"><span>照护标签</span>
        <select v-model="filters.tag"><option value="">全部</option><option v-for="t in state.meta?.care_tags" :key="t" :value="t">{{ t }}</option></select>
      </label>
      <label class="field"><span>网格</span>
        <select v-model="filters.grid_id"><option value="">全部</option><option v-for="g in grids" :key="g.id" :value="g.id">{{ g.name }}</option></select>
      </label>
      <button class="btn" @click="filters.page = 1; load()">查询</button>
      <span class="grow"></span>
      <button v-if="can('elder:import')" class="btn" @click="importing.show = true; importing.result = null">批量导入</button>
      <button v-if="can('elder:write')" class="btn primary" @click="creating = true">新建档案</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>编号</th><th>姓名</th><th>年龄</th><th>手机号</th><th>身份证号</th><th>网格</th><th>能力等级</th><th>标签</th><th></th></tr></thead>
        <tbody>
          <tr v-for="e in data.items" :key="e.id" style="cursor: pointer" @click="router.push(`/elders/${e.id}`)">
            <td class="num small">{{ e.code }}</td>
            <td><b>{{ e.name }}</b> <span class="muted small">{{ e.gender }}</span></td>
            <td class="num">{{ e.age }}</td>
            <td class="num small">{{ e.phone }}</td>
            <td class="num small">{{ e.id_card }}</td>
            <td class="small">{{ e.grid_name }}</td>
            <td><span class="tag" :class="{ red: e.disability_level === 'SEVERE', yellow: e.disability_level === 'MODERATE' }">{{ e.disability_label }}</span></td>
            <td><span class="tags"><span v-for="t in e.care_tags" :key="t" class="tag">{{ t }}</span></span></td>
            <td class="right"><span class="btn sm">详情</span></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!data.items.length" class="empty">没有符合条件的档案</div>
    </div>
    <Pager v-model:page="filters.page" :size="filters.size" :total="data.total" />

    <Modal v-if="creating" title="新建老人档案" wide @close="creating = false">
      <ElderForm :grids="grids" @submit="create" @cancel="creating = false" />
    </Modal>

    <Modal v-if="importing.show" title="批量导入档案" @close="importing.show = false">
      <div class="col">
        <p class="small muted" style="margin: 0">支持 .xlsx 和 .csv。第一行为表头：姓名、性别、出生日期、身份证号、联系电话、居住地址、网格、纬度、经度、紧急联系人、关系、紧急联系电话、医保类型、长护险类型、慢性病、用药、过敏史、失能等级、困难类型、照护标签。逐行校验，失败行不影响其他行。</p>
        <input type="file" accept=".xlsx,.csv" @change="importing.file = $event.target.files[0]" />
        <div v-if="importing.result" class="notice" :class="{ warn: importing.result.failed }">
          {{ importing.result.dry_run ? '试导入' : '导入' }}结果：共 {{ importing.result.total }} 行，成功 {{ importing.result.created }}，失败 {{ importing.result.failed }}
          <div v-for="e in importing.result.errors" :key="e.row" class="small">第 {{ e.row }} 行：{{ e.reason }}</div>
        </div>
      </div>
      <template #foot>
        <button class="btn" :disabled="importing.busy" @click="runImport(true)">先试导入</button>
        <button class="btn primary" :disabled="importing.busy" @click="runImport(false)">正式导入</button>
      </template>
    </Modal>
  </div>
</template>
