<script setup>
// 进入动画（约 1.4 秒，每个浏览器会话只播一次）：
// 屋檐和托举弧线画出，暖阳升起，标志再飞到左上角的品牌位，和页面上的 logo 重合。
// 减少动效偏好下不播放；点击可跳过。
import { onMounted, ref } from 'vue'
import { WORDMARK_PATHS, WORDMARK_WIDTH } from '../brand'

const emit = defineEmits(['done'])
const root = ref(null)
const mark = ref(null)
const roof = ref(null)
const arc = ref(null)
const sun = ref(null)
const word = ref(null)
const SIZE = 104
let finished = false

function finish() {
  if (finished) return
  finished = true
  emit('done')
}

function draw(el, delay, duration) {
  const length = el.getTotalLength()
  el.style.strokeDasharray = length
  return el.animate([{ strokeDashoffset: length }, { strokeDashoffset: 0 }], { duration, delay, easing: 'cubic-bezier(.5,0,.2,1)', fill: 'both' })
}

onMounted(async () => {
  draw(roof.value, 60, 420)
  draw(arc.value, 180, 420)
  sun.value.animate(
    [{ transform: 'translateY(16px) scale(.3)', opacity: 0 }, { transform: 'none', opacity: 1 }],
    { duration: 480, delay: 240, easing: 'cubic-bezier(.22,.8,.24,1)', fill: 'both' },
  )
  await word.value.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 360, delay: 380, easing: 'ease-out', fill: 'both' }).finished
  await new Promise((resolve) => { setTimeout(resolve, 160) })
  if (finished) return

  // 飞向页面上的 logo。页面还没渲染出来时就地淡出
  const target = document.querySelector('[data-brand-mark]')
  const easing = 'cubic-bezier(.6,0,.2,1)'
  const duration = 480
  if (target) {
    const from = mark.value.getBoundingClientRect()
    const to = target.getBoundingClientRect()
    const dx = to.left + to.width / 2 - (from.left + from.width / 2)
    const dy = to.top + to.height / 2 - (from.top + from.height / 2)
    mark.value.animate([{ transform: 'none' }, { transform: `translate(${dx}px, ${dy}px) scale(${to.width / from.width})` }], { duration, easing, fill: 'forwards' })
  }
  word.value.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 180, fill: 'forwards' })
  root.value.animate(
    [{ backgroundColor: '#e6eef8' }, { backgroundColor: 'rgba(230,238,248,0)' }],
    { duration, easing: 'ease-in', fill: 'forwards' },
  )
  root.value.style.pointerEvents = 'none'
  await new Promise((resolve) => { setTimeout(resolve, duration) })
  finish()
})
</script>

<template>
  <div ref="root" class="intro" aria-hidden="true" @click="finish">
    <svg ref="mark" viewBox="0 0 64 64" :width="SIZE" :height="SIZE">
      <circle cx="32" cy="32" r="31" fill="#25518F" />
      <path ref="roof" d="M13 30 32 14l19 16" fill="none" stroke="#fff" stroke-width="4.6" stroke-linecap="round" stroke-linejoin="round" />
      <circle ref="sun" cx="32" cy="33" r="7" fill="#F2A93B" style="transform-box: fill-box; transform-origin: center" />
      <path ref="arc" d="M15 41c4 7.500 10 11 17 11s13-3.500 17-11" fill="none" stroke="#fff" stroke-width="4.6" stroke-linecap="round" />
    </svg>
    <svg ref="word" :viewBox="`0 0 ${WORDMARK_WIDTH} 42`" :width="(30 * WORDMARK_WIDTH) / 42" height="30">
      <path v-for="(d, i) in WORDMARK_PATHS" :key="i" :d="d" fill="#163A6B" />
    </svg>
  </div>
</template>
