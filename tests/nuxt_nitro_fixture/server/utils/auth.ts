export function requireUser(event: { context: Record<string, unknown> }) {
  if (!event.context.user) throw new Error('unauthorized')
}
