declare global {
  interface Window { fiddle: any }
}

const api = window.fiddle

export function quitApp() {
  api.quit()
}

export function listen() {
  window.fiddle.addEventListener('run-fiddle', () => 1)
}
