package com.example.bookstore.web;

import com.example.bookstore.catalog.Book;
import com.example.bookstore.catalog.BookRepository;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/books")
public class BookController {
    private final BookRepository books;

    public BookController(BookRepository books) {
        this.books = books;
    }

    @GetMapping("/")
    public Iterable<Book> list() {
        return books.findAll();
    }

    @GetMapping("/{id}")
    public Book get(Long id) {
        return books.findById(id);
    }

    @GetMapping("/{id}/availability")
    public boolean availability(Long id) {
        return books.isAvailable(id);
    }

    @PostMapping("/")
    @PreAuthorize("hasRole('ADMIN')")
    public Book create(Book book) {
        return books.save(book);
    }
}
