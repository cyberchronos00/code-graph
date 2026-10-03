import Store from './native/store';
import Haptics from '../modules/haptics/src/HapticsModule';

export function readSetting(key: string) {
  return Store.getItem(key);
}

export function saveSetting(key: string, value: string) {
  Store.setItem(key, value);
}

export function tap() {
  Haptics.impact('light');
}
