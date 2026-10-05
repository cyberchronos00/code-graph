<script setup lang="ts">
interface GhUser {
  login: string
  html_url: string
  avatar_url: string
  type: string
  contributions: number
}

const { data: contributors, refresh } = await useAsyncData('cg-contributors', async () => {
  try {
    const rows = await $fetch<GhUser[]>('https://api.github.com/repos/cyberchronos00/code-graph/contributors', {
      query: { per_page: 40 },
      headers: {
        Accept: 'application/vnd.github+json',
        'User-Agent': 'code-graph-docs'
      },
      timeout: 8000
    })
    return (rows || [])
      .filter(row => row.type === 'User' && !row.login.endsWith('[bot]'))
      .slice(0, 24)
  }
  catch {
    return []
  }
}, {
  default: () => []
})

onMounted(() => {
  if (!contributors.value?.length) {
    refresh()
  }
})
</script>

<template>
  <section class="mx-auto max-w-6xl px-4 pt-16 pb-20 sm:px-6">
    <p class="font-mono text-xs tracking-[0.18em] text-lime-800 uppercase dark:text-lime-300">
      People
    </p>
    <h2 class="mt-2 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
      Contributors
    </h2>
    <ul
      v-if="contributors?.length"
      class="mt-6 flex flex-wrap gap-2"
    >
      <li
        v-for="person in contributors"
        :key="person.login"
      >
        <a
          :href="person.html_url"
          target="_blank"
          rel="noreferrer"
          class="inline-flex items-center gap-2 rounded-full border border-zinc-200 bg-white py-1 pr-3 pl-1 text-sm text-zinc-800 hover:border-lime-700/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:border-lime-300/40 dark:focus-visible:outline-lime-300"
        >
          <img
            :src="person.avatar_url"
            :alt="`${person.login} on GitHub`"
            width="32"
            height="32"
            loading="lazy"
            class="size-8 rounded-full"
          >
          <span>{{ person.login }}</span>
          <span class="font-mono text-xs text-zinc-500 dark:text-zinc-400">{{ person.contributions }}</span>
        </a>
      </li>
    </ul>
    <p
      v-else
      class="mt-4 text-sm text-zinc-600 dark:text-zinc-300"
    >
      Contributor list unavailable offline.
      <a
        href="https://github.com/cyberchronos00/code-graph/graphs/contributors"
        target="_blank"
        rel="noreferrer"
        class="font-medium text-lime-800 underline-offset-4 hover:underline dark:text-lime-300"
      >
        View on GitHub
      </a>.
    </p>
  </section>
</template>
