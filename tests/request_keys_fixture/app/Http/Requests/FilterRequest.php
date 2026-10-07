<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class FilterRequest extends FormRequest
{
    public function rules(): array
    {
        return ['q' => 'required|string'];
    }
}
