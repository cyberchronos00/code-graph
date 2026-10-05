import * as net from 'net'
import * as http from 'http'

export function callIpc() {
  return net.connect('/tmp/node-ipc.sock')
}

export function dockerVersion() {
  return http.request({ socketPath: '/var/run/docker.sock', path: '/version' })
}
