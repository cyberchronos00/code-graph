<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class ShelfRequest extends FormRequest
{
    public function rules(): array
    {
        return ['label' => 'required|string', 'location' => 'nullable|string'];
    }
}
