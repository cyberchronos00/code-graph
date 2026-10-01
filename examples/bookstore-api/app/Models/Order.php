<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Order extends Model
{
    protected $table = 'orders';

    protected $fillable = ['user_id', 'book_id', 'book_isbn', 'total'];

    public function book()
    {
        return $this->belongsTo(Book::class);
    }
}
