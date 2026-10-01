<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Book extends Model
{
    protected $fillable = ['store_id', 'isbn', 'title', 'price', 'is_active', 'stock', 'sold_count'];

    public function isSoldOut(): bool
    {
        return $this->stock !== null && $this->sold_count >= $this->stock;
    }
}
