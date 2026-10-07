import * as k8s from '@kubernetes/client-node'

export async function listNamespaces() {
  const kc = new k8s.KubeConfig()
  kc.loadFromFile('/home/ops/.kube/bookstore-config')
  const core = kc.makeApiClient(k8s.CoreV1Api)
  return core.listNamespace()
}

export async function defaultCtx() {
  const kc = new k8s.KubeConfig()
  kc.loadFromDefault()
  const net = kc.makeApiClient(k8s.NetworkingV1Api)
  return net.listNamespacedIngress({ namespace: 'edge' })
}
