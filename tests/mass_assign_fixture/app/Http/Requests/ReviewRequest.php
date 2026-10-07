<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class ReviewRequest extends FormRequest
{
    public function rules(): array
    {
        return ['body' => 'required|string', 'stars' => 'required|integer', 'moderator_note' => 'nullable|string'];
    }
}
