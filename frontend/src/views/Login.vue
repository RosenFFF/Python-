<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { homePath, login, showError, state } from '../store'

const router = useRouter()
const username = ref('')
const password = ref('')
const busy = ref(false)

const DEMO_ACCOUNTS = [
  { user: 'family_wang', label: '家属 王晓明' },
  { user: 'cg01', label: '护理员' },
  { user: 'grid_zhang', label: '网格员 张' },
  { user: 'comm_liu', label: '社区管理员 刘' },
  { user: 'org_zhao', label: '机构管理员 赵' },
  { user: 'street_sun', label: '街道监管 孙' },
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
  <div class="login-wrap">
    <section class="login-side">
      <h1>颐护通</h1>
      <p style="font-size: 1.1rem; margin: 0">谁上门、几点到、做了什么、做得怎么样、费用谁承担，全部有据可查。</p>
      <ul>
        <li>预约、派单、上门、预警、评价、结算六条流程闭环</li>
        <li>可解释的智能派单，推荐理由逐项列明</li>
        <li>蓝黄红三级健康预警，超时自动升级</li>
        <li>字段级加密、二次授权、不可改的审计日志</li>
      </ul>
    </section>
    <section class="login-form">
      <form class="col" style="width: 100%; max-width: 360px" @submit.prevent="submit">
        <h2>登录</h2>
        <label class="field"><span>用户名</span><input v-model="username" autocomplete="username" required /></label>
        <label class="field"><span>密码</span><input v-model="password" type="password" autocomplete="current-password" required /></label>
        <button class="btn primary lg" :disabled="busy">{{ busy ? '登录中…' : '登录' }}</button>
        <div v-if="state.meta?.demo_mode" class="col mt">
          <span class="muted small">演示账号（点选后登录，密码 Demo@2026）</span>
          <div class="row">
            <button v-for="a in DEMO_ACCOUNTS" :key="a.user" type="button" class="chip" @click="fill(a.user)">{{ a.label }}</button>
          </div>
        </div>
      </form>
    </section>
  </div>
</template>
