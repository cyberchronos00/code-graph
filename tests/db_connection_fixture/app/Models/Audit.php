<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Audit extends Model
{
    protected $connection = 'reporting';

    protected $fillable = ['note', 'actor'];
}
