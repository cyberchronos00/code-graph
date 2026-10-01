<script setup lang="ts">
import { ref, computed } from 'vue'
const { t } = useI18n()
const { fetchTop, exportTop } = useReports()
const session = useSessionStore()
const rows = ref<Array<{ title: string, minor: number, timezone?: string }>>([])

async function load() {
  await session.load()
  rows.value = await fetchTop({ category_id: 1, date_from: '2025-01-01' })
}
const timezone = computed(() => rows.value[0]?.timezone ?? 'UTC')
</script>

<template>
  <div>
    <h1>{{ t('title') }}</h1>
    <TopTable :rows="rows" @click="load" />
    <button @click="exportTop('csv')">{{ $t('export') }}</button>
  </div>
</template>

<i18n lang="json">
{ "en": { "title": "Top sellers", "export": "Export" } }
</i18n>
