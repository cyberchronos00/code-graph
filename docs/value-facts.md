# Value facts and the `resolutions` concept query

Deterministic facts about *values*. They come from the same PHP/TS ASTs and are emitted by `codegraph/plugins/laravel/values.py`
and the TS extractor:

| node | edges into it | from |
|---|---|---|
| `request_key:<k>` | READS_INPUT (`via`, `flow`, `default`) | `$request->input/query/get/boolean/…('k', default)`, `request('k')`, `$validated['k']` / `$filters['k']` where the array is request data (see flow) |
| | VALIDATES (`rule`) | FormRequest `rules()` keys (inline `$request->validate([...])` rules are not emitted as VALIDATES; their keys are read through `$validated['k']`) |
| `method:…::rules` | VALIDATED_BY | an action whose parameter is typed with that FormRequest |
| `setting:<key>` | READS_SETTING / WRITES_SETTING (`owner`, `default`) | `getSetting('key', default)` / `setSetting` on classes declaring them (e.g. `Store`) |
| `resolution:<fn>#<target>@<line>` | HAS_RESOLUTION (fn → resolution); FALLS_BACK_TO (resolution → each source, `order`, `default`) | a value picked through a fallback chain (see below) |

- **Request-array flow** (through more than one helper level): a fixpoint over call arguments. Sources are request
  accessors (`validated()`, `all()`, `input()`, `only()`, … on a receiver typed Request/FormRequest), `request()`, arrays
  built from request keys, and app functions that *return* request data.
  The result is a per-parameter "this array is request data" fact with an evidence trail, e.g. in the sample
  `SalesReportService::build` reads `$filters['timezone']`, `report()` passes `$filters`, and `ReportController::top`
  passes the validated request.
- **Resolution sites**: an assignment or return whose expression is a `??` / `?:` chain (ternaries and `match` are
  expanded too), or a function with ≥ 2 ordered early returns (a `resolveX()` helper). Each operand is expanded into
  atoms: request key, setting (+ its literal default), model attribute → column (the Store attribute
  `default_timezone` → `stores.default_timezone`), a builder `value('col')` call, config/env, literal.
  Transparent wrappers (`strtoupper`, `trim`, casts) are recorded as `norm`. Locals are inlined from their assignment;
  parameters are expanded through their call sites (depth ≤ 4). A site is kept when its chain has a source and ≥ 2
  entries; the flattened `signature` is stored on the node.
- **TS literal fallbacks**: the outermost `a ?? b || 'LIT'` chain ending in a string/number literal becomes a
  `resolution:` node owned by the enclosing function/component (in the sample: `rows.value[0]?.timezone ?? 'UTC'` in
  `pages/reports/[id].vue`). Client call sites record the keys they pass through variables and builder calls
  (`arg_keys`, conditional keys apart), and a request builder that forwards a typed parameter takes its keys from the
  type (`forwarded`, with the argument position).

## The concept query: `resolutions`
`codegraph.cli resolutions CONCEPT [--within SUBSTR] [--no-client] [--json]` (MCP tool `resolutions`). Deterministic:
1. **Sites**: resolution nodes whose target name (or first source key) has the concept as its head word
   (`$timezone`, `resolveTimezone`, `customerTimezone`, `reports.timezone` …; singular/plural).
2. **Chains**: sites grouped by signature into lettered chains, each with its ordered fallback steps and file:line,
   plus the routes that reach the site (reverse closure).
3. **Divergence**: pairwise between request-driven chains: common prefix, first differing step, final fallback.
4. **Client**: for every frontend endpoint matched to a route that reaches a request-driven site: the request
   builder's key status (always / conditional / absent / unknown), each call site's passed keys, client literal
   fallbacks of the issuer or its callers, and a verdict (`sent`, `sent by some call sites`, `never sent (…)`, `unknown`).

Result for `resolutions timezone` on the sample (abridged):
```
[A] input:timezone > column:orders.customer_timezone > setting:locale.timezone > column:stores.default_timezone > 'UTC'   (1 site)
    ReportController::resolveTimezone  return  (returns)  @bookstore-api/app/Http/Controllers/ReportController.php:43
[B] input:timezone > setting:reports.timezone > 'UTC'   (1 site)
    SalesReportService::build  $timezone  (expr)  @bookstore-api/app/Services/SalesReportService.php:17
  [A] vs [B]: same up to input:timezone; then [A] column:orders.customer_timezone vs [B] setting:reports.timezone; same final fallback 'UTC'
  GET /api/v1/main/admin/reports/top  -> chain B via GET /v1/{store}/admin/reports/top
     request app/composables/useReports.ts#useReports.fetchTop @bookstore-web/app/composables/useReports.ts:9  builder keys: timezone: conditional
       called by app/pages/reports/[id].vue @bookstore-web/app/pages/reports/[id].vue:10  sends timezone=no  (passes: category_id, date_from)
       client fallback @bookstore-web/app/pages/reports/[id].vue:12: rows.value[0]?.timezone ?? 'UTC'
       => 'timezone': never sent (builder key is conditional and no call site passes it)
```

