import Echo from '../plugins/echo';

test('ping reaches the native plugin', async () => {
  await Echo.ping();
});
