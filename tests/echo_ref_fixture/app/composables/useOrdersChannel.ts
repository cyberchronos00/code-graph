import Echo from 'laravel-echo'
import { ref } from 'vue'

export function useOrdersChannel(userId: number) {
  const echo = ref<Echo<'reverb'> | null>(null)
  echo.value = new Echo({ broadcaster: 'reverb', key: 'local' })
  return echo.value.private(`shop.user.${userId}`).listen('.CartUpdated', () => {})
}
