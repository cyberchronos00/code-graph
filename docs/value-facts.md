# Value facts and `resolutions`

Facts about values, from the PHP and TypeScript ASTs (`codegraph/plugins/laravel/values.py` and
the TS extractor).

| node | edges | from |
|---|---|---|
| `request_key:<k>` | READS_INPUT (`via`, `flow`, `default`) | `$request->input` / `query` / `boolean`, `request('k')`, `$validated['k']` when that array is request data |
| | VALIDATES (`rule`) | FormRequest `rules()` keys. Inline `$request->validate([...])` is not a VALIDATES edge; the key is read through `$validated['k']` |
| `method:…::rules` | VALIDATED_BY | an action whose parameter is typed with that FormRequest |
| `setting:<key>` | READS_SETTING / WRITES_SETTING | `getSetting` / `setSetting` on a class that declares them |
| `resolution:<fn>#<target>@<line>` | HAS_RESOLUTION, FALLS_BACK_TO (`order`, `ambiguous`, `candidates`) | a `??` / `?:` / `match` chain, or a helper with two or more ordered early returns |

Request-array flow is a fixpoint over call arguments, through more than one helper. Sources are
request accessors (`validated()`, `all()`, `input()`, `only()`, … on a receiver typed
Request or FormRequest), `request()`, arrays built from request keys, and app functions that
return request data. The result is a per-parameter fact with an evidence trail. In the bookstore
sample, `SalesReportService::build` reads `$filters['timezone']`, `report()` passes `$filters`, and `ReportController::top` passes the validated request.

A resolution site is an assignment or return whose expression is a `??` / `?:` chain (ternaries
and `match` are expanded), or a function with at least two ordered early returns (`resolveX()`). Each operand becomes an atom: request key, setting (plus its literal default), model
attribute → column (`default_timezone` → `stores.default_timezone`), a builder `value('col')`, config/env, or a literal. A receiver that can be several models (`Income|Expense $doc`, or a
variable assigned from either) reads one column per model as one step. The signature names them
all (`column:expenses.paid_at|incomes.paid_at`) and each FALLS_BACK_TO edge of that step has
the same `order`, `ambiguous: true` and `candidates`. Candidates narrow to the tables whose
migrations declare the column, else to the tables the migrations create. Transparent wrappers (
`strtoupper`, `trim`, casts) are `norm`. Locals are inlined from their assignment. Parameters
expand through their call sites (depth ≤ 4). A site is kept when its chain has a source and at
least two entries. The flattened `signature` is stored on the node.

In TypeScript, the outermost `a ?? b || 'LIT'` chain that ends in a string or number literal is
a `resolution:` node of the enclosing function or component (in the sample,
`rows.value[0]?.timezone ?? 'UTC'` in `pages/reports/[id].vue`). Client call sites record the
keys they pass through variables and builder calls (`arg_keys`, conditional keys apart). A
request builder that forwards a typed parameter takes its keys from the type (`forwarded`,
with the argument position).

```text
$ cg resolutions timezone --db out/app.db
[A] input:timezone > column:orders.customer_timezone > setting:locale.timezone > column:stores.default_timezone > 'UTC'
    ReportController::resolveTimezone  @ app/Http/Controllers/ReportController.php:43
[B] input:timezone > setting:reports.timezone > 'UTC'
    SalesReportService::build  $timezone  @ app/Services/SalesReportService.php:17
  [A] vs [B]: same up to input:timezone; then column:orders.customer_timezone vs setting:reports.timezone; same final fallback 'UTC'
  GET /api/v1/main/admin/reports/top -> chain B
     request useReports.fetchTop  builder keys: timezone: conditional
       called by pages/reports/[id].vue  sends timezone=no
       client fallback: rows.value[0]?.timezone ?? 'UTC'
       => 'timezone': never sent (builder key is conditional and no call site passes it)
```

`cg resolutions CONCEPT [--within SUBSTR] [--no-client] [--json]` (MCP `resolutions`):

1. Sites whose target name or first source key has the concept as its head word (singular and
   plural).
2. Chains grouped by signature, with the routes that reach each site.
3. Divergence between request-driven chains: common prefix, first differing step, final
   fallback.
4. Client: for each frontend endpoint on those routes, whether the builder key is always,
   conditional, absent or unknown, and a verdict (`sent`, `sent by some call sites`,
   `never sent`, `unknown`).
