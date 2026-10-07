import { privateChannel } from '~/composables/useRealtime'

export function openDesk(storeId: string, dynamicName: string) {
  const ch = privateChannel(`store.${storeId}.desk`)
  ch.listen('.DeskUpdated', () => {})
  privateChannel(dynamicName).listen('.Ignored', () => {})
}
