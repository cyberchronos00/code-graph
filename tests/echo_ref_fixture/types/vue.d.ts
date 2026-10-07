export interface Ref<T = unknown> {
  value: T
}
export function ref<T>(value?: T): Ref<T>
export function shallowRef<T>(value?: T): Ref<T>
