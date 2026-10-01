import { expect, test } from '@playwright/test'

test.describe('board page', () => {
  test('shows the tasks of a board', async ({ page }) => {
    await page.goto('/boards/1')
    await expect(page.locator('li')).toHaveCount(2)
  })

  test('moving a task through the API', async ({ request }) => {
    const res = await request.patch('/api/tasks/1/move', { data: { state: 'done' } })
    expect(res.ok()).toBeTruthy()
  })
})
