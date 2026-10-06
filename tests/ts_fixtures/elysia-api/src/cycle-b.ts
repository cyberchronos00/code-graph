import { Elysia } from 'elysia'
import { cycleA } from './cycle-a'

export const cycleB = new Elysia({ prefix: '/cycle-b' }).use(cycleA).get('/ping', () => 'b')
