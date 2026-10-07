import got from 'got'

const api = got.extend({ prefixUrl: 'https://cards.bookbank.example/v3' })

export async function cards(id: string) {
  await got('https://rates.bookbank.example/v1/quotes')
  await got.post('https://rates.bookbank.example/v1/quotes', { json: { pair: 'EURUSD', amount: 10 } })
  await api.get('cards')
  await api.post('cards', { json: { owner: id } })
  return api(`cards/${id}`, { method: 'DELETE' })
}
