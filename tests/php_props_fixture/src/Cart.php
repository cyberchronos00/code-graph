<?php

namespace Fx;

class Cart
{
    private array $items = [];
    protected int $total = 0;
    public static int $count = 0;

    public function __construct(private Pricer $pricer, public string $owner = '')
    {
    }

    public function add(string $sku, int $qty): void
    {
        $this->items[] = $sku;
        $this->total += $this->pricer->price($sku) * $qty;
        $this->owner = 'me';
        unset($this->items[0]);
    }

    public function label(): string
    {
        return $this->owner . count($this->items);
    }
}

class Pricer
{
    public int $rate = 1;

    public function price(string $sku): int
    {
        return $this->rate;
    }
}

function checkout(Cart $cart, Pricer $p): string
{
    $p->rate = 2;
    $p->rate++;
    $q = new Pricer();
    $q->rate = 3;
    $unknown = make();
    $unknown->rate = 4;
    return $cart->owner;
}
