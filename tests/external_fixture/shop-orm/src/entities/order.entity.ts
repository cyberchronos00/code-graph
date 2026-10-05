import { Entity, Column, PrimaryGeneratedColumn } from 'typeorm'

@Entity({ name: 'orders' })
export class Order {
  @PrimaryGeneratedColumn()
  id: number

  @Column()
  total: number
}
