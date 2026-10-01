<?php

namespace App\Http\Requests\Admin;

use Illuminate\Foundation\Http\FormRequest;

class UpdateBookRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'isbn' => 'required|string',
            'title' => 'required|string',
            'price' => 'required|numeric',
            'stock' => 'nullable|integer',
            'is_active' => 'boolean',
        ];
    }
}
