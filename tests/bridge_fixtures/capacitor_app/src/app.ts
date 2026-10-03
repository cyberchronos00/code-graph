import { Plugins } from '@capacitor/core';
import { Camera } from '@capacitor/camera';
import Echo from './plugins/echo';

export async function greet(name: string): Promise<string> {
  const r = await Echo.echo({ value: name });
  return r.value;
}

export function buzz(): Promise<void> {
  return Echo.vibrate();
}

export async function lost(): Promise<void> {
  await Echo.nowhere();
}

export async function deviceInfo() {
  const { DeviceInfo } = Plugins;
  return DeviceInfo.getInfo();
}

export async function photo() {
  return Camera.getPhoto({ quality: 90 });
}
