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
    detail: 'Nuxt, Vue, NestJS, Next.js, Express, Fastify, Koa, Hono',
    icons: [
      { name: 'i-simple-icons-typescript', label: 'TypeScript' },
      { name: 'i-simple-icons-javascript', label: 'JavaScript' },
      { name: 'i-simple-icons-nuxt', label: 'Nuxt' },
      { name: 'i-simple-icons-vuedotjs', label: 'Vue' },
      { name: 'i-simple-icons-nestjs', label: 'NestJS' },
      { name: 'i-simple-icons-nextdotjs', label: 'Next.js' },
      { name: 'i-simple-icons-express', label: 'Express' },
      { name: 'i-simple-icons-fastify', label: 'Fastify' },
      { name: 'i-simple-icons-koa', label: 'Koa' },
      { name: 'i-simple-icons-hono', label: 'Hono' }
    ]
  },
  {
    title: 'Flutter',
    detail: 'Dart',
    icons: [
      { name: 'i-simple-icons-flutter', label: 'Flutter' },
      { name: 'i-simple-icons-dart', label: 'Dart' }
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
    <p class="font-mono text-xs tracking-[0.18em] text-lime-800 uppercase dark:text-lime-300">
      Stacks
    </p>
    <h2 class="mt-2 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
      Languages the index reads
    </h2>
    <p class="mt-2 max-w-2xl text-sm text-zinc-600 dark:text-zinc-300">
      Other languages can be imported through SCIP.
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
          >
            <UIcon
              :name="icon.name"
              class="size-5"
            />
            <span class="sr-only">{{ icon.label }}</span>
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
