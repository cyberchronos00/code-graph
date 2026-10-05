<script setup lang="ts">
import type { ContentNavigationItem } from '@nuxt/content'

const navigation = inject<Ref<ContentNavigationItem[]>>('navigation')
const { header } = useAppConfig()
</script>

<template>
  <UHeader :to="header?.to || '/'">
    <template #title>
      <span class="font-semibold tracking-tight text-highlighted">
        <span class="text-primary">cg</span>
        <span class="text-muted font-normal"> / </span>
        code-graph
      </span>
    </template>

    <UContentSearchButton
      v-if="header?.search"
      :collapsed="false"
      class="w-full max-w-sm"
    />

    <template #right>
      <UButton
        to="/docs/install"
        color="neutral"
        variant="ghost"
        class="hidden sm:inline-flex"
      >
        Docs
      </UButton>

      <UContentSearchButton
        v-if="header?.search"
        class="lg:hidden"
      />

      <UColorModeButton v-if="header?.colorMode" />

      <UButton
        v-for="(link, index) of header?.links || []"
        :key="index"
        color="neutral"
        variant="ghost"
        v-bind="link"
      />
    </template>

    <template #body>
      <UContentNavigation
        highlight
        :navigation="navigation"
      />
    </template>
  </UHeader>
</template>
