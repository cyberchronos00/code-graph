'use strict'
var http = require('http')

function request (url, cb) {
  return http.get(url, cb)
}

function verbFunc (verb) {
  return function (url, cb) { return request(url, cb) }
}

request.get = verbFunc('get')
request.defaults = function (opts) { return request }

// a callback passed to a factory: `server` holds the server object, not the callback
function startServer (port) {
  var server = http.createServer(function (req, res) {
    res.end(handle(req))
  })
  server.listen(port)
  server.url = 'http://localhost:' + port
  return server
}

function handle (req) {
  return req.url
}

// a closure handed back to the caller
function createValidator (text) {
  var l = function (req, resp) { return req.body === text }
  return l
}

// a wrapper: the variable holds the wrapped function
var debounced = debounce(function () { return handle({ url: '/' }) })

function debounce (fn) {
  return function () { return fn() }
}

module.exports = { request, startServer, createValidator, debounced, handle }
