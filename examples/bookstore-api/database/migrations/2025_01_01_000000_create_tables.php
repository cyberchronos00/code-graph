<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('stores', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('default_timezone')->nullable();
            $table->json('settings')->nullable();
        });
        Schema::create('users', function (Blueprint $table) {
            $table->id();
            $table->string('name');
        });
        Schema::create('books', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('store_id');
            $table->string('isbn');
            $table->string('title');
            $table->decimal('price', 10, 2);
            $table->boolean('is_active');
            $table->integer('stock')->nullable();
            $table->integer('sold_count')->default(0);
            $table->timestamps();
        });
        Schema::create('orders', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('user_id');
            $table->unsignedBigInteger('book_id')->nullable();
            $table->string('book_isbn')->nullable();
            $table->decimal('total', 10, 2);
            $table->timestamp('placed_at')->nullable();
            $table->string('customer_timezone')->nullable();
        });
    }
};
