import { registerPlugin } from '@capacitor/core';
import { Keyboard } from '@capacitor/keyboard';

export interface GeoPlugin { start(): Promise<void>; addListener(e: string, cb: (d: any) => void): any }
export const Geo = registerPlugin<GeoPlugin>('Geo');

export function track() {
  Geo.start();
  Geo.addListener('locationChanged', (d) => console.log(d));
  Geo.addListener('geoMissing', () => {});
  Keyboard.addListener('keyboardWillShow', () => {});
}
