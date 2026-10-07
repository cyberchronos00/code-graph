<?php

namespace App\Http\Controllers\Admin;

use App\Http\Controllers\Controller;
use App\Models\Store;
use App\Services\ArchiveService;

class ArchiveController extends Controller
{
    public function __construct(private ArchiveService $archive)
    {
    }

    /** Books of a store that still lives on its own legacy database. */
    public function index(string $store)
    {
        return $this->archive->legacyBooks(Store::query()->firstOrFail());
    }
}
