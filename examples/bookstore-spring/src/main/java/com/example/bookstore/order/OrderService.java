package com.example.bookstore.order;

import com.example.bookstore.catalog.BookRepository;
import com.example.bookstore.pricing.PricingService;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final PricingService pricing;
    private final OrderRepository orders;
    private final BookRepository books;

    public OrderService(PricingService pricing, OrderRepository orders, BookRepository books) {
        this.pricing = pricing;
        this.orders = orders;
        this.books = books;
    }

    public Order place(Order order) {
        books.findById(order.getId());
        pricing.lineTotal(order);
        return orders.save(order);
    }

    public Order find(Long id) {
        return orders.findById(id);
    }
}
