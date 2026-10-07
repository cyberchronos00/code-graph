import * as k8s from '@kubernetes/client-node'

const kc = new k8s.KubeConfig()
kc.loadFromCluster()
const batch = kc.makeApiClient(k8s.BatchV1Api)
const core = kc.makeApiClient(k8s.CoreV1Api)

export async function runExport(name: string) {
  await batch.createNamespacedJob({ namespace: 'exports', body: { metadata: { name } } })
}

export async function listWorkers() {
  return core.listNamespacedPod({ namespace: 'workers' })
}
