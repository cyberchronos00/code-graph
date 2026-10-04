var assert = require('assert')
var http = require('http')
var lib = require('../src/index')

var server = http.createServer(function (req, res) {
  res.end(lib.handle(req))
})
server.url = 'http://localhost:8080'

describe('server', function () {
  it('serves', function () {
    var s = lib.startServer(0)
    var options = { url: s.url + server.url + '/foo' }
    server.listen(0)
    assert.ok(options.url)
    var v = lib.createValidator('x')
    assert.ok(v({ body: 'x' }))
    lib.debounced()
  })
})
