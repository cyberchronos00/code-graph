# Graph schema

The graph is a single SQLite file. This page lists the tables, node kinds, edge kinds, confidence levels and entry kinds.


```sql
nodes(id PK, kind, name, fqn, file, line, end_line, module, doc, lang, entry_kind, attrs JSON)
edges(id, src, dst, kind, file, line, confidence, conf_rank, attrs JSON)   -- file:line = evidence
edge_kinds(kind PK, propagates, description)    -- propagates=1: src depends on dst (used by reaches)
node_entry(node_id, entry_kind, entry_count, sample_entry)   -- which entry kinds reach each node
meta(key, value)                                 -- corpus, commit, stats
```

**Node kinds:** class, interface, trait, method (incl. functions), property, external_class (vendor placeholder), route, command, schedule, job,
event, listener, observer, admin (Filament surface), table, column, connection, config, env, script (migrations/routes/config files).
TS/Vue (lang='ts'): module (TS file), page, component, layout, app (Vue SFCs), composable, store, function, class, type,
http (client endpoint `http:<METHOD> <path template>`), i18n.
**module** is derived from the path or namespace (e.g. `Http/Controllers/Admin`, `Services`, `Console/Commands`, `Domain/X`). **doc** holds the PHPDoc text.

**Edge kinds** (✓ = propagates in `reaches`/`impact`):
CALLS✓, IMPLEMENTED_BY✓ (interface method → impl), OVERRIDDEN_BY✓ (parent → override), BOUND_TO✓ (container binding),
ROUTES_TO✓, USES_MIDDLEWARE✓, HANDLED_BY✓ (command → handle), SCHEDULES✓, DISPATCHES✓, LISTENED_BY✓,
READS_COLUMN✓, WRITES_COLUMN✓, MENTIONS_COLUMN✓ (heuristic: a literal equal to a distinctive column name, e.g. in validation rules), READS_TABLE✓, WRITES_TABLE✓,
USES_CONNECTION✓, REGISTERS_CONNECTION✓, READS_CONFIG✓, WRITES_CONFIG✓, READS_ENV✓, REFERS_TO✓ (config value → connection), CONFIGURED_BY✓, CONFIG_CONTAINS✓,
TS: IMPORTS, RENDERS✓ (template component usage), USES_COMPOSABLE✓, USES_STORE✓, HTTP_CALLS✓ (→ http endpoint), MATCHES_ROUTE✓
(http endpoint → backend route, combined DB only), USES_LAYOUT, USES_I18N, REFERENCES_TYPE.
MAPS_TO_TABLE, HAS_RELATION, CONTAINS, EXTENDS, IMPLEMENTS, USES_TRAIT, INSTANTIATES, INJECTS, REFERENCES (`X::class`), OBSERVED_BY, BINDS, DEFINES.

**Confidence:**
- `exact`: syntactically certain, e.g. a static call, `new X`, `$this->m()` or a literal key.
- `resolved`: needed type or name resolution, e.g. typed properties/params, constructor-promoted deps, inferred variable types, return types, or model → table.
- `heuristic`: a unique-method-name fallback, or a column-name literal.

**Entry kinds:**
- Runtime: `http_route`, `scheduled`, `queue_job`, `listener`.
- Operator: `artisan_command`, `admin_panel` (Filament).
- `observer`.
- UI (TS/Nuxt): `ui_page` (Nuxt pages), `ui_global` (app.vue, layouts, plugins, `*.global.ts` middleware). `reaches` shows a UI group.

Tagging is a forward closure from each entry node over propagating edges. `reaches` classifies each dependent as:
- **runtime**, if any runtime entry reaches it;
- **operator-only**, if only commands or admin panels reach it (one-off import or provisioning);
- **none**, if no entry point reaches it.


