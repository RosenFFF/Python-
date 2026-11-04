<script setup>
defineProps({ title: String, wide: Boolean })
defineEmits(['close'])
</script>

<template>
  <div class="modal-mask" role="dialog" aria-modal="true" @click.self="$emit('close')">
    <div class="modal" :class="{ wide }">
      <div class="row between mb">
        <h2>{{ title }}</h2>
        <button class="btn sm ghost" aria-label="关闭" @click="$emit('close')">关闭</button>
      </div>
      <slot />
      <div v-if="$slots.foot" class="modal-foot"><slot name="foot" /></div>
    </div>
  </div>
</template>
