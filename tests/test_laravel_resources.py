"""Laravel resource routes follow ResourceRegistrar: dotted names expand, parameters are singular,
and parameters / shallow / only / except / names / scoped change the routes they register."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402
from sample import needs_php  # noqa: E402

CONTROLLERS = {
    "PlaylistController": ["index", "store", "show", "update", "destroy"],
    "PlaylistSongController": ["index", "store", "show", "update", "destroy"],
    "FolderPlaylistController": ["index", "store", "show", "update", "destroy"],
    "PhotoCommentController": ["index", "create", "store", "show", "edit", "update", "destroy"],
    "UserController": ["index", "store", "show", "update", "destroy"],
    "PostCommentController": ["index", "store", "show", "update", "destroy"],
    "ArtistAlbumController": ["index", "store", "show", "update", "destroy"],
    "VideoController": ["index", "create", "store", "show", "edit", "update", "destroy"],
}

ROUTES = r"""<?php

use App\Http\Controllers\ArtistAlbumController;
use App\Http\Controllers\FolderPlaylistController;
use App\Http\Controllers\PhotoCommentController;
use App\Http\Controllers\PlaylistController;
use App\Http\Controllers\PlaylistSongController;
use App\Http\Controllers\PostCommentController;
use App\Http\Controllers\UserController;
use App\Http\Controllers\VideoController;
use Illuminate\Support\Facades\Route;

Route::prefix('api')->name('api.')->group(function () {
    Route::apiResource('playlists', PlaylistController::class);
    Route::apiResource('playlists.songs', PlaylistSongController::class);
    Route::apiResource('playlist-folders.playlists', FolderPlaylistController::class);
    Route::resource('photos.comments', PhotoCommentController::class)->shallow()->only(['index', 'show']);
    Route::apiResource('users', UserController::class)->parameters(['users' => 'admin_user'])->except(['destroy']);
    Route::apiResource('posts.comments', PostCommentController::class)
        ->names(['store' => 'comments.add'])
        ->name('show', 'comments.view')
        ->parameter('comments', 'note')
        ->scoped(['note' => 'slug']);
    Route::apiResources([
        'artists.albums' => ArtistAlbumController::class,
    ], ['only' => ['index', 'show']]);
    Route::resource('admin/videos', VideoController::class, ['only' => ['index', 'show'], 'names' => 'video']);
});
"""


def _app(root: Path) -> None:
    (root / "routes").mkdir(parents=True)
    (root / "app" / "Http" / "Controllers").mkdir(parents=True)
    (root / "composer.json").write_text(
        '{"require": {"laravel/framework": "^11.0"}, "autoload": {"psr-4": {"App\\\\": "app/"}}}'
    )
    (root / "artisan").write_text("")
    (root / "routes" / "api.php").write_text(ROUTES)
    for name, methods in CONTROLLERS.items():
        body = "\n".join(f"    public function {m}() {{}}" for m in methods)
        (root / "app" / "Http" / "Controllers" / f"{name}.php").write_text(
            f"<?php\nnamespace App\\Http\\Controllers;\nclass {name} {{\n{body}\n}}\n"
        )


def _routes(db: Path) -> dict:
    con = sqlite3.connect(db)
    out = {}
    for nid, name, attrs in con.execute("select id, name, attrs from nodes where kind='route'"):
        parsed = json.loads(attrs or "{}")
        handlers = [r[0] for r in con.execute(
            "select dst from edges where src=? and kind='ROUTES_TO'", (nid,))]
        out[name] = {
            "name": parsed.get("name"),
            "scoped": parsed.get("scoped"),
            "binding_fields": parsed.get("binding_fields"),
            "handler": handlers,
        }
    return out


@needs_php
def test_nested_resources_match_laravel_registrar(tmp_path):
    root = tmp_path / "app"
    _app(root)
    db = tmp_path / "graph.db"
    index_project(root, db, "resources")
    routes = _routes(db)
    assert not any("." in segment for name in routes for segment in name.split(" ", 1)[1].split("/"))

    def handler(key, method):
        assert routes[key]["handler"] == [f"method:App\\Http\\Controllers\\{method}"]
        return routes[key]

    playlists = handler("GET /api/playlists", "PlaylistController::index")
    assert playlists["name"] == "api.playlists.index"
    handler("POST /api/playlists", "PlaylistController::store")
    handler("GET /api/playlists/{playlist}", "PlaylistController::show")
    handler("PUT /api/playlists/{playlist}", "PlaylistController::update")
    handler("DELETE /api/playlists/{playlist}", "PlaylistController::destroy")
    assert "GET /api/playlists/create" not in routes

    handler("POST /api/playlists/{playlist}/songs", "PlaylistSongController::store")
    handler("GET /api/playlists/{playlist}/songs/{song}", "PlaylistSongController::show")
    assert routes["GET /api/playlists/{playlist}/songs"]["name"] == "api.playlists.songs.index"

    handler("GET /api/playlist-folders/{playlist_folder}/playlists", "FolderPlaylistController::index")
    handler("DELETE /api/playlist-folders/{playlist_folder}/playlists/{playlist}", "FolderPlaylistController::destroy")

    handler("GET /api/photos/{photo}/comments", "PhotoCommentController::index")
    show = handler("GET /api/comments/{comment}", "PhotoCommentController::show")
    assert show["name"] == "api.comments.show"
    assert routes["GET /api/photos/{photo}/comments"]["name"] == "api.photos.comments.index"
    assert "POST /api/photos/{photo}/comments" not in routes
    assert "GET /api/comments/{comment}/edit" not in routes

    handler("GET /api/users/{admin_user}", "UserController::show")
    assert "DELETE /api/users/{admin_user}" not in routes

    store = handler("POST /api/posts/{post}/comments", "PostCommentController::store")
    assert store["name"] == "api.comments.add"
    viewed = handler("GET /api/posts/{post}/comments/{note}", "PostCommentController::show")
    assert viewed["name"] == "api.comments.view" and viewed["scoped"] is True
    assert viewed["binding_fields"] == {"note": "slug"}
    assert routes["GET /api/posts/{post}/comments"]["binding_fields"] == {"note": "slug"}

    handler("GET /api/artists/{artist}/albums", "ArtistAlbumController::index")
    handler("GET /api/artists/{artist}/albums/{album}", "ArtistAlbumController::show")
    assert "POST /api/artists/{artist}/albums" not in routes

    handler("GET /api/admin/videos", "VideoController::index")
    video = handler("GET /api/admin/videos/{video}", "VideoController::show")
    assert video["name"] == "api.video.show"
    assert "POST /api/admin/videos" not in routes
