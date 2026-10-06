import { Elysia, status } from 'elysia'
import { box } from './box'
import { cycleA } from './cycle-a'
import { makeShelf } from './factory'
import shelfDefault from './shelf-default'

export const sub = new Elysia({ prefix: '/sub' }).get('/item', () => 'item')

export const compose = new Elysia({ prefix: '/cmp' })
  .use(new Elysia({ prefix: '/a' }).use(new Elysia({ prefix: '/b' }).get('/c', () => 'c')))
  .use(sub)
  .group('/v2', (app) => app.use(sub))
  .use(makeShelf('/shelf'))
  .use(cycleA)
  .use(box)
  .use(shelfDefault)
  .onBeforeHandle(function headerOnly({ set }) { set.headers['x-trace'] = '1' })
  .get('/after-header', () => 'h')
  .get('/denied', () => 'e', { beforeHandle() { return status(403, 'no') } })
  .get('/blocked', () => 'e', { beforeHandle() { return status(401, 'no') } })

export const slashed = new Elysia({ prefix: '/slash/' }).get('/trail//z', () => 'z')
