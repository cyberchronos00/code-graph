<script setup lang="ts">
import type { PropType } from 'vue'

const props = defineProps({
  href: {
    type: String,
    default: ''
  },
  target: {
    type: String as PropType<'_blank' | '_parent' | '_self' | '_top' | (string & {}) | null>,
    default: undefined,
    required: false
  }
})

const isVideo = computed(() => /\.(mp4|webm)(?:$|[?#])/i.test(props.href))

const poster = computed(() =>
  props.href.includes('cg-view-demo.mp4') ? '/media/cg-view-preview.gif' : undefined
)
</script>

<template>
  <DocVideo
    v-if="isVideo"
    :src="props.href"
    :poster="poster"
  />
  <ULink
    v-else
    :to="props.href"
    :target="props.target"
  >
    <slot mdc-unwrap="p" />
  </ULink>
</template>
