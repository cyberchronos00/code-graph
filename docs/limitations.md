# Known limitations

code-graph is beta. These are the known gaps; issues and pull requests that close them are welcome.

- **Types are flow-insensitive** (one type set per variable per function) and there are no generics. When a receiver is unresolvable, a unique-method-name fallback is used (`heuristic`, with a stoplist).
  A Builder passed as a generic `Builder` param loses its model.
- **Model → table** uses `$table` or Str::plural-like convention.
- Not indexed yet:
  - seeders as entry points;
  - `Artisan::command` closures;
  - broadcast channels;
  - observers fired by model writes (observer nodes exist but aren't propagated from writes).
- Filament resources: all methods of a resource/page class count as admin entry points.
- Dynamic connection names are normalized (e.g. `tenant_{store.id}`). String-built column/table names are not resolved.
- Gate scenarios are deterministic but limited:
  - one scenario per index;
  - per-function context-insensitive (params are TOP, except literal-argument method summaries);
  - flags passed as data arguments or held in properties (`$this->x`) aren't tracked, so those paths stay live (conservative);
  - same canonical expression = same atom;
  - "inputs present" assumption for bare null/empty checks;
  - more than 12 atoms means the branch is assumed feasible;
  - middleware gates (a middleware that aborts when a flag is off) are not modelled;
  - an edge is gated only when the gated function itself makes the reference.
- Filament form saves are not emitted as WRITES edges (the plan checks list Filament resources separately).
- SCIP importer: references are attributed to the nearest enclosing definition by range, and scip-php closures show up as anonymous functions.
- Go, Rust, C/C++ and others are **stubs**: detection plus indexer recipes exist, but the indexers aren't installed or tested.
- TS/Vue/Nuxt:
  - needs `.nuxt` (run `nuxi prepare` after installing deps); without it auto-imports/global components don't resolve;
  - library components (Nuxt UI etc.) are not nodes; `<component :is>`, `Teleport`/`Transition` are not resolved;
  - functions declared inside a `.vue` `<script setup>` collapse into the component node;
  - parameter-dependent URLs are expanded one call level only; numeric literal args stay `{param}`;
  - the axios detection is by type name (`AxiosInstance`/`AxiosStatic`); other HTTP wrappers need to be added;
  - bases not traced to `axios.create` fall back to a heuristic suffix match;
  - no navigation edges (`NuxtLink to`, `navigateTo`), no parent/child nested-page links, no laravel-echo channel links;
  - i18n keys are one global namespace (per-SFC `<i18n>` scopes are not separated);
  - no server/ or api/ handlers (Nitro routes would need a small addition);
  - request keys: builder functions are followed up to 4 levels; call-site argument keys one level (direct callers of
    the request issuer). Untyped forwarded parameters stay "unknown".
- Value facts / `resolutions`:
  - concept matching is lexical (head word of the target / first source key), so `$code` holding a timezone is missed and
    `--within` is a substring filter;
  - the "returns" form assumes source order = fallback order (guards between returns are not evaluated);
  - locals use their last assignment (flow-insensitive); arrays built from request keys assume same-name keys;
  - helper inlining depth ≤ 4; some operands stay `expr:` (e.g. array elements of untyped payload arrays, or untyped
    model attribute reads);
  - a source repeated later in a chain is shown once (`($a ?? $fb) ?: $fb`);
  - TS fallbacks: only `??`/`||` chains that end in a string/number literal (no ternaries, no `''`/`0`);
  - response fields are not modelled (setting → API response → client state chains are not followed);
  - no AI summary step.
- Plans:
  - completeness rules are fixed and named, not open-ended. Covered: table writers/readers, model-bound surfaces (Filament
    `$model`, `$fillable`, JsonResource, FormRequest key overlap), callers/entry points, clients, mirrors, identity
    columns, text mentions and declared precedents. Anything else needs a plan entry or a `precedents` regex;
  - `role: guard` bypass detection only sees graph reads (Eloquent property access the PHP plugin resolves);
  - a guard is checked by proxy: the guard method (or a direct callee) reads the declared column. Whether the branch
    really blocks the forbidden path is not evaluated;
  - verify mode cannot judge an `intent` (free text). It reports changed/UNCHANGED source spans against the baseline,
    plus the planned edges;
  - external clients come only from the snapshot file (private repos are not indexed); `code-search` entries assume the
    shared URL contract;
  - findings come from a hand-refreshed snapshot of issue evidence lines, not a live GitHub query; ranges map to the
    innermost method/function span;
  - text mentions only scan `app/` and `resources/views/` PHP/Blade and use exact names (identity columns, relation names).
- Visual view: a few hundred nodes per view is comfortable; above 1,500 nodes the closest ones are kept (`truncated`);
  layouts are force-directed (fcose) or layered (breadthfirst; does not handle module boxes well); serving is local
  (127.0.0.1) and has no auth, so expose it only through an SSH tunnel or use `viz-export`.

