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

## Inline guards

`cg routes` lists access checks a Laravel action runs before its own work on an `inline:` line, separate from route middleware. `--json` stores them on `inline_guards` (`name`, `kind`, `at`, `conditional`).

Counted:

- `$this->authorize(...)`, `Gate::authorize` / `Gate::denies` / `Gate::allows` with `abort(403)`, `abort_if` / `abort_unless` of `can` / `hasPermission` / `hasRole`, and `$request->user()->cannot(...)`.
- A FormRequest `authorize()` that does more than `return true`.
- `$this->middleware('can:...')` in the constructor and `HasMiddleware::middleware()`, including `only` / `except`, copied onto that controller's routes.
- A call on the same class, trait, or parent (two calls deep) that aborts `401` / `403` or throws `AuthorizationException` / `AccessDeniedHttpException` before any write. The access-check heuristic or `auth.extra_patterns` names that method when it rejects, or when the caller aborts on its result (`abort_unless($this->ensureAccess($order), 403)`). A discarded result does not count: `Gate::allows()` with no abort, `return Gate::allows(...)`, `can()` stored for a template, or `$this->ensureAccess($order)` called and ignored. A check after a write does not count.
- A shared-secret compare: `hash_equals` or `===` / `!==` of `config()` / `env()` against a request header or input, aborting on mismatch. Kind `secret`. A `===` between two request values is not a secret check, and `==` is not either.

A check inside a branch is `conditional`. `--unguarded` hides a route with an unconditional inline check. `--unguarded --strict` uses route middleware only. The walk stops at the action's first write. Kernel middleware groups are still not copied onto each route.

## Outbound HTTP

`Illuminate\Support\Facades\Http`, an injected `Illuminate\Http\Client\Factory` or `PendingRequest`, and Guzzle
`Client` become `http:` client endpoints (`HTTP_CALLS`). `cg link` matches them to another backend's routes.

Detected calls: `get` / `post` / `put` / `patch` / `delete` / `head`, and `send($method, $url)` / Guzzle
`request($method, $uri)` when the verb is a literal or a small set of literals from a parameter. A verb that does
not resolve is `ANY` at `heuristic`. `baseUrl()`, `withHeaders`, `withToken`, `withBody`, `asJson`, `acceptJson`,
`timeout` and `retry` are read along the chain. A helper whose path or verb is a parameter is expanded at its
call sites (`via`).

A constructor-promoted or assigned `$this->baseUrl` is followed from the container (`singleton` / `bind`,
`new` inside the provider, `when()->needs()->give()`) through `config()`, `config()->get()`,
`Config::get()`, `$app['config']->get()` and `$app->make('config')->get()` to `env()`. A config array
(`$cfg = config('services.payments')` or `->get('services.payments', [])`, then `$cfg['base_url']`) resolves
the same way; a sibling key such as `secret` is not read. The endpoint gets `origin_kind` `env` and
`attrs.base`, so `cg link` strips the base path. The sample value comes from `.env.example` or an `env()`
default. `.env` is not read, an empty example value is shown as the env name only, and a value that is not
a URL is not stored. A relative `Http::get('/path')` stays relative unless `baseUrl()` was set.
`rawurlencode`, `urlencode`, `trim`, `strval`, a `(string)` cast and `Str::of(...)->toString()` are
transparent, so a path segment keeps the variable's last name (`{paymentId}`, `$order->id` -> `{id}`).
Literal array bodies (`post($url, ['order_id' => ...])`, `withBody(json_encode([...]))`) are `body_keys`
in the same shape the TypeScript extractor writes.

```php
return $this->http->send($method, rtrim($this->baseUrl, '/') . $path);
```

`Http::fake(['*/payments' => ...])` in a feature test links the test to the client method (`TEST_HTTP`). It is
not an application call.

Symfony HttpClient, `curl_*` and `file_get_contents` are not client endpoints. A host known only at run time is
kept with `origin_kind` `unknown` and is not matched by host.

## Route attributes

spatie/laravel-route-attributes on a controller are routes, with the same `route:<METHOD> <uri>` nodes as
`routes/*.php`. `#[Prefix('api')]` and `#[Middleware('api')]` on the class combine with `#[Prefix]` /
`#[Middleware]` on the method. `#[Get]` / `#[Post]` / `#[Put]` / `#[Patch]` / `#[Delete]` / `#[Options]` /
`#[Head]` / `#[Any]` / `#[Route]` supply the path, and `name` / `middleware` arguments are the route name and
extra middleware. The same method and path in `routes/*.php` stays one route. Inline guards on the action
apply here too.
`Route::webhooks('payments/hooks', 'payments')` is `POST /payments/hooks` for spatie/laravel-webhook-client
when that name is in `config/webhook-client.php`. A controller that extends Cashier's webhook controller, or
a subclass of one, receives one event per `handle<Event>` method
([Webhook verification](protocols.md#webhook-verification)).
