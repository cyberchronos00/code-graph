<?php

namespace App\Http\Controllers;

use App\Http\Requests\SalesReportRequest;
use App\Models\Order;
use App\Models\Store;
use App\Services\SalesReportService;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Http\Request;

class ReportController extends Controller
{
    public function __construct(private SalesReportService $service) {}

    /** Top sellers for the store. */
    public function top(SalesReportRequest $request)
    {
        $store = Store::query()->firstOrFail();

        return $this->service->report($store, $request->validated());
    }

    public function summary(Request $request)
    {
        $validated = $request->validate(['timezone' => 'nullable|string']);
        $store = Store::query()->firstOrFail();
        $timezone = $this->resolveTimezone($store, $validated['timezone'] ?? null, Order::query());

        return ['timezone' => $timezone, 'category' => $request->input('category_id', 0)];
    }

    public function export(string $store, string $format)
    {
        return [];
    }

    public function destroy(string $store, int $report)
    {
        return $this->service->remove($report);
    }

    private function resolveTimezone(?Store $store, ?string $explicit, Builder $orderQuery): string
    {
        $candidate = trim((string) ($explicit ?? ''));
        if ($candidate !== '') {
            return $candidate;
        }
        $latest = (clone $orderQuery)->orderByDesc('id')->value('customer_timezone');
        if (! empty($latest)) {
            return (string) $latest;
        }
        if ($store) {
            $configured = trim((string) $store->getSetting('locale.timezone', ''));
            if ($configured !== '') {
                return $configured;
            }
            $storeDefault = is_string($store->default_timezone ?? null) ? trim((string) $store->default_timezone) : '';
            if ($storeDefault !== '') {
                return $storeDefault;
            }
        }

        return 'UTC';
    }
}
