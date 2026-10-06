package shop;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Service;

@Entity
@Table(name = "store_books")
class Book {
    @Id
    Long id;
}

interface BookQuery extends JpaRepository<Book, Long> {
    @Query("SELECT b FROM Book b")
    java.util.List<Book> search();

    @Query(value = "update store_books set title = ?1", nativeQuery = true)
    @Modifying
    void rename();
}

@Service
class BookReader {
    BookQuery books;

    void load() {
        books.search();
    }
}
