"""Plugin interfaces.

LanguagePlugin   detect(project) -> bool; index(project, builder, frameworks) adds nodes/edges.
FrameworkPlugin  sits on top of one language plugin (e.g. Laravel on PHP). It gets the
                 language plugin's analysis context (symbol tables + resolution hooks)
                 and contributes framework nodes/edges (routes, tables, commands...).
ScipPlugin       a LanguagePlugin backed by any SCIP-producing indexer (scip-php,
                 scip-typescript, scip-clang, rust-analyzer scip, scip-go...). Imports
                 the SCIP index into the same graph (see plugins/scip/importer.py).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import Edge, Node, node_id


@dataclass
class Project:
    root: Path
    name: str
    # filled by detection: {"languages": {...}, "frameworks": {...}}
    detected: dict[str, Any] = field(default_factory=dict)
    # user options, e.g. {"gates": [scenario, ...]} loaded from config/<project>.gates.json
    options: dict[str, Any] = field(default_factory=dict)

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def read_json(self, rel: str) -> dict | None:
        p = self.root / rel
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None


class GraphBuilder:
    """In-memory accumulator; de-duplicates nodes by id and edges by (src,dst,kind,line)."""

    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple, Edge] = {}
        # set by a language plugin while emitting edges for a reference that is dead under a gate
        # scenario: {"gate": name, "guard": "file:line", "guard_call": "..."}
        self.current_gate: dict | None = None

    def add_node(self, kind: str, key: str, name: str | None = None, **kw) -> str:
        nid = node_id(kind, key)
        n = self.nodes.get(nid)
        if n is None:
            self.nodes[nid] = Node(id=nid, kind=kind, name=name or key, **kw)
        else:
            for k, v in kw.items():  # fill blanks, merge attrs
                if k == "attrs" and v:
                    n.attrs.update(v)
                elif v is not None and getattr(n, k, None) in (None, ""):
                    setattr(n, k, v)
        return nid

    def has(self, nid: str) -> bool:
        return nid in self.nodes

    def add_edge(self, src: str, dst: str, kind: str, file=None, line=None, confidence="exact", **attrs) -> None:
        if src == dst and kind == "CALLS":
            return
        g = self.current_gate
        if g and g.get("src") != src:
            g = None  # only references made by the gated function itself carry the gate
        key = (src, dst, kind, file, line, g["gate"] if g else None)
        if key in self.edges:
            return
        if g:
            attrs = {**attrs, "guard": g["guard"], "guard_expr": g.get("expr")}
        self.edges[key] = Edge(src=src, dst=dst, kind=kind, file=file, line=line, confidence=confidence, attrs=attrs,
                               gate=g["gate"] if g else None)


    def move_edges(self, moves: dict[str, list[tuple[int, int, str]]]) -> int:
        """Re-attribute edges to a finer-grained source: moves = {src: [(lo, hi, new_src), ...]}; an edge from src
        whose line lies in [lo, hi] now starts at new_src (the innermost range wins). Used for closures that are
        their own graph nodes (channel callbacks, Pest tests) while the PHP plugin emits their facts from the file."""
        n = 0
        for key, e in list(self.edges.items()):
            rs = moves.get(e.src)
            if not rs or e.line is None:
                continue
            hit = [r for r in rs if r[0] <= e.line <= r[1]]
            if not hit:
                continue
            new_src = min(hit, key=lambda r: r[1] - r[0])[2]
            del self.edges[key]
            nk = (new_src,) + key[1:]
            if nk not in self.edges and not (new_src == e.dst and e.kind == "CALLS"):
                e.src = new_src
                self.edges[nk] = e
            n += 1
        return n

    def retype_edge(self, key: tuple, kind: str, **attrs) -> None:
        e = self.edges.pop(key)
        e.kind = kind
        e.attrs = {**e.attrs, **attrs}
        nk = (key[0], key[1], kind) + key[3:]
        if nk not in self.edges:
            self.edges[nk] = e


class LanguagePlugin(ABC):
    name: str = "?"

    @abstractmethod
    def detect(self, project: Project) -> bool: ...

    @abstractmethod
    def index(self, project: Project, builder: GraphBuilder, frameworks: list["FrameworkPlugin"]) -> dict:
        """Add nodes/edges; return stats."""


class FrameworkPlugin(ABC):
    name: str = "?"
    language: str = "?"

    @abstractmethod
    def detect(self, project: Project) -> bool: ...

    def register_hooks(self, lang_ctx: Any) -> None:
        """Called before the language plugin resolves references (type rules, fact handlers)."""

    @abstractmethod
    def contribute(self, project: Project, builder: GraphBuilder, lang_ctx: Any) -> dict:
        """Called after language indexing; add framework nodes/edges; return stats."""
