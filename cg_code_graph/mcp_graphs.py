"""Graph registry for one cg-mcp process serving many graphs.

Entries come from a config file (``~/.config/cg/graphs.toml``, or ``CG_GRAPHS``),
repeatable ``--db`` / ``--db NAME=PATH``, and ``--db-dir``. A combined graph is one
entry with several repo roots. Each call opens its own connection.
"""
from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .core.store import GraphStore


class GraphError(Exception):
    """A graph name is unknown, or more than one graph matches and none was chosen."""


@dataclass
class GraphEntry:
    name: str
    db: Path
    roots: list[Path] = field(default_factory=list)
    default: bool = False
    tools: str | None = None

    def label(self) -> str:
        roots = ", ".join(str(r) for r in self.roots) or "(no roots)"
        return f"{self.name} [{roots}]"


@dataclass
class Registry:
    entries: list[GraphEntry]
    default: str | None = None
    source: str = ""

    def by_name(self) -> dict[str, GraphEntry]:
        return {e.name: e for e in self.entries}

    def names(self) -> list[str]:
        return [e.name for e in self.entries]


def default_graphs_path() -> Path:
    """``$XDG_CONFIG_HOME/cg/graphs.toml``, else ``~/.config/cg/graphs.toml``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "cg" / "graphs.toml"


def git_head(root: Path) -> str | None:
    """HEAD of ``root``, or None when git is missing or ``root`` is not a work tree."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip()
    return sha or None


def _valid_name(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c in "_.-" for c in name)


def parse_db_arg(text: str) -> tuple[str, Path]:
    """``NAME=PATH`` or a bare path (the name is the file stem)."""
    raw = text.strip()
    if "=" in raw:
        name, path = raw.split("=", 1)
        name, path = name.strip(), path.strip()
        if _valid_name(name) and "/" not in name and "\\" not in name and path:
            return name, Path(path).expanduser()
    path = Path(raw).expanduser()
    return path.stem or "graph", path


def roots_from_db(db: Path) -> list[Path]:
    """Repo roots recorded in this graph, including member roots of a combined graph."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(p) -> None:
        if not p:
            return
        try:
            path = Path(p).expanduser()
        except (TypeError, ValueError):
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    try:
        st = GraphStore(db)
        try:
            meta = st.meta()
        finally:
            st.db.close()
    except Exception:
        return []
    add(meta.get("root"))
    sources = meta.get("sources") or {}
    if isinstance(sources, dict):
        for member in sources.values():
            mp = Path(str(member)).expanduser()
            if mp.suffix == ".db":
                try:
                    inner = GraphStore(mp)
                    try:
                        add(inner.meta().get("root"))
                    finally:
                        inner.db.close()
                except Exception:
                    pass
            elif mp.is_dir():
                add(mp)
    return found


def _entry_from_mapping(name: str, body: dict, default_name: str | None) -> GraphEntry:
    if "db" not in body:
        raise GraphError(f"graph {name}: missing db")
    db = Path(str(body["db"])).expanduser()
    raw_roots = body.get("roots") or body.get("root") or []
    if isinstance(raw_roots, str):
        raw_roots = [raw_roots]
    roots = [Path(str(r)).expanduser() for r in raw_roots]
    if not roots:
        roots = roots_from_db(db)
    is_default = bool(body.get("default")) or name == default_name
    tools = body.get("tools")
    return GraphEntry(name=name, db=db, roots=roots, default=is_default,
                      tools=str(tools) if isinstance(tools, str) and tools else None)


def load_toml(path: Path) -> tuple[list[GraphEntry], str | None]:
    """Read a registry file. Supports ``[[graphs]]`` and ``[graphs.NAME]``."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as ex:
        raise GraphError(f"cannot read graph registry {path}: {ex}") from ex
    if not isinstance(data, dict):
        raise GraphError(f"cannot read graph registry {path}: expected a table")
    default = data.get("default")
    default_name = str(default) if isinstance(default, str) and default else None
    entries: list[GraphEntry] = []
    graphs = data.get("graphs")
    if isinstance(graphs, list):
        for i, body in enumerate(graphs):
            if not isinstance(body, dict) or not body.get("name"):
                raise GraphError(f"{path}: [[graphs]] item {i} needs a name")
            entries.append(_entry_from_mapping(str(body["name"]), body, default_name))
    elif isinstance(graphs, dict):
        for name, body in graphs.items():
            if isinstance(body, dict) and "db" in body:
                entries.append(_entry_from_mapping(str(name), body, default_name))
            elif isinstance(body, list):
                for item in body:
                    if isinstance(item, dict):
                        entries.append(_entry_from_mapping(str(item.get("name") or name), item, default_name))
    if default_name and not any(e.name == default_name for e in entries):
        raise GraphError(f"{path}: default {default_name!r} is not a registered graph")
    return entries, default_name


def entries_from_db_args(args: list[str]) -> list[GraphEntry]:
    out: list[GraphEntry] = []
    for text in args:
        name, db = parse_db_arg(text)
        out.append(GraphEntry(name=name, db=db, roots=roots_from_db(db)))
    return out


def entries_from_dir(directory: Path) -> list[GraphEntry]:
    directory = directory.expanduser()
    if not directory.is_dir():
        raise GraphError(f"--db-dir is not a directory: {directory}")
    out = []
    for db in sorted(directory.glob("*.db")):
        if db.name.endswith(("-wal", "-shm")):
            continue
        out.append(GraphEntry(name=db.stem, db=db, roots=roots_from_db(db)))
    return out


def merge_entries(groups: list[list[GraphEntry]]) -> list[GraphEntry]:
    """Later groups replace earlier entries with the same name."""
    order: list[str] = []
    by: dict[str, GraphEntry] = {}
    for group in groups:
        for entry in group:
            if entry.name not in by:
                order.append(entry.name)
            by[entry.name] = entry
    return [by[n] for n in order]


def build_registry(*, dbs: list[str] | None = None, db_dir: str | None = None,
                   config: Path | None = None, default: str | None = None,
                   read_config: bool = True) -> Registry:
    groups: list[list[GraphEntry]] = []
    source = []
    file_default = None
    if read_config and config is not None:
        if not config.is_file():
            raise GraphError(f"graph registry not found: {config}")
        loaded, file_default = load_toml(config)
        groups.append(loaded)
        source.append(str(config))
    if db_dir:
        groups.append(entries_from_dir(Path(db_dir)))
        source.append(f"dir:{db_dir}")
    if dbs:
        groups.append(entries_from_db_args(dbs))
        source.append("cli")
    entries = merge_entries(groups)
    chosen = default or file_default
    if chosen is None:
        marked = [e.name for e in entries if e.default]
        chosen = marked[-1] if marked else None
    if chosen and chosen not in {e.name for e in entries}:
        raise GraphError(f"default graph {chosen!r} is not registered")
    if not entries:
        raise GraphError("no graphs registered")
    return Registry(entries=entries, default=chosen, source=", ".join(source))


def save_toml(path: Path, entries: list[GraphEntry], default: str | None = None) -> None:
    """Write a registry. Caller backs up an existing file."""
    lines: list[str] = []
    if default:
        lines.append(f"default = {_toml_str(default)}")
        lines.append("")
    for entry in entries:
        lines.append("[[graphs]]")
        lines.append(f"name = {_toml_str(entry.name)}")
        lines.append(f"db = {_toml_str(str(entry.db))}")
        roots = ", ".join(_toml_str(str(r)) for r in entry.roots)
        lines.append(f"roots = [{roots}]")
        if entry.tools:
            lines.append(f"tools = {_toml_str(entry.tools)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _toml_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _under(root: Path, text: str) -> bool:
    """True when ``text`` is a path inside ``root`` (absolute, cwd-relative, or root-relative)."""
    raw = text.replace("\\", "/").split("#", 1)[0].strip()
    if not raw or raw.startswith(("page:", "repo:", "table:", "column:", "connection:", "env:", "config:")):
        return False
    if "/" not in raw and not raw.startswith("."):
        return False
    try:
        base = root.expanduser().resolve()
    except OSError:
        return False
    candidates = []
    path = Path(raw)
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(Path.cwd() / path)
        candidates.append(base / path)
    for cand in candidates:
        try:
            cand.resolve().relative_to(base)
            return True
        except (ValueError, OSError):
            continue
    return False


def _matches(entry: GraphEntry, texts: list[str]) -> bool:
    return any(_under(root, text) for root in entry.roots for text in texts)


def _choice_error(entries: list[GraphEntry]) -> GraphError:
    listed = ", ".join(e.label() for e in entries)
    return GraphError("ambiguous graph: pass graph= one of: " + listed)


def _root_set(entry: GraphEntry) -> set[Path]:
    out = set()
    for root in entry.roots:
        try:
            out.add(root.expanduser().resolve())
        except OSError:
            out.add(root)
    return out


def _superset(hits: list[GraphEntry]) -> GraphEntry | None:
    """When one match's roots contain every other match's roots, that graph is the combined one."""
    if len(hits) < 2:
        return None
    sets = [(e, _root_set(e)) for e in hits]
    strict = []
    for entry, roots in sets:
        if roots and all(other <= roots for other_entry, other in sets) and any(
                other < roots for other_entry, other in sets if other_entry is not entry):
            strict.append(entry)
    return strict[0] if len(strict) == 1 else None


def _deepest_repo(hits: list[GraphEntry], texts: list[str]) -> Path | None:
    """The repo containing the path: the deepest matching root, when that root is unique."""
    containing: list[Path] = []
    for entry in hits:
        for root in entry.roots:
            if not any(_under(root, text) for text in texts):
                continue
            try:
                containing.append(root.expanduser().resolve())
            except OSError:
                containing.append(root)
    if not containing:
        return None
    deepest: list[Path] = []
    for root in containing:
        if any(other != root and _under(root, str(other)) for other in containing):
            continue
        deepest.append(root)
    unique = set(deepest)
    if len(unique) != 1:
        return None
    return next(iter(unique))


def _own_graph(hits: list[GraphEntry], texts: list[str]) -> GraphEntry | None:
    """The one match whose roots are exactly the repo that contains the path."""
    repo = _deepest_repo(hits, texts)
    if repo is None:
        return None
    own = [entry for entry in hits if _root_set(entry) == {repo}]
    return own[0] if len(own) == 1 else None


def _pick(registry: Registry, hits: list[GraphEntry], texts: list[str]) -> GraphEntry:
    """Superset, then the graph that is only that repo, then the configured default."""
    chosen = _superset(hits)
    if chosen is not None:
        return chosen
    chosen = _own_graph(hits, texts)
    if chosen is not None:
        return chosen
    if registry.default:
        by = registry.by_name()
        default = by.get(registry.default)
        if default is not None and default in hits:
            return default
    raise _choice_error(hits)


def resolve(registry: Registry, *, graph: str | None, paths: list[str],
            cwd: Path | None = None, mcp_roots: list[str] | None = None) -> GraphEntry:
    """Pick a graph. Several matches use a superset, then a single-repo graph, then the default."""
    by = registry.by_name()
    if graph:
        if graph not in by:
            raise GraphError(f"unknown graph {graph!r}; choices: {', '.join(registry.names())}")
        return by[graph]
    path_hits = [e for e in registry.entries if paths and _matches(e, paths)]
    if len(path_hits) == 1:
        return path_hits[0]
    if len(path_hits) > 1:
        return _pick(registry, path_hits, paths)
    places: list[str] = []
    if mcp_roots:
        places.extend(mcp_roots)
    places.append(str(cwd or Path.cwd()))
    loc_hits = [e for e in registry.entries if _matches(e, places)]
    if len(loc_hits) == 1:
        return loc_hits[0]
    if len(loc_hits) > 1:
        return _pick(registry, loc_hits, places)
    if registry.default and registry.default in by:
        return by[registry.default]
    if len(registry.entries) == 1:
        return registry.entries[0]
    raise _choice_error(registry.entries)


def _languages(meta: dict) -> list[str]:
    if isinstance(meta.get("languages"), list):
        return [str(x) for x in meta["languages"]]
    stats = meta.get("stats") or {}
    found: list[str] = []
    cov = stats.get("coverage") if isinstance(stats, dict) else None
    if cov is None:
        cov = meta.get("coverage")
    blocks = []
    if isinstance(cov, dict):
        if isinstance(cov.get("languages"), list):
            blocks.append(cov["languages"])
        else:
            for value in cov.values():
                if isinstance(value, dict) and isinstance(value.get("languages"), list):
                    blocks.append(value["languages"])
    for block in blocks:
        for item in block:
            if isinstance(item, dict) and item.get("language"):
                found.append(str(item["language"]))
    if not found and isinstance(stats, dict):
        det = (stats.get("detected") or {}).get("languages") or {}
        if isinstance(det, dict):
            found.extend(str(k) for k in det)
    return sorted(set(found))


def _commit_text(meta: dict) -> str:
    commit = meta.get("commit")
    if isinstance(commit, dict):
        parts = [f"{k}={str(v)[:12]}" for k, v in commit.items() if v]
        return ", ".join(parts)
    if isinstance(commit, str) and commit:
        return commit[:12]
    return ""


def _freshness(meta: dict, roots: list[Path]) -> str:
    commit = meta.get("commit")
    heads = [git_head(r) for r in roots]
    heads = [h for h in heads if h]
    if not commit or not heads:
        return "unknown"
    if isinstance(commit, dict):
        vals = [str(v) for v in commit.values() if v]
        if not vals:
            return "unknown"
        return "fresh" if all(v in heads for v in vals) else "stale"
    return "fresh" if str(commit) in heads else "stale"


def describe(entry: GraphEntry) -> dict:
    """Name, roots, languages, indexed commit and freshness. Opens the DB briefly."""
    meta: dict = {}
    try:
        st = GraphStore(entry.db)
        try:
            meta = st.meta()
        finally:
            st.db.close()
    except Exception:
        meta = {}
    roots = entry.roots or ([Path(meta["root"])] if meta.get("root") else [])
    return {
        "name": entry.name,
        "roots": [str(r) for r in roots],
        "languages": _languages(meta),
        "commit": _commit_text(meta),
        "freshness": _freshness(meta, roots),
    }


def render_graphs(registry: Registry) -> str:
    lines = [f"{len(registry.entries)} graph(s)" + (f" default={registry.default}" if registry.default else "")]
    for entry in registry.entries:
        info = describe(entry)
        mark = " default" if registry.default == entry.name else ""
        langs = ", ".join(info["languages"]) or "none"
        roots = ", ".join(info["roots"]) or "(no roots)"
        commit = info["commit"] or "unknown"
        lines.append(f"{info['name']}{mark} roots: {roots} languages: {langs} "
                     f"commit: {commit} freshness: {info['freshness']} db: {entry.db}")
    return "\n".join(lines)
