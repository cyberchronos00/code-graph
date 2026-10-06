import { Elysia } from 'elysia'

export const box = new Elysia({ prefix: '/box' }).get('/n', () => 'n')
