# PHP

## Class properties (#88)

A declared instance property (`private array $items = [];`) or a constructor-promoted one
(`public function __construct(private Pricer $pricer)`) is a `property:<Class>::$<name>` node with attrs
`property: stored`. A promoted property also has `promoted: true`. A static property and a docblock `@property` are
not stored properties. A docblock `@property` is usually a Laravel model attribute, which is a column
(`READS_COLUMN` / `WRITES_COLUMN`).

Reads and writes are `READS_PROP` / `WRITES_PROP` edges onto that node, with attr `receiver`:
- `$this->x` binds `exact`. The property can be declared on the class, an ancestor or a used trait.
- `$v->x` binds `resolved` when the type of `$v` is known: a typed parameter, a typed property, a local built by
  `new`, or a return type.
- An unknown receiver binds nothing (stat `property_refs_unresolved`).

A plain `= v` is a write. These are writes with `via`, plus the read the walk records:
- `+=`, `.=`, `++`, `--` (`via: compound`);
- `$this->items[] = v`, `$this->map[k] = v`, `unset($this->map[k])` (`via: item`);
- `unset($this->x)` (`via: unset`).

`cg readers Cart.items` / `cg writers Cart::$items` / `cg writers App\Cart::$items` list them. The facts Laravel
reads for model attributes and relations are unchanged, so every other edge stays the same. This was checked on koel
and laravel.io.
