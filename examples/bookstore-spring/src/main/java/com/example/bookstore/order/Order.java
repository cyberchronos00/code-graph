package com.example.bookstore.order;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "store_orders")
public class Order {
    @Id
    private Long id;
    private int qty;

    public Long getId() { return id; }
    public int getQty() { return qty; }
}
