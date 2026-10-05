import { DataSource } from 'typeorm'
import { Order } from './entities/order.entity'

export const AppDataSource = new DataSource({
  type: 'postgres',
  url: process.env.DATABASE_URL,
  entities: [Order],
})
