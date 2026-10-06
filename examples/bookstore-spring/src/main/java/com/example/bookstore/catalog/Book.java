package com.example.bookstore.catalog;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "store_books")
public class Book {
    @Id
    private Long id;
    private String title;

    public Long getId() { return id; }
    public String getTitle() { return title; }
}
