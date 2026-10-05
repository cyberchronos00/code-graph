const { utilityProcess } = require('electron');
const path = require('path');

function startUtility() {
  const u = utilityProcess.fork(path.join(__dirname, 'utility-child.js'));
  u.on('message', (m) => console.log(m));
  u.postMessage('ping');
}

module.exports = { startUtility };
