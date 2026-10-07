<?php

namespace App\Http\Controllers;

use App\Models\Book;
use Illuminate\Http\Request;

class BookController
{
    public function index(Request $request)
    {
        $request->validate(['genre' => 'nullable|string']);
        $q = Book::query()->when($request->boolean('in_stock_only'), fn ($q) => $q->where('stock', '>', 0));
        if ($s = $request->query('search')) {
            $q->where('title', 'like', "%{$s}%");
        }

        return $q->paginate($request->input('per_page', 20));
    }

    public function browse(Request $request)
    {
        $request->validate(['genre' => 'nullable|string']);
        $q = Book::query();
        if ($request->filled('author')) {
            $q->where('author', $request->get('author'));
        }
        if ($request->has('lang')) {
            $q->where('lang', $request->string('lang'));
        }
        $request->only(['edition']);
        $request->except(['internal']);
        $q->where('year', $request->integer('year'))->where('rating', $request->float('rating'));
        $q->whereDate('published', $request->date('published'));
        $q->where('shelf', request('shelf'));
        $q->where('series', $request->series);
        $this->applySort($request, $q);

        return $q->simplePaginate();
    }

    private function applySort(Request $request, $q)
    {
        $q->orderBy($request->input('sort', 'title'));
    }
}
