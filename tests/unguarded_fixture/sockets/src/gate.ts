import { Server } from 'socket.io'

const io = new Server(3100)

const members = io.of('/members')

members.on('connection', (socket) => {
  if (!socket.data.user) {
    socket.disconnect()
    return
  }
  socket.on('shelf:join', (room) => socket.join(room))
})

const promo = io.of('/promo')

promo.on('connection', (socket) => {
  socket.on('promo:ping', () => socket.emit('promo:pong'))
})

const carts = io.of('/carts')

carts.on('connection', (socket) => {
  socket.on('cart:update', (cart) => {
    if (!socket.data.user) return
    socket.emit('cart:saved', cart)
  })
})
