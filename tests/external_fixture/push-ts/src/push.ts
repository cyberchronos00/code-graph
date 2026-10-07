import { getMessaging } from 'firebase-admin/messaging'
import apn from '@parse/node-apn'
import webpush from 'web-push'
import { Expo } from 'expo-server-sdk'

export async function notifyShipped(token: string) {
  await getMessaging().send({ token, notification: { title: 'Your order shipped' } })
}

export async function notifyIos(deviceToken: string) {
  const provider = new apn.Provider({
    token: { key: process.env.APNS_KEY_PATH, keyId: process.env.APNS_KEY_ID, teamId: process.env.APNS_TEAM_ID },
    production: true,
  })
  await provider.send(new apn.Notification(), deviceToken)
}

export async function notifyIosLiteral(deviceToken: string) {
  const provider = new apn.Provider({ token: { key: 'certs/AuthKey_BOOKSTORE.p8', keyId: 'ABC123DEFG', teamId: 'TEAM123456' } })
  await provider.send(new apn.Notification(), deviceToken)
}

export async function notifyBrowser(sub: object) {
  webpush.setVapidDetails('mailto:ops@bookstore.example', process.env.VAPID_PUBLIC_KEY, process.env.VAPID_PRIVATE_KEY)
  await webpush.sendNotification(sub, 'Back in stock')
}

export async function notifyExpo(to: string) {
  const expo = new Expo({ accessToken: process.env.EXPO_ACCESS_TOKEN })
  await expo.sendPushNotificationsAsync([{ to, body: 'Back in stock' }])
}
