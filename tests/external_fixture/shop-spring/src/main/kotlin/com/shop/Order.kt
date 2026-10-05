package com.shop
import jakarta.persistence.Entity
import jakarta.persistence.Table
import jakarta.persistence.Id
@Entity
@Table(name = "orders")
class Order {
  @Id var id: Long = 0
}
