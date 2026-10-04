import { wrap } from 'comlink'

export const math = wrap(new Worker(new URL('./workers/math.worker.ts', import.meta.url)))
