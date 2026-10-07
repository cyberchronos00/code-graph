<?php

namespace App\Http\Controllers;

use App\Models\Audit;
use App\Services\Catalog;
use Illuminate\Http\Request;

class AuditController
{
    public function store(Request $request)
    {
        $data = $request->validate(['note' => 'required', 'actor' => 'required']);
        Audit::create($data);
    }

    public function index(Catalog $catalog)
    {
        return $catalog->onChain('978-0-00-000000-0');
    }
}
