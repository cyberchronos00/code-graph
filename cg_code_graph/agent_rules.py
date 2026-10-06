"""Canonical "how to read this codebase with cg" guidance.

TEXT is this repository's contributor guide (`document()` writes AGENTS.md). PROJECT_TEXT
(`project_text()`) is the short block `cg agents` installs into an application repo. Pointers
for extra targets use the same markers so a later run can swap a full block for a pointer."""

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


def pin() -> str:
    """Published package floor: ``cg-code-graph>=MAJOR.MINOR`` from ``cg_code_graph.__version__``."""
    import re

    from . import __version__

    m = re.match(r"(\d+)\.(\d+)", __version__)
    return f"cg-code-graph>={m.group(1)}.{m.group(2)}" if m else "cg-code-graph"


def project_text() -> str:
    """Short guidance installed into application repos (at most 1,000 characters)."""
    p = pin()
    return f"""\
# Reading this codebase with cg

`cg` indexes this repo into a code graph. Look code up by symbol instead of opening whole files.

    pip install '{p}'                       # provides `cg` and `cg-mcp`
    cg index . --db out/graph.db              # re-run after large changes
    cg search <name> --db out/graph.db        # find a symbol: file:line
    cg snippet <symbol> --db out/graph.db     # just its source, numbered
    cg impact <symbol> --db out/graph.db      # callers up to routes / jobs / pages
    cg affected --base main --db out/graph.db # tests and entry points a change reaches

`<symbol>` is a name, an FQN, `Class.method` or a node id. The MCP server (`cg-mcp --db out/graph.db`)
offers the same queries as tools. `cg <command> -h` lists flags; pipe long output through `head`.
"""


def pointer_text(primary_file: str, target: str) -> str:
    """One- or two-line pointer (at most 200 characters) at a non-primary target."""
    if target == "claude" and primary_file == "AGENTS.md":
        return "cg (code graph) guidance: @AGENTS.md\n"
    return (
        f"cg (code graph) guidance is in {primary_file}: "
        f"cg search / snippet / impact <symbol> --db out/graph.db.\n"
    )


def block(body: str | None = None) -> str:
    """Markers around ``body`` (default ``project_text()``); replaced in place on re-run."""
    if body is None:
        body = project_text()
    return BEGIN_MARK + "\n" + body + END_MARK + "\n"
