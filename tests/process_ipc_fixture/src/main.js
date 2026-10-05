const { fork, spawn } = require('child_process');
const path = require('path');
const { Worker } = require('worker_threads');
const { pathToFileURL } = require('url');

function startChild() {
  const child = fork(path.join(__dirname, 'child.js'));
  child.on('message', (msg) => handleReply(msg));
  child.send({ type: 'start' });
  return child;
}

function handleReply(msg) {
  console.log(msg);
}

function runTool() {
  spawn('node', [path.join(__dirname, 'tool.js'), '--verbose']);
}

function startWorker() {
  const w = new Worker(path.join(__dirname, 'hash-worker.js'));
  w.on('message', onHash);
  w.postMessage('abc');
}

function onHash(h) {
  console.log(h);
}

function startPool(files) {
  const workerURL = pathToFileURL(path.join(__dirname, './pool-worker.js'));
  const worker = new Worker(workerURL, {});
  worker.postMessage(files);
}

module.exports = { startChild, runTool, startWorker, startPool };
