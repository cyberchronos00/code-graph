# Quick start

Install cg, index a sample, ask four questions, and connect an agent. Python 3.11+.

## Install
```bash
pipx install cg-code-graph
# or: uv tool install cg-code-graph
cg doctor
```

`cg doctor` shows which languages index exact or heuristic on this machine. Other options: [Install](install.md).

## Index a sample
The Django sample needs nothing but Python.

```bash
git clone https://github.com/cyberchronos00/code-graph.git && cd code-graph
cg index examples/bookstore-django --name bookstore-django --db out/api.db
```

Stdout is stats JSON (nodes/edges, plus `starters`: queries that resolve in this graph). Stderr ends with coverage. `out/` is created for you. For your own repo: `cg index . --db out/graph.db`.
```text
coverage: python 32 exact
  frameworks: django, django-ninja, djangorestframework | presets: common, python, django, django-ninja, djangorestframework | config: no .cg.yaml
```

## Ask questions
### What reaches a table

`cg reaches table:store_books --db out/api.db`
```text
targets: table:store_books -> 1 node(s): table:store_books
dependents: 18 nodes  (code: 6, entry points: 9, other: 3)
== RUNTIME (reached from http_route / ... / queue_job / listener): 5 functions/methods
    catalog.api.create_book  depth=1 conf=resolved  http_route(1)
        path: function:catalog.api.create_book
          -WRITES_TABLE[resolved @ catalog/api.py:27]-> table:store_books
    catalog.signals.review_saved  depth=3 conf=resolved  listener(1)
        path: function:catalog.signals.review_saved
          -DISPATCHES[exact @ catalog/signals.py:12]-> job:catalog.tasks.recompute_rating
          -HANDLED_BY[exact @ catalog/tasks.py:13]-> function:catalog.tasks.recompute_rating
          -READS_TABLE[resolved @ catalog/tasks.py:14]-> table:store_books
```

### Who writes it

`cg writers store_books --db out/api.db`
```text
[catalog] catalog.admin.BookAdmin  WRITES_TABLE table:store_books  @catalog/admin.py:6 (resolved)  entries: admin_panel
[catalog] catalog.api.create_book  WRITES_TABLE table:store_books  @catalog/api.py:27 (resolved)  entries: http_route
[catalog/management/commands] catalog.management.commands.import_books.Command.handle  WRITES_TABLE table:store_books  @catalog/management/commands/import_books.py:18 (resolved)  entries: management_command
...
7 write edges, 4 writers
```

### Unguarded routes that write

`cg routes --writes --unguarded --db out/api.db`
```text
routes reaching a write (any table): 2 of 19 routes | filter: no auth guard -> 1
POST /api/books/  @catalog/api.py:25  NO AUTH
    guards: (none)
    writes store_books via catalog.api.create_book conf=resolved  ROUTES_TO@api.py:25 → WRITES_TABLE@api.py:27~r → table:store_books
```

### Impact of a change, and its source

`cg impact catalog.api.create_book --db out/api.db` then `cg snippet catalog.api.create_book --db out/api.db`
```text
callers (transitive): 0
entry points: 1
  http_route       POST /api/books/  conf=exact
        path: route:POST /api/books/
          -ROUTES_TO[exact @ catalog/api.py:25]-> function:catalog.api.create_book
```

```text
catalog/api.py:26-28
26| def create_book(request, payload: BookIn):
27|     book = Book.objects.create(**payload.dict())
28|     return 201, book
```

Every edge carries `exact`, `resolved` or `heuristic`. `--min-confidence resolved` hides guesses. See [CLI reference](cli.md) and [Query targets (specs)](cli.md#query-targets-specs).

### Tests a change affects

`cg affected catalog/api.py --db out/api.db` on that index (`cg index examples/bookstore-django --name bookstore-django --db out/api.db`):

```text
changed: 1 file, 12 symbols
M catalog/api.py  12 symbols

tests: 3 (3 direct, 0 transitive, 0 UI, 0 changed) in 1 file
catalog/tests/test_api.py
  test_list_books [pytest] :7  direct  via route:GET /api/books/
  test_book_endpoints [pytest] :13  direct  via route:GET /api/books/{book_id}/
  test_order_needs_token [pytest] :18  direct  via route:POST /api/orders/

entry points: 6
  http_route  GET /api/books/  (catalog/api.py:15)  via route:GET /api/books/
  http_route  GET /api/books/{book_id}/  (catalog/api.py:20)  via route:GET /api/books/{book_id}/
  http_route  GET /api/books/{book_id}/availability/  (catalog/api.py:31)  via route:GET /api/books/{book_id}/availability/
  http_route  GET /api/orders/{order_id}/  (catalog/api.py:49)  via route:GET /api/orders/{order_id}/
  http_route  POST /api/books/  (catalog/api.py:25)  via route:POST /api/books/
  http_route  POST /api/orders/  (catalog/api.py:37)  via route:POST /api/orders/
```

`--base main` keeps only the touched lines. `--quiet` prints the test files. See [CLI reference](cli.md#affected).

## Link a frontend and a backend
Flutter app plus Django API. Needs the Dart SDK; `cg doctor` shows `dart exact`.

```bash
cg index examples/bookstore-flutter --name bookstore-flutter --db out/app.db
cg link --backend out/api.db --frontend out/app.db \
        --backend-name bookstore-django --frontend-name bookstore-flutter --db out/graph.db
cg routes --db out/graph.db
```

Link stats: `"client": "bookstore-flutter", "server": "bookstore-django", "endpoints": 7, "endpoints_matched": 6`.

```text
GET /api/books/  @bookstore-django/catalog/api.py:15  NO AUTH
    guards: (none)
    called from: BookRepository.fetchBooks (book_repository.dart) @book_repository.dart:10
```

`cg reaches table:store_books --db out/graph.db` now also lists Flutter UI code (for example `lib/blocs/books/books_bloc.dart#BooksBloc._onLoad ... ui_page(1)`). The Laravel API and Nuxt sample (`examples/bookstore-api`, `examples/bookstore-web`; needs PHP 8.2+, Composer, Node 20+) and the Nest, Next and Express samples link the same way. Several apps: [Workspace](cli.md#workspace).

## Connect an agent (MCP)
`cg install --host cursor` (or `claude`, `codex`, `vscode`, …) registers the MCP server. The key is `cg`:
```json
{"mcpServers": {"cg": {
  "command": "cg-mcp",
  "args": ["--db", "out/graph.db"]}}}
```

`cg agents install --mcp` previews the change and asks before writing. It adds rules to `AGENTS.md`, `CLAUDE.md` or `.cursor/rules` and the entry to `.cursor/mcp.json`. See [MCP server](mcp.md).

## See it

`cg serve --db out/graph.db`, then open http://127.0.0.1:8177/ .
[![cg visual view: the graph around the warehouse connection, a selected node with its evidence paths and source, then the planned-change overlay for the preorders plan](media/cg-view-preview.gif)](media/cg-view-demo.mp4)

[Visual view](viz.md).

## Next

- [Configuration](configuration.md)
- [Python](python.md)
- [TypeScript / JavaScript frameworks](ts-frameworks.md)
- [Planned changes](plans.md)
- [Known limitations](limitations.md)
