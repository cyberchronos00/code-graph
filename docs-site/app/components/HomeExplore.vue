<script setup lang="ts">
import { docGroups } from '~/utils/docs-nav'

const snippet = `pipx install cg-code-graph
# or: uv tool install cg-code-graph
cg doctor`

const copied = ref(false)

async function copySnippet() {
  try {
    await navigator.clipboard.writeText(snippet)
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 1600)
  }
  catch {
    copied.value = false
  }
}
</script>

<template>
  <section class="mx-auto max-w-6xl px-4 py-8 sm:px-6">
    <div class="grid gap-8 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:items-start">
      <div>
        <p class="font-mono text-xs tracking-[0.18em] text-cg-lime-800 uppercase dark:text-cg-lime-300">
          Install
        </p>
        <h2 class="mt-2 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Python 3.11+
        </h2>
        <p class="mt-2 text-sm text-zinc-600 dark:text-zinc-300">
          The package is <span class="font-mono text-zinc-800 dark:text-zinc-200">cg-code-graph</span>. The commands are <span class="font-mono text-zinc-800 dark:text-zinc-200">cg</span> and <span class="font-mono text-zinc-800 dark:text-zinc-200">cg-mcp</span>.
        </p>
        <div class="mt-5 overflow-hidden rounded-2xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950">
          <div class="flex items-center justify-between gap-3 border-b border-zinc-200 px-4 py-2 dark:border-zinc-800">
            <span class="font-mono text-[11px] tracking-wide text-zinc-500 uppercase">shell</span>
            <button
              type="button"
              class="rounded-md px-2 py-1 font-mono text-xs text-cg-lime-800 hover:bg-zinc-200/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cg-lime-700 dark:text-cg-lime-300 dark:hover:bg-zinc-800 dark:focus-visible:outline-cg-lime-300"
              @click="copySnippet"
            >
              {{ copied ? 'Copied' : 'Copy' }}
            </button>
          </div>
          <pre class="overflow-x-auto p-4 font-mono text-sm text-zinc-800 dark:text-zinc-100"><code>{{ snippet }}</code></pre>
        </div>
        <p
          class="sr-only"
          role="status"
        >
          {{ copied ? 'Copied install command' : '' }}
        </p>
        <p class="mt-4 text-sm text-zinc-600 dark:text-zinc-300">
          macOS and Linux can also use <code class="font-mono text-xs text-zinc-800 dark:text-zinc-200">install.sh</code>.
          Windows uses <code class="font-mono text-xs text-zinc-800 dark:text-zinc-200">install.ps1</code>.
          Details, updates and extractor setup are in
          <NuxtLink
            to="/docs/install"
            class="font-medium text-cg-lime-800 underline-offset-4 hover:underline dark:text-cg-lime-300"
          >
            Install
          </NuxtLink>.
        </p>
      </div>

      <div>
        <p class="font-mono text-xs tracking-[0.18em] text-cg-lime-800 uppercase dark:text-cg-lime-300">
          Explore docs
        </p>
        <h2 class="mt-2 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          The pages live in docs/
        </h2>
        <div class="mt-5 grid gap-3 sm:grid-cols-2">
          <article
            v-for="group in docGroups"
            :key="group.title"
            class="rounded-2xl border border-zinc-200 bg-white/80 p-4 dark:border-zinc-800 dark:bg-zinc-900/70"
          >
            <h3 class="text-xs font-medium tracking-wide text-cg-lime-800 uppercase dark:text-cg-lime-300">
              {{ group.title }}
            </h3>
            <ul class="mt-3 space-y-1.5">
              <li
                v-for="item in group.items"
                :key="item.slug"
              >
                <NuxtLink
                  :to="`/docs/${item.slug}`"
                  class="text-sm text-zinc-800 hover:text-cg-lime-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cg-lime-700 dark:text-zinc-200 dark:hover:text-cg-lime-300 dark:focus-visible:outline-cg-lime-300"
                >
                  {{ item.title }}
                </NuxtLink>
              </li>
            </ul>
          </article>
        </div>
      </div>
    </div>
  </section>
</template>
