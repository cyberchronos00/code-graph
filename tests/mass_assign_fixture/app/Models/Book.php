<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Book extends Model
{
    protected $fillable = ['title', 'price', 'age_rating', 'is_active'];

    public function reviews()
    {
        return $this->hasMany(Review::class);
    }
}
