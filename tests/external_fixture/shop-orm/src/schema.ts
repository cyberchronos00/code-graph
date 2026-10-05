import { pgTable, serial, text } from 'drizzle-orm/pg-core'

export const customers = pgTable('customers', {
  id: serial('id').primaryKey(),
  email: text('email'),
})
