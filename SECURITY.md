# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub's private vulnerability reporting: on the repository page, open the
**Security** tab and choose **Report a vulnerability** (this creates a private security advisory visible only to
the maintainers). Please include:

- what is affected (component, command or MCP tool) and the version or commit;
- steps to reproduce, ideally against the bundled sample apps;
- the impact you expect.

The maintainers will acknowledge the report as soon as they can. Fixes ship as normal commits, and the advisory is published
once a fix is available.

## Scope and threat model

code-graph is a local developer tool:

- the indexer only **reads** source files. It does not execute the indexed project or connect to its databases. It
  does run the bundled PHP and Node extractors, plus `composer` / `npm` for its own dependencies;
- the visual view (`serve`) is a read-only HTTP server **without authentication**. It binds to `127.0.0.1` by
  default and shows source snippets of the indexed code, so do not expose it on a network;
- the MCP server talks over stdio only. Its `index` tool rebuilds the graph from a path the client provides.

Issues in these areas are in scope: path traversal or arbitrary file reads through the viz server or MCP tools,
code execution triggered by indexing a malicious repository, and injection through crafted plan or gates files.
