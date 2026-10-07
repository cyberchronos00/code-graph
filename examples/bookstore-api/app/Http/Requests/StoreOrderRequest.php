<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class StoreOrderRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'book_id' => 'required|integer',
            'quantity' => 'required|integer|min:1',
            'gift_note' => 'nullable|string',
        ];
    }
}
