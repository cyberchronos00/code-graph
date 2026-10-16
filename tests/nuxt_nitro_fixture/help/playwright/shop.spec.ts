import { test, expect } from '@playwright/test'
import { openShop } from './support'

test('lists plants', async ({ page, request }) => {
  await openShop(page)
  const res = await request.get('/api/plants')
  expect(res.ok()).toBeTruthy()
})
