import { NativeEventEmitter, NativeModules, DeviceEventEmitter } from 'react-native';

const emitter = new NativeEventEmitter(NativeModules.Downloader);

export function watchDownloads(onDone: (id: string) => void) {
  emitter.addListener('downloadProgress', (e: any) => console.log(e));
  emitter.addListener('downloadDone', onDone);
  emitter.addListener('downloadCancelled', () => {});
  DeviceEventEmitter.addListener('keyboardDidShow', () => {});
  DeviceEventEmitter.addListener('neverEmitted', () => {});
}

export function anyModule(name: string, evt: string) {
  NativeModules[name].start();
  emitter.addListener(evt, () => {});
}
