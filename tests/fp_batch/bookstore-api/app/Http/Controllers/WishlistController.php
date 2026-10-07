<?php

namespace App\Http\Controllers;

use App\Http\Requests\RemoveWishlistItemRequest;

class WishlistController
{
    public function remove(RemoveWishlistItemRequest $request)
    {
        return $request->validated();
    }
}
