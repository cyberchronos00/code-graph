<?php
namespace App\Support;

/** Synthetic branch shapes for the guard evaluator. Scenario: features.new_inventory.enabled = true. */
final class Flags
{
    public const KEY = 'features.new_inventory.enabled';

    public static function on($tenant): bool
    {
        if (!$tenant) {
            return false;            // presence prologue: ignored under the "inputs present" assumption
        }
        return (bool) $tenant->getSetting(self::KEY, false);
    }
}

final class FeatureGate
{
    public function usesNewInventory($tenant): bool { return Flags::on($tenant); }
    public function oldMode($tenant): bool { return ! Flags::on($tenant); }
}

final class OldFlow { public function hit(): int { return 1; } }
final class NewFlow { public function hit(): int { return 2; } }

final class Sample
{
    public function __construct(private FeatureGate $w, private OldFlow $old, private NewFlow $new) {}

    public function ifElse($t) {
        if (Flags::on($t)) { return $this->new->hit(); } else { return $this->old->hit(); }       // old: gated
    }
    public function earlyReturn($t) {
        if ($this->w->usesNewInventory($t)) { return $this->new->hit(); }
        return $this->old->hit();                                                                 // gated
    }
    public function negated($t) {
        if (! $this->w->usesNewInventory($t)) { return $this->old->hit(); }                       // gated
        return $this->new->hit();
    }
    public function ternary($t) {
        return $this->w->oldMode($t) ? $this->old->hit() : $this->new->hit();                     // old gated
    }
    public function viaVariable($t, $x) {
        $source = Flags::on($t) ? 'new' : 'old';
        if ($x && $source === 'old') { $this->old->hit(); }                                       // gated
        $stream = Flags::on($t) ? null : $this->old->hit();                                       // gated
        if (!$stream) { return 0; }
        return $this->old->hit();                                                                 // gated
    }
    public function elseifChain($t, $id) {
        $n = $this->w->usesNewInventory($t);
        if ($id && $n) { return $this->new->hit(); }
        elseif ($id) { return $this->old->hit(); }                                                // gated (needs !(id&&n) && id)
        return 0;
    }
    public function inClosure($t) {
        $n = Flags::on($t);
        return array_map(function ($row) use ($n) { return $n ? $row : $this->old->hit(); }, []); // gated
    }
    public function matchArm($t) {
        return match (Flags::on($t)) { true => $this->new->hit(), false => $this->old->hit() };   // false arm gated
    }
    public function unknownStaysLive($x) {
        if ($x) { return $this->old->hit(); }                                                     // live (not scenario-related)
        return $this->new->hit();
    }
    public function unguarded($t) {
        return $this->old->hit();                                                                 // live
    }
}
