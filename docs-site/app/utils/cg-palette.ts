/**
 * Shared PCCS theme for the docs site (and later the blog).
 * Sheet peaks: lime #CAF902, purple #A600E7.
 * Named anchors are exact. Other 50–950 stops are the OKLCH ramp on those hues
 * (lime ~122°, violet ~311°) so Nuxt UI has a full scale.
 */
import type { GraphKind } from './home-graph'

export const cgLime = {
  50: '#F7FBE8',
  100: '#EBF9CF',
  200: '#DDF5A9',
  300: '#CCED7B',
  400: '#BCE24C',
  500: '#A8D400',
  600: '#93B600',
  700: '#668000',
  800: '#4E6104',
  900: '#2E3B03',
  950: '#1C2301',
  neon: '#CAF902'
} as const

export const cgViolet = {
  50: '#FBF5FF',
  100: '#F6EAFE',
  200: '#EED7FE',
  300: '#E1B7FD',
  400: '#C781F4',
  500: '#B855F0',
  600: '#A600E7',
  700: '#830CB6',
  800: '#5C0D81',
  900: '#3F0B59',
  950: '#2A003A',
  neon: '#A70FEF'
} as const

export interface NodeTone {
  outline: string
  fill: string
  aura: string
}

export const cgGraph = {
  bgLight: '#FAFAFA',
  bgDark: '#09090B',
  onLime: '#1C2301',
  calls: {
    dark: { outline: '#CAF902', fill: '#93B600', aura: '#C3E960' } satisfies NodeTone,
    light: { outline: '#738C01', fill: '#B6D045', aura: '#EAF7C2' } satisfies NodeTone
  },
  data: {
    dark: { outline: '#A600E7', fill: '#8A00C0', aura: '#C686FF' } satisfies NodeTone,
    light: { outline: '#8A00C0', fill: '#D1AFF7', aura: '#F3E8FF' } satisfies NodeTone
  },
  boundaries: {
    dark: { outline: '#A1A1AA', fill: '#71717A', aura: '#D4D4D8' } satisfies NodeTone,
    light: { outline: '#52525B', fill: '#A1A1AA', aura: '#E4E4E7' } satisfies NodeTone
  },
  edgeIdle: { dark: '#3F3F46', light: '#A1A1AA' },
  edgeCall: { dark: '#93B600', light: '#738C01' },
  edgeData: { dark: '#A600E7', light: '#8A00C0' },
  pulse: { dark: '#CAF902', light: '#738C01' },
  pulseRing: { dark: '#FFFFFF', light: '#18181B' },
  fillAlpha: { dark: 0.1, light: 0.16 }
} as const

export function toneForKind(kind: GraphKind, dark: boolean): NodeTone {
  if (kind === 'violet') return dark ? cgGraph.data.dark : cgGraph.data.light
  if (kind === 'zinc') return dark ? cgGraph.boundaries.dark : cgGraph.boundaries.light
  return dark ? cgGraph.calls.dark : cgGraph.calls.light
}

export function linkColor(kindA: GraphKind, kindB: GraphKind, dark: boolean): string {
  if (kindA === 'violet' || kindB === 'violet') {
    return dark ? cgGraph.edgeData.dark : cgGraph.edgeData.light
  }
  if (kindA === 'zinc' && kindB === 'zinc') {
    return dark ? cgGraph.edgeIdle.dark : cgGraph.edgeIdle.light
  }
  return dark ? cgGraph.edgeCall.dark : cgGraph.edgeCall.light
}
