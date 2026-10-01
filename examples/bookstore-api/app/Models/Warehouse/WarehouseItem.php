<?php

namespace App\Models\Warehouse;

use Illuminate\Database\Eloquent\Model;

/** Stock row in the old warehouse system (separate database). */
class WarehouseItem extends Model
{
    protected $connection = 'warehouse';

    protected $table = 'warehouse_stock';

    public function isSoldOut(): bool
    {
        return $this->stock !== null && $this->sold_count >= $this->stock;
    }
}
