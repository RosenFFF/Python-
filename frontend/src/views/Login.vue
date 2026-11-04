<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import CommunityScene from '../components/CommunityScene.vue'
import Logo from '../components/Logo.vue'
import { homePath, login, showError, state } from '../store'

const router = useRouter()
const username = ref('')
const password = ref('')
const busy = ref(false)

const DEMO_ACCOUNTS = [
  { user: 'family_wang', label: '家属' },
  { user: 'cg01', label: '护理员' },
  { user: 'grid_zhang', label: '网格员' },
  { user: 'comm_liu', label: '社区管理员' },
  { user: 'org_zhao', label: '机构管理员' },
  { user: 'street_sun', label: '街道监管员' },
  { user: 'admin', label: '超级管理员' },
]

async function submit() {
  busy.value = true
  try {
    await login(username.value.trim(), password.value)
    router.push(homePath())
  } catch (error) {
    showError(error)
  } finally {
    busy.value = false
  }
}

function fill(user) {
  username.value = user
  password.value = 'Demo@2026'
}

</script>

<template>
  <div class="login">
    <div class="brand"><Logo /></div>

    <div class="login-body">
      <div class="login-copy">
        <h1>谁上门、几点到、做了什么，<br />家人都看得见。</h1>
        <p>社区智慧养老服务平台</p>
      </div>

      <form class="login-panel" @submit.prevent="submit">
        <h2>欢迎回来</h2>
        <label class="field"><span>用户名</span><input v-model="username" autocomplete="username" required /></label>
        <label class="field"><span>密码</span><input v-model="password" type="password" autocomplete="current-password" required /></label>
        <button class="btn primary lg" :disabled="busy">{{ busy ? '正在登录' : '登录' }}</button>
        <template v-if="state.meta?.demo_mode">
          <span class="muted small" style="margin-top: 4px">比赛演示，选一个身份体验</span>
          <div class="row" style="gap: 6px">
            <button v-for="a in DEMO_ACCOUNTS" :key="a.user" type="button" class="chip" @click="fill(a.user)">{{ a.label }}</button>
          </div>
        </template>
      </form>
    </div>

    <CommunityScene />
  </div>
</template>
