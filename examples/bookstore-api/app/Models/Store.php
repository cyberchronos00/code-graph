<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

/** A shop front. Feature flags and preferences live in the JSON `settings` column. */
class Store extends Model
{
    public function getSetting(string $key, mixed $default = null): mixed
    {
        return data_get($this->settings ?? [], $key, $default);
    }

    public function legacyDbConfig(): array
    {
        return ['driver' => 'mysql', 'host' => $this->settings['legacy_host'] ?? '127.0.0.1', 'database' => "legacy_{$this->id}"];
    }

    public function setSetting(string $key, mixed $value): void
    {
        $this->settings = array_merge($this->settings ?? [], [$key => $value]);
    }
}
