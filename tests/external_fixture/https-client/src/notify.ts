import axios from 'axios'

export async function notifyGithub(repo: string) {
  const r = await fetch(`https://api.github.com/repos/${repo}`)
  await fetch('https://api.github.com/zen')
  return r
}

export async function pingSlack() {
  return axios.post('https://hooks.slack.com/services/T/B/x', { text: 'hi' })
}

export async function callDynamic(base: string) {
  return fetch(`${base}/v1/items`)
}

export async function localHealth() {
  return fetch('http://127.0.0.1:3000/health')
}

export async function pingHttpBin() {
  return fetch('http://httpbin.org/get')
}
