<script setup>
import { reactive } from 'vue'
import { state, toast } from '../store'

const props = defineProps({ grids: Array, initial: Object })
const emit = defineEmits(['submit', 'cancel'])

const editing = Boolean(props.initial)
const form = reactive({
  name: props.initial?.name ?? '', gender: props.initial?.gender ?? '女', birth_date: props.initial?.birth_date ?? '',
  grid_id: props.initial?.grid_id ?? null, id_card: '', phone: '', address: '', lat: props.initial?.lat ?? null, lng: props.initial?.lng ?? null,
  emergency_name: props.initial?.emergency_name ?? '', emergency_relation: props.initial?.emergency_relation ?? '', emergency_phone: '',
  medical_insurance: props.initial?.medical_insurance ?? '', ltc_insurance: props.initial?.ltc_insurance ?? '未参保',
  disability_level: props.initial?.disability_level ?? 'INTACT', difficulty_type: props.initial?.difficulty_type ?? 'NONE',
  care_tags: [...(props.initial?.care_tags ?? [])], allergies: props.initial?.allergies ?? '',
  chronic_text: (props.initial?.chronic_diseases ?? []).join('、'), medication_text: (props.initial?.medications ?? []).join('、'),
})

const split = (text) => text.split(/[、,，]/).map((s) => s.trim()).filter(Boolean)

function submit() {
  if (!form.name || !form.birth_date || !form.grid_id || (!editing && (!form.id_card || !form.address))) {
    toast('请填写姓名、出生日期、网格、身份证号和地址', 'error')
    return
  }
  const { chronic_text: chronic, medication_text: medication, ...rest } = form
  const payload = { ...rest, chronic_diseases: split(chronic), medications: split(medication) }
  // 编辑时敏感字段留空表示不修改
  for (const key of ['id_card', 'phone', 'address', 'emergency_phone', 'lat', 'lng']) {
    if (payload[key] === '' || payload[key] === null) delete payload[key]
  }
  emit('submit', payload)
}
</script>

<template>
  <div class="form-grid">
    <label class="field"><span>姓名<i>*</i></span><input v-model="form.name" /></label>
    <label class="field"><span>性别</span><select v-model="form.gender"><option>男</option><option>女</option></select></label>
    <label class="field"><span>出生日期<i>*</i></span><input v-model="form.birth_date" type="date" /></label>
    <label class="field"><span>所属网格<i>*</i></span>
      <select v-model.number="form.grid_id"><option :value="null">请选择</option><option v-for="g in grids" :key="g.id" :value="g.id">{{ g.community }}　{{ g.name }}</option></select>
    </label>
    <label class="field"><span>身份证号<i v-if="!editing">*</i>{{ editing ? '（留空不修改）' : '' }}</span><input v-model="form.id_card" maxlength="18" /></label>
    <label class="field"><span>联系电话{{ editing ? '（留空不修改）' : '' }}</span><input v-model="form.phone" maxlength="11" /></label>
    <label class="field full"><span>居住地址<i v-if="!editing">*</i>{{ editing ? '（留空不修改）' : '' }}</span><input v-model="form.address" /></label>
    <label class="field"><span>紧急联系人</span><input v-model="form.emergency_name" /></label>
    <label class="field"><span>关系</span><input v-model="form.emergency_relation" /></label>
    <label class="field"><span>紧急联系电话{{ editing ? '（留空不修改）' : '' }}</span><input v-model="form.emergency_phone" maxlength="11" /></label>
    <label class="field"><span>医保类型</span>
      <select v-model="form.medical_insurance"><option value="">未填写</option><option v-for="t in state.meta?.medical_insurance_types" :key="t">{{ t }}</option></select>
    </label>
    <label class="field"><span>长护险类型</span>
      <select v-model="form.ltc_insurance"><option v-for="t in state.meta?.ltc_insurance_types" :key="t">{{ t }}</option></select>
    </label>
    <label class="field"><span>失能等级</span>
      <select v-model="form.disability_level"><option v-for="(t, c) in state.meta?.labels?.DisabilityLevel" :key="c" :value="c">{{ t }}</option></select>
    </label>
    <label class="field"><span>困难类型</span>
      <select v-model="form.difficulty_type"><option v-for="(t, c) in state.meta?.labels?.DifficultyType" :key="c" :value="c">{{ t }}</option></select>
    </label>
    <label class="field"><span>过敏史</span><input v-model="form.allergies" /></label>
    <label class="field full"><span>慢性病（顿号分隔）</span><input v-model="form.chronic_text" placeholder="高血压、糖尿病" /></label>
    <label class="field full"><span>用药清单（顿号分隔）</span><input v-model="form.medication_text" placeholder="氨氯地平 5mg 每日一次" /></label>
    <div class="field full"><span>照护标签</span>
      <div class="checks"><label v-for="t in state.meta?.care_tags" :key="t"><input v-model="form.care_tags" type="checkbox" :value="t" /> {{ t }}</label></div>
    </div>
  </div>
  <div class="modal-foot">
    <button class="btn" @click="emit('cancel')">取消</button>
    <button class="btn primary" @click="submit">保存</button>
  </div>
</template>
