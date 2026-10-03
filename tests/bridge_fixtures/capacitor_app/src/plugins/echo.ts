import { registerPlugin } from '@capacitor/core';

export interface EchoPlugin {
  echo(options: { value: string }): Promise<{ value: string }>;
  ping(): Promise<void>;
  vibrate(): Promise<void>;
  nowhere(): Promise<void>;
}

const Echo = registerPlugin<EchoPlugin>('Echo');

export default Echo;
