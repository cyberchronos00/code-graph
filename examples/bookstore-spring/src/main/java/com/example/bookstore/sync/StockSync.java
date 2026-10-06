package com.example.bookstore.sync;

import com.example.bookstore.catalog.BookRepository;
import org.springframework.scheduling.annotation.Scheduled;

public class StockSync {
    private final BookRepository books;

    public StockSync(BookRepository books) {
        this.books = books;
    }

    @Scheduled(fixedDelay = 60000)
    public void run() {
        books.findAll();
    }
}
