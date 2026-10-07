import { initializeApp } from 'firebase/app'
import { getFirestore, collection, getDocs } from 'firebase/firestore'
import { getAuth, signInWithEmailAndPassword } from 'firebase/auth'

const app = initializeApp({ apiKey: 'public-web-key', projectId: 'bookstore-prod' })

export async function loadCatalog() {
  const db = getFirestore(app)
  return getDocs(collection(db, 'catalog'))
}

export async function login(email: string, pw: string) {
  return signInWithEmailAndPassword(getAuth(app), email, pw)
}
