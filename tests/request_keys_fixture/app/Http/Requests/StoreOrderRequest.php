<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

class StoreOrderRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'book_id' => 'required|integer',
            'quantity' => ['required', 'integer', 'min:1'],
            'gift_note' => 'nullable|string',
            'nickname' => 'sometimes|string',
            'items.*.id' => 'required|integer',
            'address.city' => 'required|string',
            'status' => 'required|in:open,closed',
            'coupon' => 'required_if:status,open|string',
            'extra' => 'required_with:nickname|string',
            'when' => [Rule::requiredIf(true), 'string'],
        ];
    }
}
