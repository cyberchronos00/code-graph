import { NativeEventEmitter, NativeModules } from 'react-native';
import type { Spec } from './NativeKeyboard';

// @ts-expect-error global
const isTurbo = global.__turboModuleProxy != null;
const KeyboardModule: Spec = isTurbo ? require('./NativeKeyboard').default : NativeModules.Keyboard;
const emitter = new NativeEventEmitter(KeyboardModule as any);

// JS helpers merged onto the native module are not native methods
const Keyboard = Object.assign(KeyboardModule, {
  onShow: (listener: () => void) => emitter.addListener('show', listener),
});

export default Keyboard;
