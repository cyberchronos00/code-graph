<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('users', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->boolean('is_admin')->default(false);
        });
        Schema::create('teams', function (Blueprint $table) {
            $table->id();
            $table->string('name');
        });
        Schema::create('team_user', function (Blueprint $table) {
            $table->foreignId('team_id');
            $table->foreignId('user_id');
        });
        Schema::create('boards', function (Blueprint $table) {
            $table->id();
            $table->foreignId('team_id');
            $table->string('title');
        });
        Schema::create('tasks', function (Blueprint $table) {
            $table->id();
            $table->foreignId('board_id');
            $table->string('title');
            $table->string('state');
        });
    }
};
