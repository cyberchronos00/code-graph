import { save } from './storage/storage'
import { tap } from './haptics'

export function syncNotes(): string {
  tap()
  return save('notes')
}
