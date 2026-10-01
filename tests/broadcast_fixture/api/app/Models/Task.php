<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Task extends Model
{
    protected $fillable = ['board_id', 'title', 'state'];

    public function board()
    {
        return $this->belongsTo(Board::class);
    }
}
