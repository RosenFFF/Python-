<script setup>
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, MarkLineComponent, CanvasRenderer])

const props = defineProps({ option: Object, height: { type: String, default: '280px' } })
const el = ref(null)
let chart
let observer

function render() {
  if (chart && props.option) chart.setOption(props.option, true)
}

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
watch(() => props.option, render)
</script>

<template>
  <div ref="el" :style="{ height, width: '100%' }" role="img"></div>
</template>
