<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('books', function (Blueprint $table) {
            $table->id();
            $table->string('title');
            $table->integer('price');
            $table->string('age_rating')->nullable();
            $table->string('reviewer_note')->nullable();
            $table->string('internal_code')->nullable();
            $table->boolean('is_active')->default(true);
            $table->timestamps();
        });
        Schema::create('reviews', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('book_id');
            $table->string('body');
            $table->integer('stars');
            $table->string('moderator_note')->nullable();
            $table->timestamps();
        });
        Schema::create('authors', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('bio')->nullable();
            $table->string('role')->nullable();
            $table->timestamps();
        });
        Schema::create('shelves', function (Blueprint $table) {
            $table->id();
            $table->string('label');
            $table->string('location')->nullable();
            $table->timestamps();
        });
        Schema::create('orders', function (Blueprint $table) {
            $table->id();
            $table->string('status')->nullable();
            $table->integer('qty')->nullable();
            $table->timestamps();
        });
    }
};
