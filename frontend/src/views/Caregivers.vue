<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import { run, state } from '../store'

const rows = ref([])
const edit = reactive({ target: null, skills: [], is_active: true })

async function load() { rows.value = await api.get('/caregivers') }

function start(row) {
  Object.assign(edit, { target: row, skills: [...row.skills], is_active: row.is_active })
}

async function save() {
  if (await run(() => api.patch(`/caregivers/${edit.target.id}`, { skills: edit.skills, is_active: edit.is_active }), '已保存')) {
    edit.target = null
    load()
  }
}

onMounted(load)
</script>

<template>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>姓名</th><th>机构</th><th>技能</th><th class="right">连续拒单</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="c in rows" :key="c.id" :class="{ 'flag-red': c.key_attention_month }">
            <td><b>{{ c.name }}</b> <span v-if="c.key_attention_month" class="tag red">重点关注 {{ c.key_attention_month }}</span></td>
            <td class="small muted">{{ c.org_name }}</td>
            <td><span class="tags"><span v-for="s in c.skills" :key="s" class="tag">{{ s }}</span></span></td>
            <td class="num right">{{ c.consecutive_rejects }}</td>
            <td><span class="tag" :class="c.is_active ? 'green' : ''">{{ c.is_active ? '在岗' : '停用' }}</span></td>
            <td class="right"><button class="btn sm" @click="start(c)">编辑</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <Modal v-if="edit.target" :title="`编辑 ${edit.target.name}`" @close="edit.target = null">
      <div class="col">
        <div class="field"><span>技能标签</span><div class="checks"><label v-for="s in state.meta?.skills" :key="s"><input v-model="edit.skills" type="checkbox" :value="s" /> {{ s }}</label></div></div>
        <div class="checks"><label><input v-model="edit.is_active" type="checkbox" /> 在岗（可被派单）</label></div>
      </div>
      <template #foot><button class="btn primary" @click="save">保存</button></template>
    </Modal>
  </div>
</template>
