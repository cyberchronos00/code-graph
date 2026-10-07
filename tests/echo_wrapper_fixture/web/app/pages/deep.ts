import { ordersChannel } from '~/composables/useRealtime'

function levelThree(s: string) { return ordersChannel(s) }
export function levelFour(s: string) { return levelThree(s) }
