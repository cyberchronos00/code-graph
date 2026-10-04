"""Canonical "how to read this codebase with cg" guidance.

Single source for this repo's AGENTS.md and for the blocks that `cg agents` installs into a
project's AGENTS.md / CLAUDE.md / Cursor rules, so the wording never drifts. TEXT is the body;
wrap it between BEGIN_MARK and END_MARK to get a block that can be replaced in place."""

BEGIN_MARK = "<!-- BEGIN cg agent rules (managed by `cg agents`; edit cg, not here) -->"
END_MARK = "<!-- END cg agent rules -->"

TEXT = """\
# Reading this codebase with cg

This repository ships `cg`, a code-graph tool. Read the code by symbol through the index
instead of opening whole files.

## Build the index first
    pip install -e . -q                     # make `cg` this checkout's version
    cg index . --db out/graph.db --name repo
There is no incremental index: re-run `cg index` after large changes.

## Find code (get file:line; avoid ls / cat / broad grep)
    cg search <name> --db out/graph.db        # nodes by name or FQN substring
    cg impact <symbol> --db out/graph.db      # callers up to entry points (reverse)
    cg downstream <symbol> --db out/graph.db  # what the symbol calls and reaches (forward)
    cg node <symbol> --db out/graph.db        # one node's location, kind and edges
`<symbol>` is a name, an FQN, `Class.method`, or a node id. Ambiguous names list candidates.

## Read source (never cat a whole file; never more than ~200 lines at once)
    cg snippet <symbol> --db out/graph.db                 # just that symbol's source, numbered
    cg snippet <symbol> --db out/graph.db --context 5     # a few lines around it
    sed -n '120,180p' path/to/file                      # a plain line range when needed
`cg snippet` prints a `path:start-end` header then the body; `--max-lines` caps long bodies.

## Follow an edge only when your change reaches it
Use `cg impact` and `cg downstream` to decide whether a caller or callee matters before
opening it. Chase a dependency only if your edit changes what crosses that edge.

## After editing, re-query instead of rereading
Re-run `cg index`, then `cg snippet` or `cg impact` on the changed spot to confirm the new
shape, rather than reopening files.

## Keep output small
Pipe long output through `head` or `tail`:
    cg impact <symbol> --db out/graph.db | head -n 40
While iterating, run one file: `pytest -q -x tests/test_x.py`.
Run the full suite once at the end: `pytest -q 2>&1 | tail -n 30`.
"""


def document() -> str:
    """The repo-root AGENTS.md content (TEXT, newline-terminated)."""
    return TEXT


def block() -> str:
    """The marked block `cg agents` writes into a project file; replaced in place on re-run."""
    return BEGIN_MARK + "\n" + TEXT + END_MARK + "\n"
