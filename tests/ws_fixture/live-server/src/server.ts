import express from 'express'
import http from 'http'
import { WebSocketServer } from 'ws'
import expressWs from 'express-ws'
import { onLive, onEcho, streamEvents, plain } from './handlers'

const app = express()
const server = http.createServer(app)
expressWs(app, server)

// ws on the HTTP server, with a path
const wss = new WebSocketServer({ server, path: '/live' })
wss.on('connection', onLive)

// a second ws server on its own port, inline listener
const metrics = new WebSocketServer({ port: 9100 })
metrics.on('connection', (socket) => {
  socket.send('hello')
})

// express-ws on a mounted router
const api = express.Router()
api.ws('/echo', onEcho)
app.use('/api', api)

app.get('/events', streamEvents)
app.get('/plain', plain)

server.listen(3000)
