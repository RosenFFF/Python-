<script setup>
// 社区剪影：三层楼房由远及近，坐标固定。登录页和进入动画共用同一幅画，动画结束时两者完全重合。
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
const NEAR = skyline([[-20, 200, 52, 1], [190, 170, 62, 1], [370, 220, 46, 1], [600, 180, 58, 1], [790, 230, 44, 1], [1030, 190, 60, 1], [1230, 230, 50, 1]])
const LIT = [[60, 330], [110, 330], [240, 318], [300, 318], [440, 338], [520, 338], [660, 322], [720, 322], [860, 340], [950, 340], [1090, 320], [1150, 320], [1300, 335], [1380, 335]]
</script>

<template>
  <svg class="login-scene" viewBox="0 0 1440 360" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
    <defs>
      <radialGradient id="scene-glow">
        <stop offset="0" stop-color="#F2A93B" stop-opacity=".75" />
        <stop offset=".45" stop-color="#F2A93B" stop-opacity=".28" />
        <stop offset="1" stop-color="#F2A93B" stop-opacity="0" />
      </radialGradient>
    </defs>
    <circle class="scene-halo" cx="620" cy="214" r="300" fill="url(#scene-glow)" opacity="0" />
    <circle class="scene-sun" cx="620" cy="214" r="64" fill="#F2A93B" />
    <path class="scene-far" :d="FAR" fill="#C9D8EC" />
    <path class="scene-mid" :d="MID" fill="#8FABD3" />
    <path class="scene-near" :d="NEAR" fill="#25518F" />
    <rect v-for="([x, y], i) in LIT" :key="i" class="scene-window" :x="x" :y="y" width="14" height="16" rx="2" fill="#F2A93B" />
  </svg>
</template>
