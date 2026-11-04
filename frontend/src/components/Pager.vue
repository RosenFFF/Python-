<script setup>
import { computed } from 'vue'

const props = defineProps({ page: Number, size: Number, total: Number })
const emit = defineEmits(['update:page'])
const pages = computed(() => Math.max(1, Math.ceil((props.total || 0) / (props.size || 1))))
</script>

<template>
  <div class="pager">
    <span>共 {{ total ?? 0 }} 条</span>
    <button class="btn sm" :disabled="page <= 1" @click="emit('update:page', page - 1)">上一页</button>
    <span class="num">{{ page }} / {{ pages }}</span>
    <button class="btn sm" :disabled="page >= pages" @click="emit('update:page', page + 1)">下一页</button>
  </div>
</template>
