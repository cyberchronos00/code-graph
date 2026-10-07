import Docker from 'dockerode'

const local = new Docker()
const sock = new Docker({ socketPath: '/run/bookstore/docker.sock' })
const remote = new Docker({ host: '10.20.0.5', port: 2375 })
const secure = new Docker({ host: 'build.bookstore.example', port: 2376, ca: process.env.DOCKER_CA, cert: process.env.DOCKER_CERT, key: process.env.DOCKER_KEY })
const fromEnv = new Docker({ host: process.env.DOCKER_HOST })

export async function listLocal() {
  return local.listContainers()
}

export async function startReport(id: string) {
  await sock.getContainer(id).start()
}

export async function build() {
  return remote.createContainer({ Image: 'bookstore/report' })
}

export async function secureBuild() {
  return secure.listImages()
}

export async function viaEnv() {
  return fromEnv.listContainers()
}
