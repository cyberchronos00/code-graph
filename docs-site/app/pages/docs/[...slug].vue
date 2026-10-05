<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'
import { findPageHeadline } from '@nuxt/content/utils'
import { withoutTrailingSlash } from 'ufo'
import { stripDocChrome } from '~/utils/doc-page'

definePageMeta({
  layout: 'docs'
})

const route = useRoute()
const { toc } = useAppConfig()
const navigation = inject<Ref<ContentNavigationItem[]>>('navigation')
const routePath = computed(() => withoutTrailingSlash(route.path))

const { data: page } = await useAsyncData(routePath.value, () =>
  queryCollection('docs').path(routePath.value).first()
)

if (!page.value) {
  throw createError({ statusCode: 404, statusMessage: 'Page not found', fatal: true })
}

stripDocChrome(page.value)

const { data: surround } = await useAsyncData(`${routePath.value}-surround`, () => {
  return queryCollectionItemSurroundings('docs', routePath.value, {
    fields: ['description']
  })
})

const title = page.value.seo?.title || page.value.title
const description = page.value.seo?.description || page.value.description

useSeoMeta({
  title,
  ogTitle: title,
  description,
  ogDescription: description
})

const headline = computed(() => findPageHeadline(navigation?.value, page.value?.path))

const links = computed(() => {
  const editStem = page.value?.stem || ''
  const out = []
  if (toc?.bottom?.edit && editStem) {
    out.push({
      icon: 'i-lucide-external-link',
      label: 'Edit this page',
      to: `${toc.bottom.edit}/${editStem}.md`,
      target: '_blank'
    })
  }
  return [...out, ...(toc?.bottom?.links || [])]
})
</script>

<template>
  <UPage v-if="page">
    <UPageHeader
      :title="page.title"
      :headline="headline"
    />

    <UPageBody>
      <ContentRenderer :value="page" />

      <USeparator v-if="surround?.length" />

      <UContentSurround :surround="surround" />
    </UPageBody>

    <template
      v-if="page.body?.toc?.links?.length"
      #right
    >
      <UContentToc
        :title="toc?.title"
        :links="page.body.toc.links"
      >
        <template
          v-if="toc?.bottom"
          #bottom
        >
          <div class="hidden lg:block space-y-6 mt-6">
            <USeparator type="dashed" />
            <UPageLinks
              :title="toc.bottom.title"
              :links="links"
            />
          </div>
        </template>
      </UContentToc>
    </template>
  </UPage>
</template>
