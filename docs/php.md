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

## Column writes by mass assignment (#177)

`$book->update($request->validated())`, `Book::create($data)`, `$book->fill(...)`, `new Book($data)` followed by `save()` and a query-builder `->update($data)` write columns whose names are not in the call. cg takes the keys from the request array the argument carries and records `WRITES_COLUMN` at `resolved`, so `cg writers books.age_rating` and `cg routes --reaches books.age_rating` list the route that writes the column.

| argument | keys |
|---|---|
| `$request->validated()`, `$request->safe()`, `$request->validate()` | the FormRequest `rules()` keys, or the inline `validate([...])` keys, top-level only (`items.*.qty` is `items`) |
| `->only([...])` / `->except([...])` on the request or on `safe()` | the literal keys; for `except`, the request's keys minus them |
| a local or a parameter holding one of these | followed through assignments and call arguments (`$service->save($book, $request->validated())` writes from `save`) |
| `array_merge($request->validated(), [...])`, `[...$request->validated(), 'k' => v]` | both key sets |
| `$request->all()`, `input()`, `post()`, `query()`, `json()` | `heuristic`: the model's `$fillable` keys (no rules are consulted, any input key may arrive) |

Receivers: a model instance, `Model::create(...)` / `updateOrCreate` / `firstOrCreate` (the values array), a relation (`$book->reviews()->create(...)`), `new Model($data)` when the same function calls `save()` on it, and `Model::where(...)->update(...)` / `DB::table('books')->update(...)`.

Then the model's mass-assignment rules apply. A key outside a declared `$fillable` gets no edge (a rule key `reviewer_note` that `Book::$fillable` does not list is validated but never written). `$guarded` keys are dropped, `$guarded = []` keeps every rule key, and `$guarded = ['*']` without `$fillable` keeps none. `forceFill` / `forceCreate` ignore both, and query-builder writes (`->update` on a builder, `insert`, `upsert`) never consult `$fillable`. `cg plan check` still reports a new column that `$fillable` lacks as `model_fillable`.

The edge has `attrs.via` (`update(validated())`) and `attrs.keys_from` (`["UpdateBookRequest::rules", "Book::$fillable"]`); `cg node` prints them:

```text
$ cg node 'App\Http\Controllers\Admin\BookController::update' --db out/api.db
  outgoing (9):
    -> WRITES_COLUMN column:books.age_rating  @app/Http/Controllers/Admin/BookController.php:30 resolved  via update(validated()) keys from UpdateBookRequest::rules ∩ Book::$fillable
```

Keys built at run time stay out (`$data[$field] = ...` in a loop, `Arr::only($data, $allowed)` with a computed list). See [limitations](limitations.md#plans-value-facts-and-the-visual-view).

## Database connections on table edges (#179)

Every `READS_TABLE`, `WRITES_TABLE`, `READS_COLUMN`, `WRITES_COLUMN` edge from Laravel code has `attrs.connection`, the database connection the query goes to. It is taken from, in order:

1. the query's own selection, in the same chain or on the same builder variable: `Book::on($name)->where(...)`, `DB::connection($name)->table('books')`, `$book->setConnection($name)` followed by `$book->update(...)`;
2. the model's `$connection` property;
3. `database.default` from `config/database.php`.

`$name` can be a literal, a `config()` key, a method that returns a name registered with `Config::set('database.connections.{$name}', ...)`, or an interpolated string. A dynamic name keeps its pattern (`legacy_{store.id}`); a name that cannot be resolved (a parameter, a property set from elsewhere) is `?`. Mass-assignment writes ([above](#column-writes-by-mass-assignment-177)) get the same attribute from the model they write.

Edges on the default connection carry the attribute too, so `--connection mysql` works. Other attributes: `connection_default` (the project default), `connection_via` (`Book::on`, `DB::connection`, `setConnection`, `Shipment::$connection`; absent on the default), `connection_from` (the method that returns a dynamic name), `connection_model` and `connection_fallback` (the model and the connection it would have used without the override).

```text
$ cg impact 'ArchiveService::legacyBooks' --no-paths --db out/api.db
targets: ['method:App\\Services\\ArchiveService::legacyBooks']
connections: legacy_{store.id} (Book::on via ArchiveService::legacyConnection, app/Services/ArchiveService.php:13; Book default: mysql)
callers (transitive): 1
  d=1 [Http/Controllers/Admin] App\Http\Controllers\Admin\ArchiveController::index
entry points: 1
  http_route       GET /v1/{store}/admin/archive  conf=resolved
```

`reaches`, `routes --reaches`, `writers` and `readers` add `conn=<name>` to an edge that is not on the default connection, and `--connection NAME` keeps only the edges on one connection ([CLI](cli.md#database-connections)). A table used on several connections stays one node; its `attrs.connections` counts the edges per connection ([external systems](external.md#tables-on-several-connections)). A connection chosen at run time from data is `?` ([limitations](limitations.md#index-and-answers)).

## Inline guards

`cg routes` lists access checks a Laravel action runs before its own work on an `inline:` line, separate from route middleware. `--json` stores them on `inline_guards` (`name`, `kind`, `at`, `conditional`).

Counted:

- `$this->authorize(...)`, `Gate::authorize` / `Gate::denies` / `Gate::allows` with `abort(403)`, `abort_if` / `abort_unless` of `can` / `hasPermission` / `hasRole`, and `$request->user()->cannot(...)`.
- A FormRequest `authorize()` that does more than `return true`.
- `$this->middleware('can:...')` in the constructor and `HasMiddleware::middleware()`, including `only` / `except`, copied onto that controller's routes.
- A call on the same class, trait, or parent (two calls deep) that aborts `401` / `403` or throws `AuthorizationException` / `AccessDeniedHttpException` before any write. The access-check heuristic or `auth.extra_patterns` names that method when it rejects, or when the caller aborts on its result (`abort_unless($this->ensureAccess($order), 403)`). A discarded result does not count: `Gate::allows()` with no abort, `return Gate::allows(...)`, `can()` stored for a template, or `$this->ensureAccess($order)` called and ignored. A check after a write does not count.
- A shared-secret compare: `hash_equals` or `===` / `!==` of `config()` / `env()` against a request header or input, aborting on mismatch. Kind `secret`. A `===` between two request values is not a secret check, and `==` is not either.

A check inside a branch is `conditional`. `--unguarded` hides a route with an unconditional inline check. `--unguarded --strict` uses route middleware only. The walk stops at the action's first write. Kernel middleware groups are still not copied onto each route.

## Request keys the handler reads

A route's `attrs.request.keys` holds validated keys (FormRequest `rules()`, `$request->validate`, `Validator::make`) and keys the handler reads. Reads are known but never required. These reads count, anywhere in the handler and one level into a non-public method of the same controller:

- `$request->input` / `query` / `get` / `post` / `string` / `integer` / `float` / `boolean` / `date` / `enum` / `has` / `filled('k')` and `request('k')`;
- `$request->only([...])` / `except([...])`;
- the property form `$request->search`;
- `paginate()` / `simplePaginate()` (`page`), `cursorPaginate()` (`cursor`), and `per_page` when it is read or passed as `$perPage`.

`cg link` reports `request_unknown_field` for a query key only when the route has explicit validation and the key is neither validated nor read (severity `low`; an unknown body key stays `medium`). A route with reads only gives no unknown-key findings.

## Outbound HTTP

`Illuminate\Support\Facades\Http`, an injected `Illuminate\Http\Client\Factory` or `PendingRequest`, and Guzzle
`Client` become `http:` client endpoints (`HTTP_CALLS`). `cg link` matches them to another backend's routes.

Detected calls: `get` / `post` / `put` / `patch` / `delete` / `head`, and `send($method, $url)` / Guzzle
`request($method, $uri)` when the verb is a literal or a small set of literals from a parameter. A verb that does
not resolve is `ANY` at `heuristic`. `baseUrl()`, `withHeaders`, `withToken`, `withBody`, `asJson`, `acceptJson`,
`timeout` and `retry` are read along the chain. A helper whose path or verb is a parameter is expanded at its
call sites (`via`).

SendGrid, Mailgun, Postmark, Resend, Twilio and Vonage SDK clients (`$client = new \SendGrid($key)` then `->send()`,
`Mailgun::create`, `new Client($sid, $token)` `->messages->create`, ...) and Laravel mailers (`config/mail.php` +
`config/services.php`, `Mail::` callers, notification `via()` channels) are not client endpoints: they are
`external:saas:<provider>` systems ([External systems](external.md)). So are MessageBird and Plivo clients, `minishlink/web-push`,
`edamov/pushok`, Laravel FCM / APNs notification channels, `kreait/firebase-php`, `renoki-co/php-k8s`, `docker-php`, aws-sdk-php
`KmsClient` and Google Secret Manager (`external:gcp:<service>`, `external:k8s:<api-group>`, `external:docker:<target>`,
`external:aws:kms`).

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
Literal array bodies (`post($url, ['order_id' => ...])`, `withBody(json_encode([...]))`, and
`$json = json_encode($payload)` then `withBody($json)` or Guzzle `'body' => $json`) are `body_keys`
in the same shape the TypeScript extractor writes. `json_encode` is unwrapped once, after assignments in the same function, and the inner variable is followed
through a helper parameter. A variable with no assignment stays opaque. `cg api-calls` prints
those keys under the call site.

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
