<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class UpdateBookRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'title' => 'sometimes|string',
            'price' => 'sometimes|integer',
            'age_rating' => 'sometimes|nullable|in:all,teen,adult',
            'reviewer_note' => 'nullable|string',
            'tags.*.name' => 'string',
        ];
    }
}
