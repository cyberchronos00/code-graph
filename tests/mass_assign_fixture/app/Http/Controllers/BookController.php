<?php

namespace App\Http\Controllers;

use App\Http\Requests\AuthorRequest;
use App\Http\Requests\ReviewRequest;
use App\Http\Requests\ShelfRequest;
use App\Http\Requests\UpdateBookRequest;
use App\Models\Author;
use App\Models\Book;
use App\Models\Order;
use App\Models\Shelf;
use App\Services\BookSaver;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class BookController extends Controller
{
    public function __construct(private BookSaver $saver)
    {
    }

    public function updateValidated(UpdateBookRequest $request, Book $book)
    {
        $book->update($request->validated());
    }

    public function updateViaLocal(UpdateBookRequest $request, Book $book)
    {
        $data = $request->validated();
        $book->update($data);
    }

    public function updateViaService(UpdateBookRequest $request, Book $book)
    {
        $this->saver->save($book, $request->validated());
    }

    public function updateOnly(UpdateBookRequest $request, Book $book)
    {
        $book->update($request->safe()->only(['price', 'reviewer_note']));
    }

    public function updateRequestOnly(Request $request, Book $book)
    {
        $book->update($request->only(['title', 'internal_code']));
    }

    public function updateExcept(UpdateBookRequest $request, Book $book)
    {
        $book->update($request->safe()->except(['price']));
    }

    public function updateMerged(UpdateBookRequest $request, Book $book)
    {
        $book->update(array_merge($request->validated(), ['is_active' => true]));
    }

    public function updateSpread(UpdateBookRequest $request, Book $book)
    {
        $book->update([...$request->validated(), 'is_active' => false]);
    }

    public function createStatic(UpdateBookRequest $request)
    {
        return Book::create($request->validated());
    }

    public function createOrUpdate(UpdateBookRequest $request)
    {
        return Book::updateOrCreate(['title' => 'x'], $request->validated());
    }

    public function fillForced(UpdateBookRequest $request, Book $book)
    {
        $book->forceFill($request->validated());
        $book->save();
    }

    public function createForced(UpdateBookRequest $request)
    {
        return Book::forceCreate($request->validated());
    }

    public function updateAll(Request $request, Book $book)
    {
        $book->update($request->all());
    }

    public function updateInline(Request $request, Book $book)
    {
        $book->update($request->validate(['title' => 'required', 'internal_code' => 'nullable', 'price' => 'integer']));
    }

    public function addReview(ReviewRequest $request, Book $book)
    {
        $book->reviews()->create($request->validated());
    }

    public function createAuthor(AuthorRequest $request)
    {
        Author::create($request->validated());
    }

    public function createShelf(ShelfRequest $request)
    {
        Shelf::create($request->validated());
    }

    public function newThenSave(Request $request)
    {
        $data = $request->validate(['status' => 'required', 'qty' => 'integer', 'secret' => 'string']);
        $order = new Order($data);
        $order->save();
    }

    public function newWithoutSave(Request $request)
    {
        $data = $request->validate(['status' => 'required']);
        $order = new Order($data);
        return $order;
    }

    public function builderUpdate(UpdateBookRequest $request)
    {
        Book::where('is_active', true)->update($request->validated());
    }

    public function tableUpdate(UpdateBookRequest $request)
    {
        DB::table('books')->update($request->validated());
    }

    public function computedKeys(Request $request, Book $book)
    {
        $data = [];
        foreach (['title', 'price'] as $field) {
            $data[$field] = $request->input($field);
        }
        $book->update($data);
    }
}
