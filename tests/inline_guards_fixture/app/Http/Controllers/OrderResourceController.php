<?php

namespace App\Http\Controllers;

class OrderResourceController extends Controller
{
    public function __construct()
    {
        $this->middleware('auth');
        $this->middleware('can:orders.update')->only('update', 'destroy');
        $this->middleware('can:orders.view')->except('update', 'destroy');
    }

    public function index()
    {
    }

    public function create()
    {
    }

    public function store()
    {
    }

    public function show()
    {
    }

    public function edit()
    {
    }

    public function update()
    {
    }

    public function destroy()
    {
    }
}
