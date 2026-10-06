<script setup lang="ts">
const stacks = [
  {
    title: 'PHP',
    detail: 'Laravel',
    icons: [
      { name: 'i-simple-icons-php', label: 'PHP' },
      { name: 'i-simple-icons-laravel', label: 'Laravel' }
    ]
  },
  {
    title: 'Python',
    detail: 'Django, FastAPI, Flask',
    icons: [
      { name: 'i-simple-icons-python', label: 'Python' },
      { name: 'i-simple-icons-django', label: 'Django' },
      { name: 'i-simple-icons-fastapi', label: 'FastAPI' },
      { name: 'i-simple-icons-flask', label: 'Flask' }
    ]
  },
  {
    title: 'TypeScript and JavaScript',
    detail: 'Nuxt, Vue, NestJS, Next.js, Astro, Express, Fastify, Koa, Hono. The extractor runs on Node or Bun.',
    icons: [
      { name: 'i-simple-icons-typescript', label: 'TypeScript' },
      { name: 'i-simple-icons-javascript', label: 'JavaScript' },
      { name: 'i-simple-icons-nuxt', label: 'Nuxt' },
      { name: 'i-simple-icons-vuedotjs', label: 'Vue' },
      { name: 'i-simple-icons-nestjs', label: 'NestJS' },
      { name: 'i-simple-icons-nextdotjs', label: 'Next.js' },
      { name: 'i-simple-icons-astro', label: 'Astro' },
      { name: 'i-simple-icons-express', label: 'Express' },
      { name: 'i-simple-icons-fastify', label: 'Fastify' },
      { name: 'i-simple-icons-koa', label: 'Koa' },
      { name: 'i-simple-icons-hono', label: 'Hono' },
      { name: 'i-simple-icons-bun', label: 'Bun' }
    ]
  },
  {
    title: 'Dart',
    detail: 'Flutter',
    icons: [
      { name: 'i-simple-icons-flutter', label: 'Flutter' },
      { name: 'i-simple-icons-dart', label: 'Dart' }
    ]
  },
  {
    title: 'Kotlin',
    detail: 'Android, Kotlin Multiplatform',
    icons: [
      { name: 'i-simple-icons-kotlin', label: 'Kotlin' },
      { name: 'i-simple-icons-android', label: 'Android' }
    ]
  },
  {
    title: 'Java',
    detail: 'Spring Boot',
    icons: [
      { name: 'i-simple-icons-openjdk', label: 'Java' }
    ]
  },
  {
    title: 'Swift',
    detail: 'iOS / SwiftPM',
    icons: [
      { name: 'i-simple-icons-swift', label: 'Swift' }
    ]
  },
  {
    title: 'Rust',
    detail: 'Supported natively',
    icons: [
      { name: 'i-simple-icons-rust', label: 'Rust' }
    ]
  },
  {
    title: 'C and C++',
    detail: 'Supported natively',
    icons: [
      { name: 'i-simple-icons-c', label: 'C' },
      { name: 'i-simple-icons-cplusplus', label: 'C++' }
    ]
  }
]
</script>

<template>
  <section class="mx-auto max-w-6xl px-4 py-16 sm:px-6">
    <p class="font-mono text-xs tracking-[0.18em] text-cg-lime-800 uppercase dark:text-cg-lime-300">
      Stacks
    </p>
    <h2 class="mt-2 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
      Languages the index reads
    </h2>
    <p class="mt-2 max-w-2xl text-sm text-zinc-600 dark:text-zinc-300">
      Go and other languages can be imported through SCIP.
    </p>
    <ul class="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <li
        v-for="stack in stacks"
        :key="stack.title"
        class="rounded-2xl border border-zinc-200 bg-white/80 p-4 dark:border-zinc-800 dark:bg-zinc-900/70"
      >
        <div class="flex flex-wrap gap-2">
          <span
            v-for="icon in stack.icons"
            :key="icon.name"
            class="inline-flex size-9 items-center justify-center rounded-lg bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-100"
            :aria-label="icon.label"
          >
            <UIcon
              :name="icon.name"
              class="size-5"
              aria-hidden="true"
            />
          </span>
        </div>
        <h3 class="mt-3 font-semibold text-zinc-900 dark:text-zinc-50">
          {{ stack.title }}
        </h3>
        <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-300">
          {{ stack.detail }}
        </p>
      </li>
    </ul>
  </section>
</template>
