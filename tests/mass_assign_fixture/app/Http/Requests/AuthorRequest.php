<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class AuthorRequest extends FormRequest
{
    public function rules(): array
    {
        return ['name' => 'required|string', 'bio' => 'nullable|string', 'role' => 'nullable|string'];
    }
}
