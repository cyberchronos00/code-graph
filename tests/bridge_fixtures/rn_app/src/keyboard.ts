import { Platform, NativeModules } from 'react-native';
import Keyboard from '@acme/keyboard';

export function hideKeyboard() {
  Keyboard.dismiss();
  Keyboard.onShow(() => {});
}

export function tintBar(color: string) {
  // the native method exists on android only; the call is gated to it
  if (Platform.OS === 'android') {
    NativeModules.CalendarModule.setBarColor(color);
  }
}
