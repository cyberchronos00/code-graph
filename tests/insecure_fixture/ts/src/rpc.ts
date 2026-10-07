import * as grpc from '@grpc/grpc-js'

export function inventoryCredentials() {
  return grpc.credentials.createInsecure()
}

export function openInventory(Client: any) {
  return new Client('inventory.bookstore.example:50051', grpc.credentials.createInsecure())
}

export function openLocalInventory(Client: any) {
  return new Client('localhost:50051', grpc.credentials.createInsecure())
}

export function openSocketInventory(Client: any) {
  return new Client('unix:/run/bookshop/inventory.sock', grpc.credentials.createInsecure())
}

export function openTlsInventory(Client: any) {
  return new Client('inventory.bookstore.example:443', grpc.credentials.createSsl())
}

export function serveInventory(server: grpc.Server) {
  server.bindAsync('0.0.0.0:50052', grpc.ServerCredentials.createInsecure(), () => {})
}
