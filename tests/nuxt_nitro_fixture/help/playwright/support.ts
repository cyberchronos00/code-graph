export function openShop(page: { goto(url: string): Promise<unknown> }) {
  return page.goto('/')
}
