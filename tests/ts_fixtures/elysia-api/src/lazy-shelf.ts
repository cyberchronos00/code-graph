import { Elysia } from 'elysia'

export default new Elysia({ prefix: '/shelf' }).get('/:code', () => 'shelf')
