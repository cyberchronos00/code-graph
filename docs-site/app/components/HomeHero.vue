<template>
  <section class="relative isolate min-h-[100svh] overflow-hidden">
    <HomeGraph class="absolute inset-0 z-0" />

    <div
      class="pointer-events-none absolute inset-0 z-[1] bg-[radial-gradient(ellipse_48%_62%_at_8%_58%,var(--ui-bg)_0%,color-mix(in_oklab,var(--ui-bg)_70%,transparent)_38%,transparent_64%)]"
      aria-hidden="true"
    />
    <div
      class="pointer-events-none absolute inset-x-0 top-0 z-[1] h-24 bg-gradient-to-b from-[var(--ui-bg)] to-transparent"
      aria-hidden="true"
    />
    <div
      class="pointer-events-none absolute inset-x-0 bottom-0 z-[1] h-36 bg-gradient-to-b from-transparent to-[var(--ui-bg)]"
      aria-hidden="true"
    />
    <div
      class="hero-grid pointer-events-none absolute inset-0 z-[1]"
      aria-hidden="true"
    />
    <div
      class="pointer-events-none absolute inset-0 z-[1] bg-[radial-gradient(ellipse_42%_36%_at_72%_42%,rgba(101,163,13,0.16),transparent_70%),radial-gradient(ellipse_32%_28%_at_88%_68%,rgba(109,40,217,0.12),transparent_70%)] dark:bg-[radial-gradient(ellipse_42%_36%_at_72%_42%,rgba(163,230,53,0.14),transparent_70%),radial-gradient(ellipse_34%_30%_at_86%_70%,rgba(139,92,246,0.18),transparent_70%)]"
      aria-hidden="true"
    />

    <div class="relative z-10 mx-auto flex min-h-[100svh] max-w-6xl flex-col justify-center px-4 py-28 sm:px-6 lg:py-32">
      <div class="max-w-3xl">
        <p class="font-mono text-xs tracking-[0.22em] text-lime-800 uppercase dark:text-lime-300">
          cg · local graph
        </p>
        <h1 class="mt-6 max-w-4xl text-[clamp(3.4rem,8.4vw,6.75rem)] leading-[0.9] font-semibold tracking-[-0.045em] text-zinc-950 dark:text-white">
          See what a
          <span class="block">change <span class="text-lime-800 dark:text-lime-300">touches</span></span>
        </h1>
        <p class="mt-8 max-w-xl text-lg leading-relaxed text-zinc-700 sm:text-xl dark:text-zinc-300">
          code-graph builds a local, deterministic graph of routes, calls, data access and platform boundaries. Ask what reaches a table, connection, config key or method, and follow each hop by file:line.
        </p>
        <p class="mt-6 font-mono text-sm text-zinc-800 sm:text-base dark:text-zinc-200">
          <span class="text-lime-800 dark:text-lime-300">$</span>
          cg impact StockService::reserve
        </p>
        <div class="mt-10 flex flex-wrap items-center gap-3">
          <UButton
            to="/docs/install"
            color="primary"
            size="xl"
            icon="i-lucide-book-open"
            class="shadow-[0_12px_40px_-14px_rgba(77,124,15,0.85)]"
          >
            Read the docs
          </UButton>
          <UButton
            to="/docs/cli"
            color="neutral"
            variant="outline"
            size="xl"
          >
            CLI reference
          </UButton>
          <UButton
            to="/docs/viz"
            color="neutral"
            variant="ghost"
            size="xl"
            icon="i-lucide-share-2"
          >
            Visual view
          </UButton>
        </div>
        <ul class="mt-10 flex flex-wrap gap-x-5 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
          <li class="inline-flex items-center gap-1.5">
            <span class="size-1.5 rounded-full bg-lime-700 dark:bg-lime-400" />
            Calls
          </li>
          <li class="inline-flex items-center gap-1.5">
            <span class="size-1.5 rounded-full bg-violet-700 dark:bg-violet-300" />
            Data
          </li>
          <li class="inline-flex items-center gap-1.5">
            <span class="size-1.5 rounded-full bg-zinc-600 dark:bg-zinc-300" />
            Boundaries
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.hero-grid {
  background-image:
    linear-gradient(to right, color-mix(in oklab, var(--ui-border) 65%, transparent) 1px, transparent 1px),
    linear-gradient(to bottom, color-mix(in oklab, var(--ui-border) 65%, transparent) 1px, transparent 1px);
  background-size: 72px 72px;
  mask-image: radial-gradient(ellipse at 68% 42%, black 10%, transparent 70%);
}
</style>
