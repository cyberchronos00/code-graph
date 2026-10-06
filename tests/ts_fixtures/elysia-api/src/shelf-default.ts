import { Elysia } from 'elysia'

export default new Elysia({ prefix: '/shelf-default' }).get('/d', () => 'd')
