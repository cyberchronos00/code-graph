import { initializeApp, applicationDefault } from 'firebase-admin/app'
import { getFirestore } from 'firebase-admin/firestore'
import { getAuth } from 'firebase-admin/auth'

initializeApp({ credential: applicationDefault() })

export async function listAuthors() {
  return getFirestore().collection('authors').get()
}

export async function removeUser(uid: string) {
  await getAuth().deleteUser(uid)
}
