# Attack surface

`cg surface` reads facts the index already holds and answers three questions: what does the code expose, what does it
connect to, and where are credentials and transport protection weak. Nothing is rescanned. A secret value is never
stored or printed: a finding names the kind and the location.

```bash
cg surface --db out/graph.db                          # findings, grouped by type then protocol
cg surface --db out/graph.db --inbound --outbound     # plus both surface listings
cg surface --db out/graph.db --format sarif > cg.sarif
cg surface --db out/graph.db --fail-on hardcoded,unverified   # exit 1 in CI
```

## What it lists

| surface | items | per item |
|---|---|---|
| `--inbound` | HTTP routes, webhooks, WebSocket / Socket.IO / SSE, gRPC / GraphQL / tRPC / JSON-RPC handlers, message consumers, TCP / UDP listeners, Unix sockets, modelled IPC handlers (Electron, Tauri, Android) | guard state, whether it reaches a write (as `cg routes --writes`), protocol, transport, bind exposure for listeners |
| `--outbound` | every `external:` system ([External systems](external.md)) | address source, credential source, TLS, entry points that reach it |

Guard state is `guarded`, `secret-checked` (a signature or shared-secret check), `inline-guarded` (an unconditional
check inside the action), `unguarded`, or `unchecked` when the protocol records no guards. Socket.IO handlers in a
client bundle are not an inbound surface and are left out.

## Findings

| finding | triggers | severity |
|---|---|---|
| `hardcoded` | an external system or SDK call with a literal credential (`credential_source=literal`, `credential_literal`, an edge with `literal_credential`); a DSN with a password in it; a config key named like a secret (`password`, `token`, `secret`, `*_key`) with a literal value, or an `env('X', 'literal')` default for a secret-looking variable | high |
| `plaintext` | an external system with `tls=false` and a non-loopback host (`http://`, `ws://`, `redis://`, `amqp://`, `ftp://`, `ldap://`, plain SMTP, a Docker TCP host); also Telnet / TFTP, MQTT on 1883 and SMTP on 25 at `heuristic` | medium |
| `unverified` | a webhook route that reads a provider header and never verifies it ([Protocol links](protocols.md)) | high |
| `unguarded` | a route or handler with no guard: the `cg routes --unguarded` rule, so an unconditional inline check counts; `--strict` keeps route guards only | medium |
| `exposed-listener` | a TCP / UDP listener bound to all interfaces (`0.0.0.0`, `::`, no host) | medium |

Every finding carries `finding`, `severity`, `confidence`, `protocol`, `node`, `file`, `line`, `entry_points`,
`detail` and `fingerprint`. `entry_points` lists the entry kinds that reach the finding (`kind`, `count`, `sample`; at
most five kinds, the total in `entry_point_total`). On a combined graph (`cg link`) each finding also names its `repo`.
The fingerprint hashes finding, node id and file, never the line, so it survives edits above the finding.

```text
$ cg surface --db out/graph.db --finding hardcoded
attack surface: 2 finding(s) (2 high)
inbound surface: 7 item(s) (1 guarded, 1 secret-checked, 2 unchecked, 3 unguarded); outbound surface: 4 system(s)

== hardcoded (2) - Hardcoded credential ==
  postgres
    HIGH [exact] external:postgres:db.bookstore.example:5432
      literal password in code or configuration for db.bookstore.example:5432 (value not shown)
      at bookshop/settings.py:6
      reached from no entry point
      fingerprint 75454517a93c3363
  saas
    HIGH [exact] external:saas:postmark
      literal key in code or configuration for postmark (value not shown)
      at bookshop/notify.py:6
      reached from http_route(1)
      fingerprint 3412ee3e89b46e48
```

## Options

| option | meaning |
|---|---|
| `--inbound` / `--outbound` | list that surface; findings are limited to its direction |
| `--protocol P` | one protocol (`http`, `ws`, `grpc`, `tcp`, `redis`, `postgres`, `saas`, ...) |
| `--finding F` | one finding type |
| `--min-confidence resolved` | drop `heuristic` findings and items (`exact` keeps only exact) |
| `--format text\|json\|sarif` | `json` is the full structure; `sarif` is SARIF 2.1.0 |
| `--fail-on f1,f2` | exit 1 when any listed finding remains after ignores |
| `--max-items N` | cap on listed findings and items (default 200); totals stay in the summary |
| `--strict` | `unguarded` counts route guards only |
| `--show-ignored` | list the findings `surface.ignore` accepted |

SARIF has one rule per finding type (`cg.surface.<finding>`), `level` from severity (high `error`, medium `warning`),
repo-relative locations, and the fingerprint in `partialFingerprints`. Ignored findings appear as results with a
`suppressions` entry only with `--show-ignored`.

## Gates

`--fail-on` exits 1 when a listed finding remains, 0 otherwise, 2 for an unknown name. It judges every finding type
after ignores, `--protocol` and `--min-confidence`; `--finding`, `--inbound` and `--outbound` only change what is printed.

```yaml
# GitHub Actions
- run: cg index . --db out/graph.db
- run: cg surface --db out/graph.db --format sarif > cg.sarif && cg surface --db out/graph.db --fail-on hardcoded,unverified
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with: { sarif_file: cg.sarif }
```

## Ignores

An accepted risk goes in `.cg.yaml`. The reason is required; `cg config validate` rejects an entry without one.

```yaml
surface:
  ignore:
    - {finding: unguarded, path: "app/Http/Controllers/HealthController.php", reason: public health probe}
    - {finding: plaintext, id: "external:redis:cache:6379", reason: compose-internal network}
    - {finding: hardcoded, fingerprint: 75454517a93c3363, reason: rotated test key}
```

`finding` is a finding type, plus one of `path` (a file, a directory or a glob, repo-relative), `id` (a node id or glob)
or `fingerprint`. Every key given must match. The file is read again from the indexed root on each run, so an ignore
applies without re-indexing. Ignored findings are counted in the summary and listed with `--show-ignored`. On a
combined graph an ignore applies to the findings of the repo whose `.cg.yaml` holds it.

## MCP

The `attack_surface` tool takes `inbound`, `outbound`, `protocol`, `finding`, `min_confidence`, `strict`,
`show_ignored` and `max_items`. The reply is the text report; the structured reply has the full result under
`surface` next to the `completeness` object ([MCP server](mcp.md)).

## Not detected yet

TLS verification switched off, SSRF candidates, insecure gRPC channels and IPC exposure findings are not produced by
this version ([Known limitations](limitations.md#attack-surface)). New detectors add a finding type; the shape above does not change.
