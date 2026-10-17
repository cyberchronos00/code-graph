"""Laravel routes at their served path (#191) and routes registered in loops over literal data (#184)."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402
from sample import needs_php  # noqa: E402

API = r"""<?php
use App\Enums\Shelf;
use App\Http\Controllers\GenreController;
use App\Http\Controllers\OrderController;
use App\Http\Controllers\ShelfController;
use Illuminate\Support\Facades\Route;

Route::post('v1/orders', [OrderController::class, 'store'])->middleware('auth:api');

Route::prefix('v1/{store}')->group(function () {
    foreach (['fiction' => 'genre.fiction', 'poetry' => 'genre.poetry'] as $genre => $mw) {
        Route::get("genres/{$genre}/books", [GenreController::class, 'index'])
            ->defaults('genre', $genre)
            ->middleware($mw);
    }
});

collect(['index', 'show'])->each(fn ($action) => Route::get('catalog/' . $action, [GenreController::class, $action]));

array_map(fn ($s) => Route::get("shelves/{$s->value}", [ShelfController::class, 'show']), Shelf::cases());

$formats = ['csv', 'pdf'];
foreach ($formats as $format) {
    Route::get("exports/{$format}", [OrderController::class, 'export'])->name("exports.{$format}");
}

foreach (config('bookstore.partners') as $partner) {
    Route::post("partners/{$partner}/hooks", [OrderController::class, 'hook']);
}
"""

HOOKS = r"""<?php
use App\Http\Controllers\OrderController;
use Illuminate\Support\Facades\Route;

Route::post('payments', [OrderController::class, 'hook']);
"""

WEB = r"""<?php
use App\Http\Controllers\OrderController;
use Illuminate\Support\Facades\Route;

Route::get('orders', [OrderController::class, 'page']);
"""

BOOTSTRAP = r"""<?php
use Illuminate\Foundation\Application;
use Illuminate\Support\Facades\Route;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        apiPrefix: 'api/v2',
        then: function () {
            Route::middleware('api')->prefix('webhooks')->group(base_path('routes/hooks.php'));
        },
    )
    ->create();
"""

PROVIDER = r"""<?php
namespace App\Providers;
use Illuminate\Support\Facades\Route;
use Illuminate\Foundation\Support\Providers\RouteServiceProvider as ServiceProvider;

class RouteServiceProvider extends ServiceProvider
{
    public function boot(): void
    {
        $this->routes(function () {
            Route::middleware('api')->prefix('rest')->group(base_path('routes/api.php'));
            Route::middleware('web')->group(base_path('routes/web.php'));
        });
    }
}
"""


def _app(root: Path, bootstrap: str | None = None, provider: str | None = None) -> None:
    for d in ("routes", "app/Http/Controllers", "app/Enums", "bootstrap", "app/Providers"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "composer.json").write_text('{"require": {"laravel/framework": "^11.0"}, "autoload": {"psr-4": {"App\\\\": "app/"}}}')
    (root / "artisan").write_text("")
    (root / "routes" / "api.php").write_text(API)
    (root / "routes" / "hooks.php").write_text(HOOKS)
    (root / "routes" / "web.php").write_text(WEB)
    if bootstrap:
        (root / "bootstrap" / "app.php").write_text(bootstrap)
    if provider:
        (root / "app" / "Providers" / "RouteServiceProvider.php").write_text(provider)
    (root / "app" / "Enums" / "Shelf.php").write_text(
        "<?php\nnamespace App\\Enums;\nenum Shelf: string {\n    case Front = 'front';\n    case Back = 'back';\n}\n")
    ctl = root / "app" / "Http" / "Controllers"
    (ctl / "GenreController.php").write_text("<?php\nnamespace App\\Http\\Controllers;\nclass GenreController {\n"
                                             "    public function index() {}\n    public function show() {}\n}\n")
    (ctl / "ShelfController.php").write_text("<?php\nnamespace App\\Http\\Controllers;\nclass ShelfController {\n"
                                             "    public function show() {}\n}\n")
    (ctl / "OrderController.php").write_text("<?php\nnamespace App\\Http\\Controllers;\nclass OrderController {\n"
                                             "    public function store() {}\n    public function export() {}\n"
                                             "    public function hook() {}\n    public function page() {}\n}\n")


def _routes(db: Path) -> dict:
    con = sqlite3.connect(db)
    out = {}
    for nid, attrs in con.execute("select id, attrs from nodes where kind='route'"):
        a = json.loads(attrs or "{}")
        a["handler"] = [r[0] for r in con.execute("select dst from edges where src=? and kind='ROUTES_TO'", (nid,))]
        out[nid[len("route:"):]] = a
    return out


def _cov(db: Path) -> dict:
    from cg_code_graph.core.store import GraphStore
    return GraphStore(db).meta()["stats"]["coverage"]


def _index(tmp_path, **kw):
    root = tmp_path / "app"
    _app(root, **kw)
    db = tmp_path / "g.db"
    index_project(root, db, "shop")
    return root, db


@needs_php
def test_api_routes_use_the_default_api_prefix(tmp_path):
    _, db = _index(tmp_path)
    routes = _routes(db)
    order = routes["POST /api/v1/orders"]
    assert order["uri"] == "/api/v1/orders" and order["declared_uri"] == "/v1/orders"
    assert order["prefix_from"] == "laravel-default"
    assert "POST /v1/orders" not in routes
    assert routes["GET /orders"].get("declared_uri") is None      # web.php stays unprefixed


@needs_php
def test_api_prefix_from_bootstrap_and_then_groups(tmp_path):
    _, db = _index(tmp_path, bootstrap=BOOTSTRAP)
    routes = _routes(db)
    order = routes["POST /api/v2/v1/orders"]
    assert order["declared_uri"] == "/v1/orders" and order["prefix_from"] == "bootstrap/app.php"
    hook = routes["POST /webhooks/payments"]
    assert hook["handler"] == ["method:App\\Http\\Controllers\\OrderController::hook"]
    assert "GET /orders" in routes


@needs_php
def test_api_prefix_from_route_service_provider(tmp_path):
    _, db = _index(tmp_path, provider=PROVIDER)
    routes = _routes(db)
    assert routes["POST /rest/v1/orders"]["prefix_from"] == "app/Providers/RouteServiceProvider.php"
    assert "GET /orders" in routes


@needs_php
def test_served_path_link_variants_and_declared_spec(tmp_path):
    from cg_code_graph import query as Q
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph.link import match_endpoint, route_uri_variants
    _, db = _index(tmp_path, bootstrap=BOOTSTRAP)
    routes = _routes(db)
    a = routes["POST /api/v2/v1/orders"]
    variants = route_uri_variants("routes/api.php", a)
    assert variants == [("served", "/api/v2/v1/orders")]
    rows = [{"id": "route:POST /api/v2/v1/orders", "uri": a["uri"], "method": "POST", "uris": variants}]
    hit = match_endpoint("POST", "/api/v2/v1/orders", rows)["matched"]
    assert hit and hit[0]["confidence"] == "exact"
    assert not match_endpoint("POST", "/api/v1/orders", rows)["matched"]
    # an older graph without declared_uri keeps the hard-coded /api variant
    assert route_uri_variants("routes/api.php", {"uri": "/v1/x"}) == [("as-declared", "/v1/x"), ("api-prefixed", "/api/v1/x")]
    # the default prefix keeps the declared path as a second variant
    assert route_uri_variants("routes/api.php", {"uri": "/api/v1/x", "declared_uri": "/v1/x", "prefix_from": "laravel-default"})[1][1] == "/v1/x"
    st = GraphStore(str(db))
    assert Q.match_route_spec(st, "route:POST /v1/orders") == ["route:POST /api/v2/v1/orders"]
    assert Q.route_declared_note(st, "route:POST /v1/orders") == "note: declared path; served as POST /api/v2/v1/orders"
    assert Q.route_declared_note(st, "route:POST /api/v2/v1/orders") is None


@needs_php
def test_literal_loops_are_unrolled(tmp_path):
    _, db = _index(tmp_path)
    routes = _routes(db)
    for genre in ("fiction", "poetry"):
        r = routes[f"GET /api/v1/{{store}}/genres/{genre}/books"]
        assert r["middleware"] == [f"genre.{genre}"]
        assert r["handler"] == ["method:App\\Http\\Controllers\\GenreController::index"]
        assert r["unrolled_from"]["binding"] == {"genre": genre, "mw": f"genre.{genre}"}
        assert r["unrolled_from"]["file"] == "routes/api.php" and r["unrolled_from"]["line"] == 11
        assert r["defaults"] == {"genre": genre}
    assert routes["GET /api/catalog/show"]["handler"] == ["method:App\\Http\\Controllers\\GenreController::show"]
    assert routes["GET /api/catalog/index"]["unrolled_from"]["binding"] == {"action": "index"}
    assert routes["GET /api/shelves/front"]["unrolled_from"]["binding"] == {"s": "App\\Enums\\Shelf::Front"}
    assert "GET /api/shelves/back" in routes
    assert routes["GET /api/exports/pdf"]["name"] == "exports.pdf"
    assert not any("partners" in k for k in routes)


@needs_php
def test_only_non_literal_loops_stay_blind_spots(tmp_path):
    root, db = _index(tmp_path)
    cov = _cov(db)
    spots = [b for b in cov.get("blind_spots") or [] if b["kind"] == "laravel_loop_routes"]
    assert len(spots) == 1 and spots[0]["count"] == 1
    assert spots[0]["samples"] == ["routes/api.php:27"]
    assert spots[0]["reasons"] == {"routes/api.php:27": "iterable not literal"}
    assert "iterable not literal" in spots[0]["what"]


@needs_php
def test_loop_over_more_than_64_combinations_is_a_blind_spot(tmp_path):
    root = tmp_path / "app"
    _app(root)
    many = ", ".join(f"'g{i}'" for i in range(9))
    (root / "routes" / "web.php").write_text(
        "<?php\nuse Illuminate\\Support\\Facades\\Route;\n"
        f"foreach ([{many}] as $a) {{\n    foreach ([{many}] as $b) {{\n"
        "        Route::get(\"x/{$a}/{$b}\", fn () => 1);\n    }\n}\n")
    db = tmp_path / "g.db"
    index_project(root, db, "shop")
    routes = _routes(db)
    assert not any(k.startswith("GET /x/") for k in routes)
    cov = _cov(db)
    spots = [b for b in cov.get("blind_spots") or [] if b["kind"] == "laravel_loop_routes"]
    assert "more than 64 combinations" in spots[0]["what"]


@needs_php
def test_routes_listing_marks_unrolled_routes(tmp_path):
    from cg_code_graph import routes as R
    from cg_code_graph.core.store import GraphStore
    _, db = _index(tmp_path)
    st = GraphStore(db)
    txt = R.render_routes(R.routes_report(st), st)
    assert "GET /api/v1/{store}/genres/fiction/books  @routes/api.php:12  (unrolled: $genre = 'fiction', $mw = 'genre.fiction')" in txt


@needs_php
def test_loop_over_a_map_of_invokable_controllers(tmp_path):
    root = tmp_path / "app"
    _app(root)
    ctl = root / "app" / "Http" / "Controllers"
    for name in ("PingController", "SearchController"):
        (ctl / f"{name}.php").write_text(f"<?php\nnamespace App\\Http\\Controllers;\nclass {name} {{\n"
                                         "    public function __invoke() {}\n}\n")
    (root / "routes" / "web.php").write_text(r"""<?php
use App\Http\Controllers\PingController;
use App\Http\Controllers\SearchController;
use Illuminate\Support\Arr;
use Illuminate\Support\Facades\Route;

$endpoints = ['ping' => PingController::class, 'search' => SearchController::class];
$extra = ['search' => 'throttle:search'];
foreach ($endpoints as $endpoint => $controller) {
    Route::match(['get', 'post'], "feed/{$endpoint}{format?}", $controller)
        ->middleware(Arr::wrap($extra[$endpoint] ?? []));
}
Route::get('feed/items/{item}/{slug}', PingController::class)->whereNumber('item')->where('slug', '[a-z-]+');
""")
    db = tmp_path / "g.db"
    index_project(root, db, "shop")
    routes = _routes(db)
    assert routes["GET /feed/ping{format?}"]["handler"] == ["method:App\\Http\\Controllers\\PingController::__invoke"]
    assert routes["POST /feed/search{format?}"]["handler"] == ["method:App\\Http\\Controllers\\SearchController::__invoke"]
    assert routes["GET /feed/search{format?}"]["middleware"] == ["throttle:search"]
    assert not routes["GET /feed/ping{format?}"].get("middleware")
    assert routes["GET /feed/items/{item}/{slug}"]["where"] == {"item": "[0-9]+", "slug": "[a-z-]+"}
