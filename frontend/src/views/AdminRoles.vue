<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { run } from '../store'

const matrix = ref(null)
const dirty = ref(new Set())

const groups = computed(() => {
  const result = {}
  for (const p of matrix.value?.permissions || []) (result[p.group] ||= []).push(p)
  return result
})

function has(role, code) { return role.permissions.includes(code) }

function toggle(role, code) {
  if (has(role, code)) role.permissions = role.permissions.filter((p) => p !== code)
  else role.permissions = [...role.permissions, code]
  dirty.value.add(role.code)
  dirty.value = new Set(dirty.value)
}

async function save() {
  for (const code of dirty.value) {
    const role = matrix.value.roles.find((r) => r.code === code)
    const ok = await run(() => api.put(`/admin/roles/${code}/permissions`, { permissions: role.permissions }))
    if (!ok) return
  }
  dirty.value = new Set()
  matrix.value = await api.get('/admin/roles')
}

onMounted(async () => { matrix.value = await api.get('/admin/roles') })
</script>

<template>
  <div v-if="matrix" class="card">
    <div class="row between mb">
      <div class="small muted">功能权限和数据范围分别生效。修改立即生效，并写入审计日志。</div>
      <button class="btn primary" :disabled="!dirty.size" @click="save">保存修改（{{ dirty.size }}）</button>
    </div>
    <div class="table-wrap">
      <table class="matrix">
        <thead>
          <tr><th>权限</th><th v-for="r in matrix.roles" :key="r.code">{{ r.name }}<div class="small muted">{{ r.data_scope_label }}</div></th></tr>
        </thead>
        <tbody>
          <template v-for="(items, group) in groups" :key="group">
            <tr><td :colspan="matrix.roles.length + 1" style="background: var(--pine-50); font-weight: 600">{{ group }}</td></tr>
            <tr v-for="p in items" :key="p.code">
              <td>{{ p.name }} <span class="muted small">{{ p.code }}</span></td>
              <td v-for="r in matrix.roles" :key="r.code"><input type="checkbox" :checked="has(r, p.code)" :aria-label="`${r.name} ${p.name}`" @change="toggle(r, p.code)" /></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
