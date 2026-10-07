import { KubeConfig, AppsV1Api } from '@kubernetes/client-node'

export async function scale(name: string) {
  const kc = new KubeConfig()
  kc.loadFromFile(process.env.KUBECONFIG as string)
  const apps = kc.makeApiClient(AppsV1Api)
  await apps.patchNamespacedDeployment({ name, namespace: 'shop', body: [] })
}
