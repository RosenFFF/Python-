<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
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

// 社区剪影：三层楼房由远及近，坐标固定，画面每次一致
const BASE = 360
function skyline(buildings) {
  return buildings.map(([x, w, h, gable]) => {
    const top = BASE - h
    return gable
      ? `M${x} ${BASE}V${top}L${x + w / 2} ${top - w * 0.3}L${x + w} ${top}V${BASE}Z`
      : `M${x} ${BASE}V${top}H${x + w}V${BASE}Z`
  }).join('')
}
const FAR = skyline([[-20, 130, 150, 0], [120, 90, 190, 0], [220, 110, 140, 1], [340, 120, 210, 0], [470, 80, 160, 0], [560, 130, 120, 1], [700, 100, 200, 0], [810, 120, 150, 0], [940, 90, 230, 0], [1040, 130, 140, 1], [1180, 110, 190, 0], [1300, 160, 160, 0]])
const MID = skyline([[-30, 170, 90, 1], [150, 120, 120, 0], [280, 150, 80, 1], [440, 110, 130, 0], [560, 180, 75, 1], [750, 120, 110, 0], [880, 160, 85, 1], [1050, 110, 135, 0], [1170, 170, 90, 1], [1350, 120, 115, 0]])
const NEAR = [[-20, 200, 52], [190, 170, 62], [370, 220, 46], [600, 180, 58], [790, 230, 44], [1030, 190, 60], [1230, 230, 50]]
const NEAR_PATH = skyline(NEAR.map(([x, w, h]) => [x, w, h, 1]))
const LIT = [[60, 330], [110, 330], [240, 318], [300, 318], [440, 338], [520, 338], [660, 322], [720, 322], [860, 340], [950, 340], [1090, 320], [1150, 320], [1300, 335], [1380, 335]]
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

    <svg class="login-scene" viewBox="0 0 1440 360" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
      <circle class="scene-sun" cx="620" cy="214" r="64" fill="#F2A93B" />
      <path :d="FAR" fill="#C9D8EC" />
      <path :d="MID" fill="#8FABD3" />
      <path :d="NEAR_PATH" fill="#25518F" />
      <rect v-for="([x, y], i) in LIT" :key="i" :x="x" :y="y" width="14" height="16" rx="2" fill="#F2A93B" />
    </svg>
  </div>
</template>
