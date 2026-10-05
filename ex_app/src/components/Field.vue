<script setup lang="ts">
import NcTextField from '@nextcloud/vue/components/NcTextField'
const props = withDefaults(defineProps<{ modelValue: string | number; label: string; numeric?: boolean; type?: string }>(), { type: 'text' })
const emit = defineEmits<{ 'update:modelValue': [value: string | number] }>()
function change(value: string | number) { emit('update:modelValue', props.numeric ? Number(value) : String(value)) }
</script>
<template>
  <label v-if="['date','datetime-local'].includes(type)" class="select-label">{{ label }}<input :type="type" :value="modelValue" @input="change(($event.target as HTMLInputElement).value)"></label>
  <NcTextField v-else :model-value="String(modelValue)" :label="label" :type="numeric ? 'number' : type === 'password' ? 'password' : 'text'" @update:model-value="change" />
</template>
