<?php

namespace App\Http\Resources;

use Illuminate\Http\Resources\Json\JsonResource;

class BookResource extends JsonResource
{
    public function toArray($request): array
    {
        return ['id' => $this->id, 'isbn' => $this->isbn, 'title' => $this->title, 'price' => $this->price];
    }
}
