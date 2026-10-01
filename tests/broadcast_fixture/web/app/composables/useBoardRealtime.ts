import { useEchoClient } from './useEcho'

export function useBoardRealtime(boardId: number, teamId: number) {
  const echo = useEchoClient()
  echo.private(`board.${boardId}`).listen('TaskMoved', () => {})

  const activity = echo.private(`board.${boardId}.activity`)
  activity.listen('.board.activity', () => {})

  echo.join(`team.${teamId}.lobby`).here(() => {})
  // nobody publishes here and the backend has no such channel
  echo.private(`board.${boardId}.archive.events`).listen('BoardArchived', () => {})

  return () => echo.leave(`board.${boardId}`)
}
