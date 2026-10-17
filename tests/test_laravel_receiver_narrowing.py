"""#148 / #149: `$this->...` and `parent::...` calls follow only the overrides the receiver's own class chain can
dispatch to, in `downstream` and in `routes --writes`. Fixture written from scratch in a temp dir."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph import routes as R  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from sample import needs_php  # noqa: E402

FILES = {
    "routes/web.php": r"""<?php
use App\Http\Controllers\ContactController;
use App\Http\Controllers\StatusController;
use Illuminate\Support\Facades\Route;

Route::delete('contacts/{contact}', [ContactController::class, 'destroy']);
Route::delete('statuses/{status}', [StatusController::class, 'destroy']);
""",
    "app/Http/Controllers/ContactController.php": r"""<?php
namespace App\Http\Controllers;
use App\Services\RemoveContact;
class ContactController {
    public function destroy() { dispatch(new RemoveContact()); }
}
""",
    "app/Http/Controllers/StatusController.php": r"""<?php
namespace App\Http\Controllers;
use App\Repositories\StatusRepository;
class StatusController {
    public function __construct(private StatusRepository $statuses) {}
    public function destroy() { $this->statuses->remove(); }
}
""",
    "app/Services/QueuedService.php": r"""<?php
namespace App\Services;
use Illuminate\Contracts\Queue\ShouldQueue;
abstract class QueuedService implements ShouldQueue {
    public function handle(): void { $this->execute(); }
    abstract public function execute(): void;
}
""",
    "app/Services/RemoveContact.php": r"""<?php
namespace App\Services;
use Illuminate\Support\Facades\DB;
class RemoveContact extends QueuedService {
    public function execute(): void { DB::table('contacts')->delete(); }
}
""",
    "app/Services/ChangeCurrency.php": r"""<?php
namespace App\Services;
use Illuminate\Support\Facades\DB;
class ChangeCurrency extends QueuedService {
    public function execute(): void { DB::table('wallet_currency')->update(['code' => 'EUR']); }
}
""",
    "app/Repositories/BaseRepository.php": r"""<?php
namespace App\Repositories;
use Illuminate\Support\Facades\DB;
class BaseRepository {
    public function remove(): void { DB::table('audit_trail')->insert(['what' => 'remove']); }
}
""",
    "app/Repositories/StatusRepository.php": r"""<?php
namespace App\Repositories;
use Illuminate\Support\Facades\DB;
class StatusRepository extends BaseRepository {
    public function remove(): void { parent::remove(); DB::table('task_states')->delete(); }
}
""",
    "app/Repositories/LabelRepository.php": r"""<?php
namespace App\Repositories;
use Illuminate\Support\Facades\DB;
class LabelRepository extends BaseRepository {
    public function remove(): void { DB::table('labels')->delete(); }
}
""",
}


def build(tmp: Path) -> GraphStore:
    root = tmp / "app"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / "composer.json").write_text('{"require": {"laravel/framework": "^11.0"}, "autoload": {"psr-4": {"App\\\\": "app/"}}}')
    (root / "artisan").write_text("")
    db = tmp / "g.db"
    index_project(root, db, "narrow")
    return GraphStore(str(db))


def _writes(st):
    rep = R.routes_report(st, writes="*")
    return {i["name"]: {r["what"].split(" ", 1)[1]: r for r in i["reaches"]} for i in rep["items"]}


@needs_php
def test_this_call_in_inherited_job_handle_reaches_only_the_jobs_override(tmp_path):
    st = build(tmp_path)
    edges = st.q("SELECT attrs FROM edges WHERE kind='CALLS' AND src LIKE '%QueuedService::handle' AND dst LIKE '%::execute'")
    assert edges and '"this": true' in edges[0]["attrs"]
    tg = Q.downstream(st, "route:DELETE /contacts/{contact}")["table_groups"]
    assert tg["event"] == ["contacts"] and not tg["override"]     # a queued job counts as the event channel
    assert not any("wallet_currency" in v for v in tg.values())
    w = _writes(st)["DELETE /contacts/{contact}"]
    assert set(w) == {"contacts"} and not w["contacts"].get("via_override")


@needs_php
def test_parent_call_does_not_fan_out_into_sibling_overrides(tmp_path):
    st = build(tmp_path)
    tg = Q.downstream(st, "route:DELETE /statuses/{status}")["table_groups"]
    assert set(tg["direct"]) == {"task_states", "audit_trail"}
    assert not any("labels" in v for v in tg.values())
    w = _writes(st)["DELETE /statuses/{status}"]
    assert set(w) == {"task_states", "audit_trail"}


@needs_php
def test_routes_writes_matches_downstream_direct_tables(tmp_path):
    st = build(tmp_path)
    w = _writes(st)
    for route in ("DELETE /contacts/{contact}", "DELETE /statuses/{status}"):
        tg = Q.downstream(st, f"route:{route}")["table_groups"]
        assert set(w[route]) == set(tg["direct"]) | set(tg["event"]) | set(tg["auth"])
