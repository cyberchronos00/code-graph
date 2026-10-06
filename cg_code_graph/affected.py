"""Changed files → tests and entry points (#121).

A path is a whole file. A git revision (`--base`) keeps only the touched lines, and the innermost
symbol on those lines (a method, not the class around it). Renames and deletions still resolve.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from . import query as Q

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(root, args: list[str]) -> str:
    proc = subprocess.run(["git", "-C", str(root), "-c", "color.ui=false", "-c", "core.quotepath=false", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout or "git failed").strip())
    return proc.stdout


def _parse_name_status(raw: str) -> list[dict]:
    parts = raw.split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    out, i = [], 0
    while i < len(parts):
        status = parts[i]
        i += 1
        if not status:
            continue
        if status[0] in ("R", "C"):
            old, new = parts[i], parts[i + 1]
            i += 2
            out.append({"path": new, "status": "R" if status[0] == "R" else "C", "old_path": old})
        else:
            out.append({"path": parts[i], "status": status[0], "old_path": None})
            i += 1
    return out


def _diff_path(raw: str) -> str:
    """A `+++` path: git appends a tab to a name with a space and C-quotes one with special bytes."""
    raw = raw.rstrip("\t")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        import codecs
        try:
            raw = codecs.escape_decode(raw[1:-1].encode("utf-8"))[0].decode("utf-8", "surrogateescape")
        except ValueError:
            raw = raw[1:-1]
    return raw


def _hunks(diff: str) -> dict[str, list[tuple[int, int]]]:
    """New-side inclusive ranges per path. A pure deletion (`+c,0`) covers `(max(c, 1), c + 1)`."""
    found: dict[str, list[tuple[int, int]]] = defaultdict(list)
    current = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current = None
        elif line.startswith("rename to "):
            current = line[len("rename to "):]
        elif line.startswith("+++ "):
            path = _diff_path(line[4:])
            if path == "/dev/null":
                current = None
            else:
                current = path[2:] if path.startswith("b/") else path
        elif line.startswith("@@") and current:
            m = _HUNK.match(line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:
                found[current].append((max(start, 1), start + 1))
            else:
                found[current].append((start, start + count - 1))
    return found


def git_changes(root, base, paths: list[str] | None = None) -> list[dict]:
    """Working tree and index versus `base`. Each entry is path, status (A/M/D/R), old_path (R only)
    and ranges (None = the whole file). A and D are always whole-file. M and R use the hunks
    (an R with no hunks has an empty list). Untracked files are A, whole-file.
    Raises ValueError with git's message when `base` is not a revision or `root` is not a checkout."""
    spec = ["--", *paths] if paths else ["--"]
    listed = _parse_name_status(_git(root, ["diff", "--relative", "--no-color", "-M", "-z", "--name-status", base, *spec]))
    # fixed a/ b/ prefixes, no external diff or textconv: user config (diff.noprefix, diff.mnemonicPrefix,
    # diff.external, a textconv driver) would otherwise change the paths or the line numbers parsed here
    ranges = _hunks(_git(root, ["diff", "--relative", "--no-color", "--no-ext-diff", "--no-textconv",
                                "--src-prefix=a/", "--dst-prefix=b/", "-M", "-U0", base, *spec]))
    out = []
    for ch in listed:
        status = ch["status"]
        if status in ("A", "D"):
            ch["ranges"] = None
        elif status == "R":
            ch["ranges"] = list(ranges.get(ch["path"]) or [])
        else:
            if status not in ("M",):
                ch["status"] = "M"
            ch["ranges"] = list(ranges.get(ch["path"]) or [])
        if status != "R":
            ch["old_path"] = None
        out.append(ch)
    seen = {c["path"] for c in out}
    extra = ["--", *paths] if paths else []
    raw = _git(root, ["ls-files", "-o", "--exclude-standard", "-z", *extra])
    for path in raw.split("\0"):
        if path and path not in seen:
            out.append({"path": path, "status": "A", "old_path": None, "ranges": None})
    return out


def _inside(path: str, root: str) -> str:
    """Absolute or cwd-relative paths inside ROOT become ROOT-relative; anything else stays as given."""
    root_p = Path(root).resolve()
    raw = Path(path)
    candidates = [raw] if raw.is_absolute() else [Path.cwd() / raw, root_p / raw]
    for cand in candidates:
        try:
            return cand.resolve().relative_to(root_p).as_posix()
        except ValueError:
            continue
    return raw.as_posix()


def file_changes(paths, root) -> list[dict]:
    """Whole-file changes (status M) for an explicit path list."""
    return [{"path": _inside(p, root), "status": "M", "old_path": None, "ranges": None} for p in paths]


def _symbols(st, change) -> list[str]:
    path, status, ranges = change["path"], change["status"], change.get("ranges")
    old = change.get("old_path")
    if status == "R" and ranges == []:
        ids = Q.file_symbols(st, path, None)
        if old:
            have = set(ids)
            ids += [i for i in Q.file_symbols(st, old, None) if i not in have]
        return ids
    if status in ("A", "D") or ranges is None:
        return Q.file_symbols(st, path, None)
    ids = Q.file_symbols(st, path, ranges)
    if status == "R" and not ids and old:
        ids = Q.file_symbols(st, old, ranges)
    return ids


def _rows(st, ids: list[str]) -> dict[str, dict]:
    info = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id, kind, name, fqn, file, line, entry_kind, attrs FROM nodes WHERE id IN ({q})", chunk):
            info[r["id"]] = dict(r)
    return info


def _is_test(node: dict) -> bool:
    return node.get("kind") == "test" or node.get("entry_kind") == "test"


def _framework(node: dict) -> str | None:
    import json
    try:
        return (json.loads(node.get("attrs") or "{}") or {}).get("framework")
    except ValueError:
        return None


def _via_of(test: dict) -> str:
    path = test.get("path") or []
    if path:
        return Q.short_id(path[-1].get("to"))
    return Q.short_id(test.get("test"))


def affected(st, changes, min_conf="heuristic", near_depth=3, unit_only=False, max_targets=200, *, base=None) -> dict:
    """Union of tests and entry points reached by the symbols in `changes`."""
    files, not_indexed, picked = [], [], []
    seen = set()
    for change in changes:
        ids = [i for i in _symbols(st, change) if i not in seen]
        for i in ids:
            seen.add(i)
        if not ids:
            not_indexed.append(change["path"])
        files.append({"path": change["path"], "status": change["status"], "symbols": len(ids),
                      "ranges": change.get("ranges")})
        picked.extend(ids)
    info = _rows(st, picked)
    changed_tests, seeds = [], []
    for nid in picked:
        node = info.get(nid)
        if node is None:
            continue
        if _is_test(node):
            changed_tests.append({"test": nid, "name": node.get("name"), "framework": _framework(node),
                                  "file": node.get("file"), "line": node.get("line"), "via": Q.short_id(nid)})
        else:
            seeds.append(node)
    seeds.sort(key=lambda n: (n.get("file") or "", n.get("line") or 0, n["id"]))
    changed_tests.sort(key=lambda t: (t.get("file") or "", t.get("line") or 0, t["test"]))
    total = len(seeds)
    cap = max_targets if max_targets and max_targets > 0 else None      # 0 / None: no cap
    walked = seeds[:cap] if cap is not None else seeds
    truncated = {"symbols": total, "walked": len(walked)} if cap is not None and total > cap else None
    seed_ids = [n["id"] for n in walked]
    covered = Q.tests_covering(st, "", targets=seed_ids, min_conf=min_conf, near_depth=near_depth, unit_only=unit_only)
    changed_ids = {t["test"] for t in changed_tests}
    tests = {}
    for key in ("direct", "transitive", "ui"):
        group = []
        for t in covered.get(key) or []:
            if t.get("test") in changed_ids:
                continue
            t["via"] = _via_of(t)
            group.append(t)
        tests[key] = group
    reached = Q.reaches(st, seed_ids, min_conf=min_conf, gate=None) if seed_ids else {"items": []}
    entry_points, have = [], set()
    for item in reached.get("items") or []:
        ek = item.get("entry_kind")
        if not ek or ek == "test" or item["id"] in have:
            continue
        have.add(item["id"])
        path = item.get("path") or []
        via_id = path[-1]["to"] if path else item["id"]
        entry_points.append({"id": item["id"], "entry_kind": ek, "name": item.get("name"), "file": item.get("file"),
                             "line": item.get("line"), "via": Q.short_id(via_id)})
    listed = [t for key in ("direct", "transitive", "ui") for t in tests[key]] + changed_tests
    test_files = sorted({t["file"] for t in listed if t.get("file")})
    stats = dict(covered.get("stats") or {})
    stats.update(direct=len(tests["direct"]), transitive=len(tests["transitive"]), ui=len(tests["ui"]),
                 changed=len(changed_tests), entry_points=len(entry_points), symbols=sum(f["symbols"] for f in files),
                 files=len(files))
    return {"base": base, "files": files, "not_indexed": not_indexed, "targets": seed_ids,
            "changed_tests": changed_tests, "tests": tests, "test_files": test_files,
            "entry_points": entry_points, "truncated": truncated, "stats": stats}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _file_line(entry: dict, missing: set[str]) -> str:
    path = entry["path"]
    if path in missing:
        return f"?  {path}  not in the index"
    n = entry.get("symbols") or 0
    if entry.get("status") == "D":
        tail = "  (from the index)"
    elif entry.get("ranges"):
        tail = " (lines " + ", ".join(f"{a}-{b}" for a, b in entry["ranges"]) + ")"
    else:
        tail = ""
    return f"{entry.get('status') or 'M'} {path}  {_plural(n, 'symbol')}{tail}"


def _why(stats: dict) -> str:
    if stats.get("tests_in_graph"):
        why = (f" ({stats['tests_in_graph']} test cases are indexed; none calls the target, directly or through"
               " application code)")
    elif stats.get("test_files"):
        why = (f" (the graph has {stats['test_files']} test files but no recognised test cases; the supported"
               " frameworks are listed in docs/channels-and-tests.md)")
    else:
        why = " (the graph has no test code: no test files or test cases were indexed)"
    return "no indexed test reaches the target" + why


def render_affected(res: dict, limit=60) -> str:
    """Text report: changed files, tests grouped by file, then entry points."""
    files = res.get("files") or []
    symbols = sum(f.get("symbols") or 0 for f in files)
    header = f"changed: {_plural(len(files), 'file')}, {_plural(symbols, 'symbol')}"
    if res.get("base"):
        header += f" [base {res['base']}]"
    missing = set(res.get("not_indexed") or [])
    lines = [header]
    lines += [_file_line(f, missing) for f in files]
    tests = res.get("tests") or {}
    direct, trans, ui = tests.get("direct") or [], tests.get("transitive") or [], tests.get("ui") or []
    changed = res.get("changed_tests") or []
    buckets: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    for how, group in (("direct", direct), ("transitive", trans), ("ui", ui), ("changed", changed)):
        for t in group:
            buckets[t.get("file") or ""].append((t, how))
    n_tests = len(direct) + len(trans) + len(ui) + len(changed)
    n_files = len(res.get("test_files") or [])
    lines.append("")
    lines.append(f"tests: {n_tests} ({len(direct)} direct, {len(trans)} transitive, {len(ui)} UI, "
                 f"{len(changed)} changed) in {_plural(n_files, 'file')}")
    if n_tests == 0:
        lines.append(_why(res.get("stats") or {}))
    for path in res.get("test_files") or []:
        group = buckets.get(path) or []
        if not group:
            continue
        lines.append(path)
        for t, how in group[:limit]:
            fw = t.get("framework") or "test"
            lines.append(f"  {t.get('name')} [{fw}] :{t.get('line')}  {how}  via {t.get('via') or '?'}")
        if len(group) > limit:
            lines.append(f"... {len(group) - limit} more")
    cut = res.get("truncated")
    if cut:
        lines.append(f"(walked the first {cut.get('walked')} of {cut.get('symbols')} changed symbols; raise --max-targets)")
    eps = res.get("entry_points") or []
    lines.append("")
    lines.append(f"entry points: {len(eps)}")
    for ep in eps[:limit]:
        lines.append(f"  {ep.get('entry_kind')}  {ep.get('name')}  ({ep.get('file')}:{ep.get('line')})  via {ep.get('via')}")
    if len(eps) > limit:
        lines.append(f"... {len(eps) - limit} more")
    return "\n".join(lines)


def render_quiet(res: dict) -> str:
    """Test file paths only, one per line."""
    return "\n".join(res.get("test_files") or [])
