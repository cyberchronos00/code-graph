process.on('message', (msg) => {
  process.send({ ok: true, got: msg });
});
