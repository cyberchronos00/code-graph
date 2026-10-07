<?php

namespace App\Services;

use App\Models\Book;

class BookSaver
{
    public function save(Book $book, array $data): void
    {
        $book->update($data);
    }
}
