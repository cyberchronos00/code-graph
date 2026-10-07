import admin from 'firebase-admin'

admin.initializeApp({ credential: admin.credential.cert('./secrets/service-account.json') })

export async function listCarts() {
  return admin.firestore().collection('carts').get()
}

export async function notifyLowStock(token: string) {
  await admin.messaging().send({ token, notification: { title: 'Low stock' } })
}
