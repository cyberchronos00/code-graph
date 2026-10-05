<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'
import { docGroups } from '~/utils/docs-nav'

const { seo } = useAppConfig()

const { data: rawNavigation } = await useAsyncData('navigation', () => queryCollectionNavigation('docs'))
const { data: files } = useLazyAsyncData('search', () => queryCollectionSearchSections('docs'), {
  server: false
})

const navigation = computed<ContentNavigationItem[]>(() => {
  const byPath = new Map<string, ContentNavigationItem>()
  const walk = (items?: ContentNavigationItem[]) => {
    for (const item of items || []) {
      if (item.path) {
        byPath.set(item.path, item)
      }
      walk(item.children)
    }
  }
  walk(rawNavigation.value || [])

  return docGroups.map(group => ({
    title: group.title,
    children: group.items.map((item) => {
      const path = `/docs/${item.slug}`
      const found = byPath.get(path)
      return {
        title: found?.title || item.title,
        path,
        stem: found?.stem
      }
    })
  }))
})

useHead({
  meta: [
    { name: 'viewport', content: 'width=device-width, initial-scale=1' }
  ],
  link: [
    { rel: 'icon', href: '/favicon.svg', type: 'image/svg+xml' }
  ],
  htmlAttrs: {
    lang: 'en'
  }
})

useSeoMeta({
  titleTemplate: `%s · ${seo?.siteName}`,
  ogSiteName: seo?.siteName,
  twitterCard: 'summary'
})

provide('navigation', navigation)
</script>

<template>
  <UApp>
    <NuxtLoadingIndicator color="#84cc16" />

    <AppHeader />

    <UMain>
      <NuxtLayout>
        <NuxtPage />
      </NuxtLayout>
    </UMain>

    <AppFooter />

    <ClientOnly>
      <LazyUContentSearch
        :files="files"
        :navigation="navigation"
      />
    </ClientOnly>
  </UApp>
</template>
