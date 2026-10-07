import { getMessaging } from 'firebase-admin/messaging'

test('send from a test', async () => {
  await getMessaging().send({ token: 't' })
})
