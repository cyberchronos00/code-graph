<?php

namespace App\Filament\Resources;

use App\Models\Book;
use Filament\Forms\Form;
use Filament\Resources\Resource;

class BookResource extends Resource
{
    protected static ?string $model = Book::class;

    public static function form(Form $form): Form
    {
        return $form->schema([
            \Filament\Forms\Components\TextInput::make('isbn')->required(),
            \Filament\Forms\Components\TextInput::make('price')->numeric(),
        ]);
    }
}
