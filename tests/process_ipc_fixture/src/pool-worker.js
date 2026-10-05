const { parentPort } = require('node:worker_threads');

parentPort?.once('message', lintAll);

function lintAll(files) {
  parentPort.postMessage(files.length);
}
