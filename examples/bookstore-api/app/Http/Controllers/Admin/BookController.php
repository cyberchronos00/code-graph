<?php

namespace App\Http\Controllers\Admin;

use App\Http\Controllers\Controller;
use App\Http\Requests\Admin\StoreBookRequest;
use App\Http\Requests\Admin\UpdateBookRequest;
use App\Http\Resources\BookResource;
use App\Models\Book;

class BookController extends Controller
{
    public function store(StoreBookRequest $request)
    {
        $book = Book::create([
            'store_id' => 1,
            'isbn' => $request->input('isbn'),
            'title' => $request->input('title'),
            'price' => $request->input('price'),
            'is_active' => true,
            'stock' => $request->input('stock'),
        ]);

        return new BookResource($book);
    }

    public function update(UpdateBookRequest $request, int $id)
    {
        $book = Book::findOrFail($id);
        $book->update([
            'title' => $request->input('title'),
            'price' => $request->input('price'),
            'stock' => $request->input('stock'),
        ]);

        return new BookResource($book);
    }
}
