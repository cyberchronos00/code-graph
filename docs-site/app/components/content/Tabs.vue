<script setup lang="ts">
import { computed, onBeforeUpdate, ref } from 'vue'
import type { TabsItem as UiTabsItem } from '@nuxt/ui'

const props = withDefaults(defineProps<{
  /** Tab `value` to select first. Defaults to the first item (`0`). */
  defaultValue?: string
  class?: string
}>(), {
  defaultValue: '0'
})

const slots = defineSlots<{
  default?: () => unknown
}>()

const model = ref(props.defaultValue)
const rerenderCount = ref(0)

type MdcTab = UiTabsItem & { component: unknown }

const items = computed<MdcTab[]>(() => {
  // Re-read the default slot after each update so markdown panels stay current.
  void rerenderCount.value
  const nodes = slots.default?.() ?? []
  return flatten(nodes).filter((item) => item.label)
})

onBeforeUpdate(() => {
  rerenderCount.value++
})

function flatten(nodes: unknown[]): MdcTab[] {
  const out: MdcTab[] = []
  for (const node of nodes) {
    collect(node, out)
  }
  return out
}

function collect(node: unknown, out: MdcTab[]) {
  if (!node || typeof node !== 'object') return
  const vnode = node as {
    type?: unknown
    props?: Record<string, unknown> | null
    children?: unknown
  }
  if (typeof vnode.type === 'symbol') {
    if (Array.isArray(vnode.children)) {
      for (const child of vnode.children) collect(child, out)
    }
    return
  }
  const label = vnode.props?.label
  if (typeof label !== 'string' || !label.trim()) return
  const explicit = vnode.props?.value
  out.push({
    label,
    icon: typeof vnode.props?.icon === 'string' ? vnode.props.icon : undefined,
    badge: typeof vnode.props?.badge === 'string' ? vnode.props.badge : undefined,
    value: explicit != null && explicit !== '' ? String(explicit) : String(out.length),
    component: vnode
  })
}
</script>

<template>
  <UTabs
    v-model="model"
    color="primary"
    variant="pill"
    size="sm"
    :items="items"
    :unmount-on-hide="false"
    :class="['cg-tabs not-prose my-5 w-full', props.class]"
    :ui="{
      list: 'bg-zinc-100 ring ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-800 overflow-x-auto',
      trigger: 'grow-0 shrink-0',
      label: 'overflow-visible whitespace-nowrap',
      content: 'pt-3'
    }"
  >
    <template #content="{ item }">
      <div class="prose prose-zinc dark:prose-invert max-w-none">
        <component :is="(item as MdcTab).component" />
      </div>
    </template>
  </UTabs>
</template>
