<script setup lang="ts">
import NcTextField from '@nextcloud/vue/components/NcTextField'
import { useId } from 'vue'
const inputId = useId()
const props = withDefaults(defineProps<{ modelValue: string | number; label: string; numeric?: boolean; type?: string }>(), { type: 'text' })
const emit = defineEmits<{ 'update:modelValue': [value: string | number] }>()
function change(value: string | number) { emit('update:modelValue', props.numeric ? Number(value) : String(value)) }
</script>
<template>
  <label v-if="['date','datetime-local'].includes(type)" class="select-label">{{ label }}<input :type="type" :value="modelValue" @input="change(($event.target as HTMLInputElement).value)"></label>
  <div v-else class="events-field"><label :for="inputId">{{ label }}</label><NcTextField :id="inputId" :model-value="String(modelValue)" label-outside :type="numeric ? 'number' : type === 'password' ? 'password' : 'text'" @update:model-value="change" /></div>
</template>
<style scoped>
.events-field{width:100%;min-width:0}.events-field>label{display:block;white-space:normal;line-height:1.5;margin-bottom:8px;overflow-wrap:anywhere}
</style>
