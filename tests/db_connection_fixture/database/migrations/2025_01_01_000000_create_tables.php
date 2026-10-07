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
            $table->unsignedBigInteger('store_id');
            $table->string('isbn');
            $table->timestamps();
        });
        Schema::create('shipments', function (Blueprint $table) {
            $table->id();
            $table->string('isbn');
            $table->integer('qty');
            $table->timestamps();
        });
        Schema::create('audits', function (Blueprint $table) {
            $table->id();
            $table->string('note');
            $table->string('actor');
            $table->timestamps();
        });
    }
};
