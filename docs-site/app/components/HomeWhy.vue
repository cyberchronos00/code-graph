<script setup lang="ts">
const reasons = [
  {
    icon: 'i-lucide-repeat',
    title: 'Deterministic',
    text: 'The same tree produces the same graph. Re-index, and the hops do not drift.'
  },
  {
    icon: 'i-lucide-laptop',
    title: 'Local',
    text: 'The database stays on your machine. Queries never need a hosted index.'
  },
  {
    icon: 'i-lucide-file-search',
    title: 'Evidence',
    text: 'Every hop is a file:line, so a path is something you can open.'
  },
  {
    icon: 'i-lucide-bot',
    title: 'Agents',
    text: 'MCP exposes the graph, so an agent can read impact before it writes.'
  },
  {
    icon: 'i-lucide-waypoints',
    title: 'Boundaries',
    text: 'Routes, calls, tables, config keys and platform edges share one index.'
  }
]
</script>

<template>
  <section class="mx-auto max-w-6xl px-4 py-16 sm:px-6">
    <p class="font-mono text-xs tracking-[0.18em] text-cg-lime-800 uppercase dark:text-cg-lime-300">
      Why cg
    </p>
    <h2 class="mt-2 max-w-xl text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
      A graph you can trust before you edit
    </h2>
    <ul class="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <li
        v-for="(reason, index) in reasons"
        :key="reason.title"
        class="rounded-2xl border border-zinc-200 bg-white/80 p-4 dark:border-zinc-800 dark:bg-zinc-900/70"
      >
        <div class="flex items-center justify-between gap-2">
          <UIcon
            :name="reason.icon"
            class="size-5 text-cg-lime-800 dark:text-cg-lime-300"
          />
          <span class="font-mono text-[11px] text-zinc-400 dark:text-zinc-500">
            0{{ index + 1 }}
          </span>
        </div>
        <h3 class="mt-4 text-sm font-semibold text-zinc-900 dark:text-zinc-50">
          {{ reason.title }}
        </h3>
        <p class="mt-1.5 text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
          {{ reason.text }}
        </p>
      </li>
    </ul>
  </section>
</template>
