<?php

namespace App\Http\Controllers;

use App\Models\Order;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Gate;

class FalseGuardController extends Controller
{
    public function ignored(Order $order)
    {
        $this->ensureAccess($order);
        $order->save();
    }

    public function allows(Order $order)
    {
        Gate::allows('update', $order);
        $order->save();
    }

    public function allowsReturn(Order $order)
    {
        return Gate::allows('update', $order);
    }

    public function allowsAbort(Order $order)
    {
        abort_unless(Gate::allows('update', $order), 403);
        $order->save();
    }

    public function ui(Request $request, Order $order)
    {
        $canRefund = $request->user()->can('orders.refund');
        return ['canRefund' => $canRefund];
    }

    public function flag(Request $request, Order $order)
    {
        if ($request->user()->can('orders.refund')) {
            $order->note = 'show refund';
        }
        $order->save();
    }

    public function preview(Order $order)
    {
        if (Gate::allows('update', $order)) {
            $order->note = 'editable';
        }
        $order->save();
    }

    public function afterCallee(Order $order)
    {
        $this->persist($order);
        $this->authorize('update', $order);
    }

    public function branched(Order $order)
    {
        $this->ensureAccessOrContinue($order);
        $order->save();
    }

    public function branchCall(Order $order)
    {
        if (!$this->ensureAccess($order)) {
            abort(403);
        }
        $order->save();
    }

    public function gated(Order $order)
    {
        abort_unless($this->ensureAccess($order), 403);
        $order->save();
    }

    public function headers(Request $request)
    {
        abort_unless($request->header('X-Sync-Secret') === $request->input('secret'), 401);
    }

    public function loose(Request $request)
    {
        abort_unless((string) $request->header('X-Sync-Secret') == (string) config('bookstore.sync_secret'), 401);
    }

    private function ensureAccess(Order $order): bool
    {
        return Gate::allows('update', $order);
    }

    private function ensureAccessOrContinue(Order $order): void
    {
        if ($order->id) {
            abort(403);
        }
    }

    private function persist(Order $order): void
    {
        $order->save();
    }
}
