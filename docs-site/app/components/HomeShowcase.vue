<script setup lang="ts">
const clips = [
  {
    title: 'Visual view',
    caption: 'Search, evidence paths, then the planned-change overlay.',
    src: '/media/cg-view-demo.mp4',
    poster: '/media/cg-view-preview.gif',
    to: '/docs/viz',
    link: 'Visual view docs'
  },
  {
    title: 'Terminal',
    caption: 'Index the sample apps, link them, then run reaches, path, impact and plan check.',
    src: '/media/cg-terminal-demo.mp4',
    poster: '/media/cg-terminal-demo.gif',
    to: '/docs/cli',
    link: 'CLI docs'
  },
  {
    title: 'Setup',
    caption: 'Install cg and connect Cursor to the local MCP server.',
    src: '/media/cg-setup-demo.mp4',
    to: '/docs/install',
    link: 'Install docs'
  }
]
</script>

<template>
  <section class="mx-auto max-w-6xl px-4 py-8 sm:px-6">
    <p class="font-mono text-xs tracking-[0.18em] text-cg-lime-800 uppercase dark:text-cg-lime-300">
      In use
    </p>
    <h2 class="mt-2 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
      Watch the graph answer
    </h2>
    <div class="mt-8 grid gap-4 lg:grid-cols-3">
      <article
        v-for="clip in clips"
        :key="clip.src"
        class="overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
      >
        <DocVideo
          :framed="false"
          :src="clip.src"
          :poster="clip.poster"
          :label="clip.title"
        />
        <div class="p-4">
          <h3 class="font-semibold text-zinc-900 dark:text-zinc-50">
            {{ clip.title }}
          </h3>
          <p class="mt-1 text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
            {{ clip.caption }}
          </p>
          <NuxtLink
            :to="clip.to"
            class="mt-3 inline-flex text-sm font-medium text-cg-lime-800 underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cg-lime-700 dark:text-cg-lime-300 dark:focus-visible:outline-cg-lime-300"
          >
            {{ clip.link }}
          </NuxtLink>
        </div>
      </article>
    </div>

    <article class="mt-4 overflow-hidden rounded-2xl border border-zinc-200 bg-white lg:grid lg:grid-cols-[minmax(0,1.35fr)_minmax(0,0.75fr)] dark:border-zinc-800 dark:bg-zinc-900">
      <DocVideo
        :framed="false"
        src="/media/cg-agent-demo.mp4"
        poster="/media/cg-agent-compare.png"
        label="Agent demo"
      />
      <div class="flex flex-col justify-center p-5">
        <p class="font-mono text-[11px] tracking-[0.16em] text-cg-violet-800 uppercase dark:text-cg-violet-300">
          Secondary
        </p>
        <h3 class="mt-2 text-xl font-semibold text-zinc-900 dark:text-zinc-50">
          Agent over MCP
        </h3>
        <p class="mt-2 text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
          A live session where the agent reads the graph before it edits.
        </p>
        <NuxtLink
          to="/docs/mcp"
          class="mt-4 inline-flex text-sm font-medium text-cg-lime-800 underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cg-lime-700 dark:text-cg-lime-300 dark:focus-visible:outline-cg-lime-300"
        >
          MCP docs
        </NuxtLink>
      </div>
    </article>
  </section>
</template>
