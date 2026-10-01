import { Column, Entity, ManyToOne, PrimaryGeneratedColumn } from 'typeorm';
import { Store } from './store.entity';

@Entity('orders')
export class Order {
  @PrimaryGeneratedColumn() id: number;
  @Column({ name: 'placed_at' }) placedAt: Date;
  @Column() total: number;
  @Column({ name: 'customer_timezone', nullable: true }) customerTimezone: string;
  @ManyToOne(() => Store) store: Store;
}
