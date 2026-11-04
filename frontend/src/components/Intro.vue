<script setup>
// 进入动画（约 1.8 秒）：社区天亮。
// 夜色里窗户一盏盏亮起，太阳从屋顶后升起，天空和楼房随之变亮，标志飞到左上角。
// 画面终点和登录页完全一致，所以揭开时看不到切换；已登录时则淡入工作台。
// 每个浏览器会话播放一次，点击可跳过，减少动效偏好下不播放。
import { onMounted, ref } from 'vue'
import CommunityScene from './CommunityScene.vue'
import Logo from './Logo.vue'

const emit = defineEmits(['done'])
const backdrop = ref(null)
const night = ref(null)
const lockup = ref(null)
const MARK_SIZE = 72
let finished = false
let timers = []

function finish() {
  if (finished) return
  finished = true
  timers.forEach(clearTimeout)
  delete document.documentElement.dataset.intro
  emit('done')
}

const later = (fn, ms) => timers.push(setTimeout(fn, ms))
const DAWN = { delay: 400, duration: 900, easing: 'ease-in-out', fill: 'both' }

function playScene() {
  const scene = backdrop.value
  // 夜空经过饱和的晨蓝再变亮，避免深色直接淡出时发灰
  night.value.animate([
    { backgroundColor: '#0f2748', opacity: 1 },
    { backgroundColor: '#3f6cab', opacity: 1, offset: .42 },
    { backgroundColor: '#b4cce8', opacity: .9, offset: .76 },
    { backgroundColor: '#e6eef8', opacity: 0 },
  ], DAWN)
  // 楼房从夜色剪影变成白天的颜色
  for (const [selector, dark, day] of [
    ['.scene-far', '#1a3b6c', '#C9D8EC'],
    ['.scene-mid', '#142f57', '#8FABD3'],
    ['.scene-near', '#0c2240', '#25518F'],
  ]) {
    scene.querySelector(selector).animate([{ fill: dark }, { fill: day }], DAWN)
  }
  // 窗户由左至右错落亮起
  const order = [3, 8, 0, 11, 5, 13, 1, 9, 6, 2, 12, 7, 4, 10]
  scene.querySelectorAll('.scene-window').forEach((el, index) => {
    el.animate([{ opacity: 0 }, { opacity: 1 }], { delay: 40 + order[index] * 34, duration: 120, easing: 'ease-out', fill: 'both' })
  })
  // 太阳从屋顶后升起，起点在画面之外
  scene.querySelector('.scene-sun').animate(
    [{ transform: 'translateY(240px)' }, { transform: 'none' }],
    { delay: 320, duration: 1020, easing: 'cubic-bezier(.16,.7,.3,1)', fill: 'both' },
  )
  scene.querySelector('.scene-halo').animate(
    [{ opacity: 0, transform: 'translateY(200px)' }, { opacity: 1, transform: 'none', offset: .6 }, { opacity: 0, transform: 'none' }],
    { delay: 320, duration: 1250, easing: 'ease-out', fill: 'both' },
  )
}

function flyToPageLogo() {
  // 标志飞到页面上 logo 的位置并缩放到同样大小，揭开后正好重合
  const target = document.querySelector('.brand [data-brand-mark]')
  const duration = 480
  if (target) {
    const from = lockup.value.querySelector('[data-brand-mark]').getBoundingClientRect()
    const to = target.getBoundingClientRect()
    lockup.value.animate(
      [{ transform: 'none' }, { transform: `translate(${to.left - from.left}px, ${to.top - from.top}px) scale(${to.width / from.width})` }],
      { duration, easing: 'cubic-bezier(.6,0,.2,1)', fill: 'forwards' },
    )
  } else {
    lockup.value.animate([{ opacity: 1 }, { opacity: 0 }], { duration, fill: 'forwards' })
  }
  later(() => {
    backdrop.value.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 380, easing: 'ease-out', fill: 'forwards' })
  }, 80)
  later(finish, Math.max(duration, 460))
}

onMounted(() => {
  // 动画期间先隐藏页面上的 logo，飞行的标志落位后再替换，全程只看到一个
  document.documentElement.dataset.intro = 'on'
  playScene()
  lockup.value.animate([{ opacity: 0 }, { opacity: 1 }], { delay: 800, duration: 260, easing: 'ease-out', fill: 'both' })
  later(flyToPageLogo, 1320)
})
</script>

<template>
  <div class="intro" aria-hidden="true" @click="finish">
    <div ref="backdrop" class="intro-backdrop">
      <div ref="night" class="intro-night"></div>
      <CommunityScene />
    </div>
    <div ref="lockup" class="intro-lockup"><Logo :size="MARK_SIZE" /></div>
  </div>
</template>
