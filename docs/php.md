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

## Outbound HTTP

`Illuminate\Support\Facades\Http`, an injected `Illuminate\Http\Client\Factory` or `PendingRequest`, and Guzzle
`Client` become `http:` client endpoints (`HTTP_CALLS`). `cg link` matches them to another backend's routes.

Detected calls: `get` / `post` / `put` / `patch` / `delete` / `head`, and `send($method, $url)` / Guzzle
`request($method, $uri)` when the verb is a literal or a small set of literals from a parameter. A verb that does
not resolve is `ANY` at `heuristic`. `baseUrl()`, `withHeaders`, `withToken`, `withBody`, `asJson`, `acceptJson`,
`timeout` and `retry` are read along the chain. A helper whose path or verb is a parameter is expanded at its
call sites (`via`).

A constructor-promoted or assigned `$this->baseUrl` is followed from the container (`singleton` / `bind`,
`new` inside the provider, `when()->needs()->give()`) through `config()` to `env()`. The endpoint gets
`origin_kind` `env` and `attrs.base`, so `cg link` strips the base path. The sample value comes from
`.env.example` or an `env()` default. `.env` is not read, and a value that is not a URL is not stored.
Literal array bodies (`post($url, ['order_id' => ...])`, `withBody(json_encode([...]))`) are `body_keys`
in the same shape the TypeScript extractor writes.

```php
return $this->http->send($method, rtrim($this->baseUrl, '/') . $path);
```

`Http::fake(['*/payments' => ...])` in a feature test links the test to the client method (`TEST_HTTP`). It is
not an application call.

Symfony HttpClient, `curl_*` and `file_get_contents` are not client endpoints. A host known only at run time is
kept with `origin_kind` `unknown` and is not matched by host.
