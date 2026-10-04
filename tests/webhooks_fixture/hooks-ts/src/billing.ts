export async function activateSubscription(session) {
  return session.id
}

export async function markInvoicePaid(invoice) {
  return invoice.id
}

export function audit(kind: string) {
  return kind
}
