import { Platform } from 'react-native'
import { syncNotes } from '../sync'

export function openSettings(): void {
  if (Platform.OS === 'ios') {
    openIosSettings()
  } else if (Platform.OS === 'android') {
    openAndroidSettings()
  }
  syncNotes()
}

export const shadow = Platform.select({
  ios: () => iosShadow(),
  android: () => androidElevation(),
  default: () => webShadow(),
})

function openIosSettings(): void {}
function openAndroidSettings(): void {}
function iosShadow(): string { return 'shadow' }
function androidElevation(): string { return 'elevation' }
function webShadow(): string { return 'box-shadow' }
