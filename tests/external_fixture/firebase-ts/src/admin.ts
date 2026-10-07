import { initializeApp, cert, applicationDefault } from 'firebase-admin/app'
import { getFirestore } from 'firebase-admin/firestore'
import { getAuth } from 'firebase-admin/auth'
import { getDatabase } from 'firebase-admin/database'
import { getStorage } from 'firebase-admin/storage'

initializeApp({ credential: cert(JSON.parse(process.env.FIREBASE_SERVICE_ACCOUNT as string)), storageBucket: 'bookstore-prod.appspot.com' })

const db = getFirestore()

export async function listOrders() {
  return db.collection('orders').where('status', '==', 'open').get()
}

export async function saveReview(id: string) {
  await db.collection(process.env.REVIEWS_COLLECTION as string).doc(id).set({ ok: true })
}

export async function checkout() {
  return getFirestore().runTransaction(async () => 1)
}

export async function whoIs(idToken: string) {
  return getAuth().verifyIdToken(idToken)
}

export async function stock() {
  await getDatabase().ref('/stock').set({ a: 1 })
}

export async function upload(buf: Buffer) {
  await getStorage().bucket().file('covers/a.png').save(buf)
}

export async function uploadNamed(buf: Buffer) {
  await getStorage().bucket('bookstore-exports').file('b.csv').save(buf)
}
