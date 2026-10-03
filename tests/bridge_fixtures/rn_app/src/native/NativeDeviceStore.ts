import type { TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export interface Spec extends TurboModule {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export default TurboModuleRegistry.getEnforcing<Spec>('DeviceStore');
