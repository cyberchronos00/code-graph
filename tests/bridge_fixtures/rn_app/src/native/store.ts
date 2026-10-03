import { NativeModules } from 'react-native';
import type { Spec } from './NativeDeviceStore';

// @ts-expect-error global
const isTurbo = global.__turboModuleProxy != null;
const StoreModule: Spec = isTurbo ? require('./NativeDeviceStore').default : NativeModules.DeviceStore;
const Store = StoreModule || null;

export default Store;
