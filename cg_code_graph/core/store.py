"""SQLite graph store. One DB per project. Traversals use recursive CTEs."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .model import EDGE_KINDS, Edge, Node, CONFIDENCE_RANK

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS nodes(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, fqn TEXT,
  file TEXT, line INTEGER, end_line INTEGER, module TEXT, doc TEXT, lang TEXT,
  entry_kind TEXT, attrs TEXT);
CREATE TABLE IF NOT EXISTS edges(
  id INTEGER PRIMARY KEY, src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
  file TEXT, line INTEGER, confidence TEXT NOT NULL, conf_rank INTEGER NOT NULL, attrs TEXT, gate TEXT);
CREATE TABLE IF NOT EXISTS edge_kinds(kind TEXT PRIMARY KEY, propagates INTEGER, description TEXT);
-- which entry-point kinds reach a node (forward closure from entry points)
CREATE TABLE IF NOT EXISTS node_entry(node_id TEXT, entry_kind TEXT, entry_count INTEGER, sample_entry TEXT,
  PRIMARY KEY(node_id, entry_kind));
-- same closure, but only over edges that are live under a gate scenario (gate column NULL or != scenario)
CREATE TABLE IF NOT EXISTS node_entry_live(scenario TEXT, node_id TEXT, entry_kind TEXT, entry_count INTEGER, sample_entry TEXT,
  PRIMARY KEY(scenario, node_id, entry_kind));
-- guard predicates found by the evaluator: method -> constant value under a scenario
CREATE TABLE IF NOT EXISTS gate_predicates(scenario TEXT, method TEXT, value TEXT, how TEXT, PRIMARY KEY(scenario, method));
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src, kind);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst, kind);
CREATE INDEX IF NOT EXISTS ix_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS ix_nodes_fqn ON nodes(fqn);
"""


class GraphStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row

    @classmethod
    def create(cls, path: str | Path) -> "GraphStore":
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            q = Path(str(p) + suffix)
            if q.exists():
                q.unlink()
        s = cls(p)
        s.db.executescript(SCHEMA)
        s.db.executemany("INSERT INTO edge_kinds VALUES (?,?,?)",
                         [(k, int(v[0]), v[1]) for k, v in EDGE_KINDS.items()])
        return s

    def write(self, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(n.id, n.kind, n.name, n.fqn, n.file, n.line, n.end_line, n.module, n.doc, n.lang,
              n.entry_kind, json.dumps(n.attrs, default=str) if n.attrs else None) for n in nodes])
        self.db.executemany(
            "INSERT INTO edges(src,dst,kind,file,line,confidence,conf_rank,attrs,gate) VALUES (?,?,?,?,?,?,?,?,?)",
            [(e.src, e.dst, e.kind, e.file, e.line, e.confidence, CONFIDENCE_RANK[e.confidence],
              json.dumps(e.attrs, default=str) if e.attrs else None, e.gate) for e in edges])
        self.db.commit()

    def set_meta(self, **kv) -> None:
        self.db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)",
                            [(k, json.dumps(v, default=str)) for k, v in kv.items()])
        self.db.commit()

    def meta(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT * FROM meta")}

    def node(self, nid: str):
        return self.db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()

    def q(self, sql: str, params=()):
        return self.db.execute(sql, params).fetchall()
