import { Elysia } from 'elysia'
import { cycleB } from './cycle-b'

export const cycleA = new Elysia({ prefix: '/cycle-a' }).use(cycleB).get('/ping', () => 'a')
