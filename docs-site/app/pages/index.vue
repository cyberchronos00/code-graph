<script setup lang="ts">
import { docGroups } from '~/utils/docs-nav'

useSeoMeta({
  title: 'code-graph',
  titleTemplate: '%s',
  description: 'A deterministic dependency graph for the code you and your agent are about to change.'
})

</script>

<template>
  <UPage>
    <UPageHero
      title="See what a change touches"
      description="code-graph builds a local, deterministic graph of routes, calls, data access and platform boundaries. Ask what reaches a table, connection, config key or method, and follow each hop by file:line."
      :links="[
        { label: 'Read the docs', to: '/docs/install', icon: 'i-lucide-book-open', color: 'primary' },
        { label: 'CLI reference', to: '/docs/cli', color: 'neutral', variant: 'subtle' }
      ]"
    />

    <UPageSection
      title="Install"
      description="Python 3.11+. The package is cg-code-graph; the commands are cg and cg-mcp."
    >
      <div class="max-w-2xl">
        <pre class="overflow-x-auto rounded-lg bg-muted p-4 text-sm text-default font-mono"><code>pipx install cg-code-graph
# or: uv tool install cg-code-graph
cg doctor</code></pre>
        <p class="mt-4 text-sm text-muted">
          macOS and Linux can also use
          <code class="font-mono text-xs">install.sh</code>.
          Windows uses
          <code class="font-mono text-xs">install.ps1</code>.
          Details, updates and extractor setup are in
          <NuxtLink
            to="/docs/install"
            class="text-primary"
          >Install</NuxtLink>.
        </p>
      </div>
    </UPageSection>

    <UPageSection
      title="Documentation"
      description="The pages are the markdown files in docs/. This site does not copy that tree."
    >
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <UCard
          v-for="group in docGroups"
          :key="group.title"
        >
          <p class="text-xs font-medium uppercase tracking-wide text-primary">
            {{ group.title }}
          </p>
          <ul class="mt-3 space-y-1.5">
            <li
              v-for="item in group.items"
              :key="item.slug"
            >
              <NuxtLink
                :to="`/docs/${item.slug}`"
                class="text-sm text-default hover:text-primary"
              >
                {{ item.title }}
              </NuxtLink>
            </li>
          </ul>
        </UCard>
      </div>
    </UPageSection>
  </UPage>
</template>
