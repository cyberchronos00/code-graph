package com.example.bookstore.catalog;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

@Repository
public interface BookRepository extends JpaRepository<Book, Long> {
    java.util.List<Book> findAll();
    Book findById(Long id);
    Book save(Book book);
    boolean isAvailable(Long id);
}
