import { KMSClient, EncryptCommand, DecryptCommand } from '@aws-sdk/client-kms'

const kms = new KMSClient({ region: 'eu-west-1' })

export async function sealLiteral(data: Uint8Array) {
  return kms.send(new EncryptCommand({ KeyId: 'alias/bookstore-orders', Plaintext: data }))
}

export async function sealEnv(data: Uint8Array) {
  return kms.send(new EncryptCommand({ KeyId: process.env.ORDERS_KMS_KEY_ID, Plaintext: data }))
}

export async function openExplicit(blob: Uint8Array) {
  const client = new KMSClient({ region: 'eu-west-1', credentials: { accessKeyId: process.env.AWS_ACCESS_KEY_ID as string, secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY as string } })
  return client.send(new DecryptCommand({ KeyId: 'alias/bookstore-orders', CiphertextBlob: blob }))
}
