import { SecretManagerServiceClient } from '@google-cloud/secret-manager'
import { KeyManagementServiceClient } from '@google-cloud/kms'
import { SecretClient } from '@azure/keyvault-secrets'
import { DefaultAzureCredential, ClientSecretCredential } from '@azure/identity'

const sm = new SecretManagerServiceClient()
const kmsClient = new KeyManagementServiceClient({ keyFilename: process.env.GOOGLE_APPLICATION_CREDENTIALS })

export async function dbPassword() {
  const [v] = await sm.accessSecretVersion({ name: 'projects/bookstore-prod/secrets/db-password/versions/latest' })
  return v
}

export async function envSecret() {
  return sm.accessSecretVersion({ name: process.env.SECRET_RESOURCE })
}

export async function seal(data: Buffer) {
  return kmsClient.encrypt({ name: 'projects/bookstore-prod/locations/eu/keyRings/shop/cryptoKeys/orders', plaintext: data })
}

const vault = new SecretClient('https://bookstore-vault.vault.azure.net', new DefaultAzureCredential())

export async function apiKey() {
  return vault.getSecret('payments-api-key')
}

export async function explicit() {
  const cred = new ClientSecretCredential(process.env.AZURE_TENANT_ID as string, process.env.AZURE_CLIENT_ID as string, process.env.AZURE_CLIENT_SECRET as string)
  const client = new SecretClient(process.env.KEY_VAULT_URL as string, cred)
  return client.getSecret('smtp-password')
}
