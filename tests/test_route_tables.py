"""Route -> table precision: typed override fan-out, Eloquent pivot writes, grouped downstream."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.indexer import index_project  # noqa: E402
from codegraph.query import downstream, render_downstream  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from sample import needs_php  # noqa: E402


def _app(root: Path) -> None:
    (root / "routes").mkdir(parents=True)
    (root / "app" / "Http" / "Controllers").mkdir(parents=True)
    (root / "app" / "Repositories").mkdir(parents=True)
    (root / "app" / "Services").mkdir(parents=True)
    (root / "app" / "Models").mkdir(parents=True)
    (root / "app" / "Events").mkdir(parents=True)
    (root / "app" / "Listeners").mkdir(parents=True)
    (root / "composer.json").write_text(
        '{"require": {"laravel/framework": "^11.0"}, "autoload": {"psr-4": {"App\\\\": "app/"}}}'
    )
    (root / "artisan").write_text("")
    (root / "routes" / "api.php").write_text(
        "<?php\nuse Illuminate\\Support\\Facades\\Route;\n"
        "Route::post('/api/playlists', [App\\Http\\Controllers\\PlaylistController::class, 'store']);\n"
    )
    (root / "app" / "Repositories" / "Repository.php").write_text(
        "<?php\nnamespace App\\Repositories;\nabstract class Repository {\n"
        "    public function getOne($id) { return null; }\n}\n"
    )
    (root / "app" / "Repositories" / "PlaylistRepository.php").write_text(
        "<?php\nnamespace App\\Repositories;\nclass PlaylistRepository extends Repository {\n"
        "    public function getOne($id) { return \\App\\Models\\Playlist::query()->find($id); }\n}\n"
    )
    (root / "app" / "Repositories" / "AlbumRepository.php").write_text(
        "<?php\nnamespace App\\Repositories;\nclass AlbumRepository extends Repository {\n"
        "    public function getOne($id) {\n"
        "        \\Illuminate\\Support\\Facades\\DB::table('interactions')->insert(['song_id' => $id]);\n"
        "        return \\App\\Models\\Album::query()->find($id);\n"
        "    }\n}\n"
    )
    (root / "app" / "Models" / "Concerns").mkdir(parents=True)
    (root / "app" / "Models" / "Concerns" / "HasPlaylists.php").write_text(
        "<?php\nnamespace App\\Models\\Concerns;\ntrait HasPlaylists {\n"
        "    public function playlists() {\n"
        "        return $this->belongsToMany(\\App\\Models\\Playlist::class)->withPivot('role');\n"
        "    }\n"
        "    public function ownedPlaylists() {\n"
        "        return $this->playlists()->wherePivot('role', 'owner');\n"
        "    }\n}\n"
    )
    (root / "app" / "Models" / "User.php").write_text(
        "<?php\nnamespace App\\Models;\nuse Illuminate\\Database\\Eloquent\\Model;\n"
        "use App\\Models\\Concerns\\HasPlaylists;\n"
        "class User extends Model {\n    use HasPlaylists;\n}\n"
    )
    (root / "app" / "Models" / "Playlist.php").write_text(
        "<?php\nnamespace App\\Models;\nuse Illuminate\\Database\\Eloquent\\Model;\n"
        "class Playlist extends Model {\n"
        "    public function folders() {\n"
        "        return $this->belongsToMany(PlaylistFolder::class, 'playlist_playlist_folder');\n"
        "    }\n}\n"
    )
    (root / "app" / "Models" / "PlaylistFolder.php").write_text(
        "<?php\nnamespace App\\Models;\nuse Illuminate\\Database\\Eloquent\\Model;\nclass PlaylistFolder extends Model {}\n"
    )
    (root / "app" / "Models" / "Album.php").write_text(
        "<?php\nnamespace App\\Models;\nuse Illuminate\\Database\\Eloquent\\Model;\nclass Album extends Model {}\n"
    )
    (root / "app" / "Services" / "PlaylistService.php").write_text(
        "<?php\nnamespace App\\Services;\nuse App\\Models\\Playlist;\nuse App\\Models\\User;\n"
        "class PlaylistService {\n"
        "    public function createPlaylist(User $user) {\n"
        "        $playlist = Playlist::query()->create(['name' => 'x']);\n"
        "        $user->ownedPlaylists()->attach($user->id, ['role' => 'owner']);\n"
        "        $playlist->folders()->sync([1 => ['position' => 1]]);\n"
        "        event(new \\App\\Events\\PlaylistCreated($playlist));\n"
        "        return $playlist;\n"
        "    }\n}\n"
    )
    (root / "app" / "Events" / "PlaylistCreated.php").write_text(
        "<?php\nnamespace App\\Events;\nclass PlaylistCreated {\n    public function __construct(public $playlist) {}\n}\n"
    )
    (root / "app" / "Listeners" / "WriteAudit.php").write_text(
        "<?php\nnamespace App\\Listeners;\nclass WriteAudit {\n"
        "    public function handle(\\App\\Events\\PlaylistCreated $event) {\n"
        "        \\Illuminate\\Support\\Facades\\DB::table('audits')->insert(['name' => 'created']);\n"
        "    }\n}\n"
    )
    (root / "app" / "Http" / "Controllers" / "PlaylistController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n"
        "use App\\Repositories\\PlaylistRepository;\nuse App\\Services\\PlaylistService;\n"
        "class PlaylistController {\n"
        "    public function __construct(private PlaylistRepository $repo, private PlaylistService $svc) {}\n"
        "    public function store() {\n"
        "        $this->requireAccess();\n"
        "        $this->repo->getOne(1);\n"
        "        return $this->svc->createPlaylist(new \\App\\Models\\User());\n"
        "    }\n"
        "    private function requireAccess() {\n"
        "        \\Illuminate\\Support\\Facades\\DB::table('permits')->where('id', 1)->first();\n"
        "    }\n}\n"
    )


def _edges(db: Path, kind: str):
    con = sqlite3.connect(db)
    return [(s, d, json.loads(a or "{}")) for s, d, a in con.execute(
        "select src, dst, attrs from edges where kind=?", (kind,))]


@needs_php
def test_pivot_writes_and_grouped_downstream(tmp_path):
    root = tmp_path / "app"
    _app(root)
    db = tmp_path / "graph.db"
    index_project(root, db, "route-tables")
    writes = {(s, d) for s, d, _a in _edges(db, "WRITES_TABLE")}
    svc = "method:App\\Services\\PlaylistService::createPlaylist"
    assert (svc, "table:playlists") in writes
    assert (svc, "table:playlist_user") in writes
    assert (svc, "table:playlist_playlist_folder") in writes
    cols = {(s, d) for s, d, _a in _edges(db, "WRITES_COLUMN")}
    assert (svc, "column:playlist_user.role") in cols
    assert (svc, "column:playlist_playlist_folder.position") in cols
    # the sibling repository still writes its own table, but not for this route
    album = "method:App\\Repositories\\AlbumRepository::getOne"
    assert (album, "table:interactions") in writes

    st = GraphStore(db)
    res = downstream(st, "route:POST /api/playlists")
    groups = res["table_groups"]
    assert "playlists" in groups["direct"]
    assert "playlist_user" in groups["direct"]
    assert "playlist_playlist_folder" in groups["direct"]
    assert "interactions" not in groups["direct"]
    assert "interactions" not in groups["override"]
    assert "permits" in groups["auth"]
    assert "audits" in groups["event"]
    text = render_downstream(res, show_paths=False)
    assert "direct (route -> handler -> service):" in text
    assert "through auth / access checks:" in text
    assert "through events / listeners:" in text
    assert "interactions" not in text

    from codegraph.query import impact
    callers = {c["id"] if isinstance(c, dict) else c for c in impact(st, "table:playlists").get("callers", [])}
    # column-level impact still sees the service that writes the table
    blob = json.dumps(impact(st, "table:playlists"))
    assert "PlaylistService::createPlaylist" in blob
    assert "PlaylistService::createPlaylist" in blob or callers
