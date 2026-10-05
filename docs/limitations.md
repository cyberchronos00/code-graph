# Known limitations

code-graph is beta. This page is the cross-cutting ceiling: how far an answer reaches when the graph is incomplete.
Language gaps are on the language pages, linked at the end.

## Types and dispatch

- Types are flow-insensitive: one type set per variable per function. PHP generics are outside the current scope.
- An unresolvable receiver falls back to a unique method name (`heuristic`, with a stoplist).
- A Builder passed as a generic `Builder` parameter loses its model.
- A trait, interface, generic, or virtual call reaches every implementation or override in the graph.
- The walk narrows to the receiver's class only where that type is known: the TypeScript checker, a Python inferred instance, or a Kotlin / Swift / Dart / PHP typed value.
- Generic substitution such as `Repo[B]().get()` returning `T` stays unresolved.
- A callback passed as a JSX attribute is a reference from the JSX site. It stays unlinked from the component's prop, so a shared component does not become a hub of every call site.

## Gate scenarios

One scenario per index ([configuration.md](configuration.md#gate-scenarios---gates)).

- The analysis is per function and context-insensitive. Parameters are TOP, except literal-argument method summaries.
- A flag passed as data, or stored on a property (`$this->x`), stays live. That is the conservative choice.
- The same canonical expression is the same atom. Bare null or empty checks assume the inputs are present.
- A branch with more than 12 atoms is assumed feasible.
- Middleware that aborts when a flag is off is not a gate.
- An edge is gated only when the gated function itself makes the reference.

## Index and answers

- A missing toolchain degrades that language: `skipped`, or `heuristic` when the exact indexer is absent. The rest of the index still runs.
- `cg coverage` and the MCP `coverage` tool print the mode, the reason, and an install hint ([completeness.md](completeness.md)).
- Route lists, caller lists, `reaches`, `tests`, and plan checks add a `coverage note:` when a blind spot or an unindexed file in the answer's languages and directories could affect them.
- Empty replies and unknown symbols end with a coverage line. MCP replies carry a `completeness` object.
- Blind-spot detectors are the list in [completeness.md](completeness.md#blind-spots).
- Code generated at build or run time, and files outside the indexed root, are absent from the graph and from that list.
- Generated or copied files need a marker from [generated.md](generated.md): gitattributes, a banner, or a known build path. A generator with none of those is source until `generated.paths` names it.
- Dangling symlinks are skipped with a per-file warning. The TypeScript walker does not follow symlinked directories.
- References taken from a SCIP index are attributed to the nearest enclosing definition by source range.
- Go and Java are SCIP recipes (`plugins/stubs`), not language plugins. Without a SCIP index their files are `unsupported`.

## Link, payload, and guards

- `cg link` matches method and path ([architecture.md](architecture.md#cross-repo-link)). Nest DTO fields, Fastify schema fields, and client body or query keys are stored and are not compared.
- An endpoint whose origin was not traced matches by suffix at `heuristic`.
- Payload checks run for a single route match ([architecture.md](architecture.md#payload--field-check)). Ambiguous matches are skipped.
- Server shapes are the declared schema or a returned literal. Framework error bodies (validation 422, auth 401, 500 pages) are absent.
- Enum checks need `choices=` or a `Literal` / `Enum` annotation.
- A route guard is a fact the framework plugin recorded on the route: middleware, Nest guards, `Depends`, `auth=`, permission classes.
- A check inside the handler body (`if (!req.user)`, `request.user.is_authenticated`) is not a guard.
- Project-wide defaults are not copied onto each route: Laravel kernel groups, Django `MIDDLEWARE`, DRF `DEFAULT_PERMISSION_CLASSES`, and `$this->middleware(...)` in a Laravel constructor.
- `cg routes --unguarded` lists routes those defaults protect. Preset lists decide which names count as auth ([configuration.md](configuration.md#framework-presets)).
- A project-specific guard name counts when it matches `auth.extra_patterns` or `--auth-pattern`.
- A sent-but-not-forwarded key is an object literal passed to a helper whose request keys are known, one call level deep. Spreads, runtime keys, and opaque objects are omitted, so no gap is reported for them.

## Plans, value facts, and the visual view

- Plan completeness rules are the named set: writers, readers, callers, clients, mirrors, identity columns, text mentions, and declared precedents. Anything else needs a plan entry or a `precedents` regex ([plans.md](plans.md)).
- `role: guard` sees a graph read of the declared column by the guard method or a direct callee. Whether that branch blocks the path is left to the reader.
- `--verify` reports changed and unchanged spans against the baseline, plus the planned edges. The free-text `intent` stays with the reviewer.
- External clients come from the snapshot file. Private repos are not indexed for that list.
- Findings come from a hand-refreshed snapshot of issue evidence lines, mapped to the innermost function span.
- Plan text mentions match exact names (identity columns, relation names) inside `plans.text_mention_dirs`.
- Value-fact concept match is the head word of the target, or of the first source key. `--within` is a substring filter ([value-facts.md](value-facts.md)).
- The "returns" form treats source order as fallback order. Locals keep their last assignment. Helper inlining stops at depth 4.
- A setting → API response → client chain is not followed. Response fields are not nodes.
- The visual view is comfortable at a few hundred nodes. Above about 1,500 it keeps the closest ones (`truncated`). The server binds to localhost and has no auth ([viz.md](viz.md)).

## Language pages

| area | page |
|---|---|
| Python roots, references, routes | [python.md](python.md) |
| PHP properties | [php.md](php.md) |
| TypeScript servers | [ts-frameworks.md](ts-frameworks.md) |
| Rust, C, C++ | [native.md](native.md) |
| Kotlin | [kotlin.md](kotlin.md) |
| Swift | [swift.md](swift.md) |
| Platforms and variants | [platforms.md](platforms.md) |
| Channels and tests | [channels-and-tests.md](channels-and-tests.md) |
| Protocols | [protocols.md](protocols.md) |
| Bridges | [bridges.md](bridges.md) |
