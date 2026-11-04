<script setup>
import { WORDMARK_PATHS, WORDMARK_WIDTH } from '../brand'

defineProps({ size: { type: Number, default: 34 }, wordmark: { type: Boolean, default: true } })
</script>

<template>
  <span class="row" :style="{ gap: `${size * 0.3}px`, flexWrap: 'nowrap' }" role="img" aria-label="颐护通">
    <svg data-brand-mark viewBox="0 0 64 64" :width="size" :height="size" style="display: block; flex: none">
      <circle cx="32" cy="32" r="31" fill="#25518F" />
      <path d="M13 30 32 14l19 16" fill="none" stroke="#fff" stroke-width="4.6" stroke-linecap="round" stroke-linejoin="round" />
      <circle cx="32" cy="33" r="7" fill="#F2A93B" />
      <path d="M15 41c4 7.500 10 11 17 11s13-3.500 17-11" fill="none" stroke="#fff" stroke-width="4.6" stroke-linecap="round" />
    </svg>
    <svg v-if="wordmark" :viewBox="`0 0 ${WORDMARK_WIDTH} 42`" :height="size * 0.62" :width="(size * 0.62 * WORDMARK_WIDTH) / 42" style="display: block; flex: none">
      <path v-for="(d, i) in WORDMARK_PATHS" :key="i" :d="d" fill="#163A6B" />
    </svg>
  </span>
</template>
