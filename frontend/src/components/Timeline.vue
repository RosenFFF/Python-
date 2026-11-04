<script setup>
import { dt } from '../utils'

defineProps({ items: Array })
</script>

<template>
  <ul class="timeline">
    <li v-for="(item, index) in items" :key="item.id ?? index">
      <div class="when num">{{ dt(item.at) }} · {{ item.actor }}</div>
      <div>
        <b v-if="item.to_label && item.to">{{ item.to_label }}</b>
        <span v-if="item.note" :class="{ muted: item.to_label && item.to }"> {{ item.note }}</span>
        <span v-if="item.detail">{{ item.detail }}</span>
      </div>
    </li>
  </ul>
</template>
