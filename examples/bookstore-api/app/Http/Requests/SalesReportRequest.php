<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class SalesReportRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'category_id' => 'nullable|integer',
            'timezone' => ['nullable', 'string', 'max:64'],
        ];
    }
}
