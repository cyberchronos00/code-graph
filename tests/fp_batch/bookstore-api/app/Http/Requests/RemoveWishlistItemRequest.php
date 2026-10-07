<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class RemoveWishlistItemRequest extends FormRequest
{
    public function rules(): array
    {
        return ['book_id' => 'required|integer', 'reason' => 'nullable|string'];
    }
}
