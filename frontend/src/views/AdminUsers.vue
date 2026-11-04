<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import Modal from '../components/Modal.vue'
import Pager from '../components/Pager.vue'
import { run, state } from '../store'
import { dt } from '../utils'

const filters = reactive({ keyword: '', role: '', page: 1, size: 20 })
const data = ref({ items: [], total: 0 })
const tree = ref({ communities: [], organizations: [] })
const edit = reactive({ show: false, id: null, username: '', password: '', display_name: '', role: 'grid_worker', phone: '', grid_id: null, community_id: null, org_id: null, is_active: true })
const reset = reactive({ target: null, password: '' })

async function load() { data.value = await api.get('/admin/users', filters) }

function startEdit(user) {
  Object.assign(edit, { show: true, id: user?.id ?? null, username: user?.username ?? '', password: '', display_name: user?.display_name ?? '', role: user?.role ?? 'grid_worker',
    phone: '', grid_id: user?.grid_id ?? null, community_id: user?.community_id ?? null, org_id: user?.org_id ?? null, is_active: user?.is_active ?? true })
}

async function save() {
  const org = { grid_id: edit.grid_id, community_id: edit.community_id, org_id: edit.org_id }
  const action = edit.id
    ? () => api.patch(`/admin/users/${edit.id}`, { display_name: edit.display_name, role: edit.role, is_active: edit.is_active, ...org, ...(edit.phone ? { phone: edit.phone } : {}) })
    : () => api.post('/admin/users', { username: edit.username, password: edit.password, display_name: edit.display_name, role: edit.role, phone: edit.phone || null, ...org })
  if (await run(action, '已保存')) {
    edit.show = false
    load()
  }
}

async function doReset() {
  if (await run(() => api.post(`/admin/users/${reset.target.id}/reset-password`, { new_password: reset.password }), '密码已重置，旧登录已失效')) reset.target = null
}

watch(() => [filters.role, filters.page], load)
onMounted(async () => {
  load()
  tree.value = await api.get('/org/tree')
})
</script>

<template>
  <div class="card">
    <div class="filters">
      <label class="field"><span>用户名或姓名</span><input v-model="filters.keyword" @keyup.enter="filters.page = 1; load()" /></label>
      <label class="field"><span>角色</span>
        <select v-model="filters.role"><option value="">全部</option><option v-for="(t, c) in state.meta?.labels?.RoleCode" :key="c" :value="c">{{ t }}</option></select>
      </label>
      <button class="btn" @click="filters.page = 1; load()">查询</button>
      <span class="grow"></span>
      <button class="btn primary" @click="startEdit(null)">新建用户</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>用户名</th><th>姓名</th><th>角色</th><th>归属</th><th>手机号</th><th>最近登录</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="u in data.items" :key="u.id">
            <td class="num">{{ u.username }}</td><td>{{ u.display_name }}</td><td><span class="tag">{{ u.role_name }}</span></td>
            <td class="small muted">{{ u.grid_name || u.community_name || u.org_name || '—' }}</td><td class="num small">{{ u.phone }}</td>
            <td class="num small">{{ dt(u.last_login_at) }}</td><td><span class="tag" :class="u.is_active ? 'green' : ''">{{ u.is_active ? '正常' : '停用' }}</span></td>
            <td class="right nowrap"><button class="btn sm" @click="startEdit(u)">编辑</button><button class="btn sm" @click="reset.target = u; reset.password = ''">重置密码</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <Pager v-model:page="filters.page" :size="filters.size" :total="data.total" />

    <Modal v-if="edit.show" :title="edit.id ? '编辑用户' : '新建用户'" @close="edit.show = false">
      <div class="form-grid">
        <label v-if="!edit.id" class="field"><span>用户名<i>*</i></span><input v-model="edit.username" /></label>
        <label v-if="!edit.id" class="field"><span>初始密码<i>*</i></span><input v-model="edit.password" type="password" autocomplete="new-password" /></label>
        <label class="field"><span>姓名<i>*</i></span><input v-model="edit.display_name" /></label>
        <label class="field"><span>手机号{{ edit.id ? '（留空不修改）' : '' }}</span><input v-model="edit.phone" maxlength="11" /></label>
        <label class="field"><span>角色</span><select v-model="edit.role"><option v-for="(t, c) in state.meta?.labels?.RoleCode" :key="c" :value="c">{{ t }}</option></select></label>
        <label class="field"><span>网格（网格员必填）</span>
          <select v-model="edit.grid_id"><option :value="null">无</option><template v-for="c in tree.communities" :key="c.id"><option v-for="g in c.grids" :key="g.id" :value="g.id">{{ g.name }}</option></template></select>
        </label>
        <label class="field"><span>社区（社区管理员必填）</span>
          <select v-model="edit.community_id"><option :value="null">无</option><option v-for="c in tree.communities" :key="c.id" :value="c.id">{{ c.name }}</option></select>
        </label>
        <label class="field"><span>机构（机构管理员必填）</span>
          <select v-model="edit.org_id"><option :value="null">无</option><option v-for="o in tree.organizations" :key="o.id" :value="o.id">{{ o.name }}</option></select>
        </label>
        <label v-if="edit.id" class="checks full"><label><input v-model="edit.is_active" type="checkbox" /> 账号可用</label></label>
      </div>
      <template #foot><button class="btn primary" @click="save">保存</button></template>
    </Modal>

    <Modal v-if="reset.target" :title="`重置 ${reset.target.display_name} 的密码`" @close="reset.target = null">
      <label class="field"><span>新密码（至少 8 位，含三类字符）</span><input v-model="reset.password" type="password" autocomplete="new-password" /></label>
      <template #foot><button class="btn primary" @click="doReset">确认</button></template>
    </Modal>
  </div>
</template>
