"""Dispatch follow-ups (#55): TypeScript interface members are nodes (a call on an interface-typed value has a
target) linked with IMPLEMENTED_BY to the implementing class members (`implements`, also through base classes and
interface `extends`, and structurally where `new X()` is used as the interface); `impact` / `reaches` / `tests` on an
inherited `Sub.method` keep only the calls whose receiver can be a Sub (TypeScript checker types, Python inferred
instances and collection elements); a method-to-method container binding (Nest `useClass`) is a dispatch hop, shown
under `overrides:` and not as a caller. Fixtures are written from scratch in a temp dir."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

TS_DEPS = ROOT / "codegraph" / "plugins" / "ts" / "extractor" / "node_modules"
needs_ts = pytest.mark.skipif(not TS_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")

TS = {
    "tsconfig.json": '{"compilerOptions": {"strict": true, "target": "es2020"}, "include": ["src"]}',
    "package.json": '{"name": "feeds", "devDependencies": {"typescript": "5"}}',
    "src/feed.ts": '''
        export interface Base {
          peek(): number
        }
        export interface FeedAPI extends Base {
          fetch(cursor: string): Promise<string[]>
          label: (x: number) => string
        }
        export interface Unused {
          onClick: () => void
        }
        export class Following implements FeedAPI {
          async fetch(cursor: string) { return [cursor] }
          peek() { return 1 }
          label = (x: number) => `f${x}`
        }
        export class Merge {
          async fetch(cursor: string) { return [cursor, 'm'] }
          peek() { return 2 }
          label(x: number) { return `m${x}` }
        }
        export class Sub extends Following {
          peek() { return 3 }
        }
        export class Sub2 extends Following {}
        export function load(api: FeedAPI) { return api.fetch('top') }
        export function first(api: FeedAPI) { return api.peek() + api.label(1).length }
        export function make(kind: string): FeedAPI {
          if (kind === 'm') return new Merge()
          return new Following()
        }
        export function useSub(s: Sub) { return s.fetch('x') }
        export function useSub2(s: Sub2) { return s.fetch('y') }
        export function useFollowing(f: Following) { return f.fetch('z') }
        export function useEither(f: Sub | Sub2) { return f.fetch('w') }
        function mix<T extends new (...a: any[]) => object>(base: T) { return class extends base {} }
        export class Mixed extends mix(Sub2) {}
        export function useMixed(m: Mixed) { return m.fetch('q') }
    ''',
}

PY = {
    "pkg/__init__.py": "",
    "pkg/core.py": '''
        class Base:
            def run(self):
                return 0


        class A(Base):
            pass


        class B(Base):
            pass


        class C(B):
            pass


        def use_a():
            a = A()
            return a.run()


        def use_b():
            return B().run()


        def use_c():
            return C().run()


        def use_base(x: Base):
            return x.run()


        def use_list():
            for item in [A(), A()]:
                item.run()


        def use_mixed():
            for item in [A(), C()]:
                item.run()


        def entry():
            use_a()
            use_b()
    ''',
}

NEST = {
    "tsconfig.json": '{"compilerOptions": {"experimentalDecorators": true, "emitDecoratorMetadata": true, "target": "es2020"}, '
                     '"include": ["src"]}',
    "package.json": '{"name": "files", "dependencies": {"@nestjs/common": "^11.0.0", "@nestjs/core": "^11.0.0"}}',
    "src/file.repository.ts": '''
        export abstract class FileRepository {
          abstract create(name: string): Promise<string>
          abstract remove(name: string): Promise<void>
        }
    ''',
    "src/relational.repository.ts": '''
        import { Injectable } from '@nestjs/common'
        import { FileRepository } from './file.repository'

        @Injectable()
        export class RelationalFileRepository implements FileRepository {
          async create(name: string): Promise<string> { return name }
          async remove(name: string): Promise<void> {}
        }
    ''',
    "src/memory.repository.ts": '''
        import { Injectable } from '@nestjs/common'

        @Injectable()
        export class MemoryFileRepository {
          async create(name: string): Promise<string> { return name + '!' }
          async remove(name: string): Promise<void> {}
        }
    ''',
    "src/files.service.ts": '''
        import { Injectable } from '@nestjs/common'
        import { FileRepository } from './file.repository'

        @Injectable()
        export class FilesService {
          constructor(private readonly repo: FileRepository) {}

          upload(name: string) { return this.repo.create(name) }
        }
    ''',
    "src/files.module.ts": '''
        import { Module } from '@nestjs/common'
        import { FileRepository } from './file.repository'
        import { RelationalFileRepository } from './relational.repository'
        import { FilesService } from './files.service'

        @Module({ providers: [FilesService, { provide: FileRepository, useClass: RelationalFileRepository }] })
        export class FilesModule {}
    ''',
    "src/memory.module.ts": '''
        import { Module } from '@nestjs/common'
        import { FileRepository } from './file.repository'
        import { MemoryFileRepository } from './memory.repository'

        @Module({ providers: [{ provide: FileRepository, useClass: MemoryFileRepository }] })
        export class MemoryModule {}
    ''',
}


def build(tmp: Path, files: dict, name: str):
    root = tmp / name
    for rel, src in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src).lstrip("\n"))
    db = tmp / f"{name}.db"
    index_project(root, db, name)
    return GraphStore(str(db)), db


def cli(*args):
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *args], cwd=ROOT, capture_output=True, text=True).stdout


def edges(st, kind):
    return {(r["src"], r["dst"]): (r["confidence"], json.loads(r["attrs"] or "{}"))
            for r in st.q("SELECT src, dst, confidence, attrs FROM edges WHERE kind=?", (kind,))}


F = "src/feed.ts#"


@needs_ts
def test_ts_interface_members_and_implementations(tmp_path):
    st, db = build(tmp_path, TS, "ts")
    m = {r["id"]: json.loads(r["attrs"] or "{}") for r in st.q("SELECT id, attrs FROM nodes WHERE kind='method'")}
    # method signatures, function-typed properties of an implemented interface; not those of an unimplemented one
    for x in ("Base.peek", "FeedAPI.fetch", "FeedAPI.label"):
        assert m[f"method:{F}{x}"].get("signature") is True
    assert f"method:{F}Unused.onClick" not in m
    calls = edges(st, "CALLS")
    assert (f"function:{F}load", f"method:{F}FeedAPI.fetch") in calls
    assert (f"function:{F}first", f"method:{F}Base.peek") in calls
    impl = edges(st, "IMPLEMENTED_BY")
    # explicit `implements` (members of the extended interface too), an arrow-function property
    for i, c in (("FeedAPI.fetch", "Following.fetch"), ("Base.peek", "Following.peek"), ("FeedAPI.label", "Following.label")):
        assert impl[(f"method:{F}{i}", f"method:{F}{c}")][0] == "exact"
    # structural: `return new Merge()` from a function returning FeedAPI
    conf, a = impl[(f"method:{F}FeedAPI.fetch", f"method:{F}Merge.fetch")]
    assert conf == "resolved" and a["via"] == ["structural"] and a["at"].startswith("src/feed.ts:")
    # Sub.peek overrides Following.peek, which implements Base.peek: no second edge from the interface
    assert (f"method:{F}Base.peek", f"method:{F}Sub.peek") not in impl
    # impact on an implementation: the call through the interface, via base
    imp = Q.impact(st, "Merge.fetch")
    assert [o["fqn"] for o in imp["overrides"]] == ["FeedAPI.fetch"]
    assert {c["id"]: c.get("via_base") for c in imp["callers"]} == {f"function:{F}load": "FeedAPI.fetch"}
    out = cli("impact", "Following.label", "--db", str(db))
    assert "overrides: FeedAPI.label" in out and "first  (via base FeedAPI.label)" in out


@needs_ts
def test_ts_inherited_spec_narrowed_by_receiver(tmp_path):
    st, db = build(tmp_path, TS, "ts")
    calls = edges(st, "CALLS")
    assert calls[(f"function:{F}useSub2", f"method:{F}Following.fetch")][1]["recv"] == [f"class:{F}Sub2"]
    assert "recv" not in calls[(f"function:{F}useFollowing", f"method:{F}Following.fetch")][1]
    imp = Q.impact(st, "Sub.fetch")
    assert imp["targets"] == [f"method:{F}Following.fetch"]
    # Sub2 is a sibling: left out; Sub | Sub2 can be a Sub; a Following-typed value can hold one; Mixed extends a
    # mixin call, so the graph does not know its ancestry: kept
    assert {c["id"] for c in imp["callers"]} == {f"function:{F}useSub", f"function:{F}useEither", f"function:{F}useMixed",
                                                 f"function:{F}useFollowing", f"function:{F}load"}
    assert imp["inherited"][0]["narrowed"] == {"calls": 5, "receiver_typed": 4, "dropped": 1}
    out = cli("impact", "Sub.fetch", "--db", str(db))
    assert "Sub.fetch -> inherited from Following.fetch (callers narrowed to Sub: 1 of 5 calls on other classes left out)" in out
    # the plain definition is not narrowed
    assert len(Q.impact(st, "Following.fetch")["callers"]) == 6


def test_python_inherited_spec_narrowed_by_receiver(tmp_path):
    st, db = build(tmp_path, PY, "py")
    calls = edges(st, "CALLS")
    run = "method:pkg.core.Base.run"
    assert calls[("function:pkg.core.use_a", run)][1]["recv"] == ["class:pkg.core.A"]
    assert calls[("function:pkg.core.use_mixed", run)][1] == {"via": "collection", "recv": ["class:pkg.core.A", "class:pkg.core.C"]}
    assert "recv" not in calls[("function:pkg.core.use_base", run)][1]       # annotated with the defining class
    imp = Q.impact(st, "B.run")
    # A instances are left out; C is a B; Base-typed and mixed collections can hold one
    assert {c["id"] for c in imp["callers"]} == {"function:pkg.core.use_b", "function:pkg.core.use_c",
                                                 "function:pkg.core.use_base", "function:pkg.core.use_mixed",
                                                 "function:pkg.core.entry"}
    assert {c["id"] for c in Q.impact(st, "A.run")["callers"]} == {
        "function:pkg.core.use_a", "function:pkg.core.use_base", "function:pkg.core.use_list",
        "function:pkg.core.use_mixed", "function:pkg.core.entry"}
    # reaches narrows the same way; `entry` still reaches it through use_b
    deps = {i["id"] for i in Q.reaches(st, ["B.run"], gate=None)["items"] if not i["is_target"]}
    assert "function:pkg.core.use_a" not in deps and "function:pkg.core.use_list" not in deps
    assert "function:pkg.core.entry" in deps
    out = cli("impact", "B.run", "--db", str(db))
    assert "B.run -> inherited from Base.run (callers narrowed to B: 2 of 6 calls on other classes left out)" in out


@needs_ts
def test_nest_binding_is_a_dispatch_hop(tmp_path):
    st, db = build(tmp_path, NEST, "nest")
    R, A = "method:src/relational.repository.ts#RelationalFileRepository.create", "method:src/file.repository.ts#FileRepository.create"
    M = "method:src/memory.repository.ts#MemoryFileRepository.create"
    assert (A, R) in edges(st, "BOUND_TO") and (A, R) in edges(st, "IMPLEMENTED_BY")
    assert (A, M) in edges(st, "BOUND_TO") and (A, M) not in edges(st, "IMPLEMENTED_BY")
    for impl in (R, M):
        imp = Q.impact(st, impl)
        # the abstract method is the relation (once), not a d=1 caller; the service calling it is
        assert [(o["fqn"], o["edge"]) for o in imp["overrides"]] == [("FileRepository.create",
                                                                     "IMPLEMENTED_BY" if impl == R else "BOUND_TO")]
        assert A not in {c["id"] for c in imp["callers"]}
        assert "method:src/files.service.ts#FilesService.upload" in {c["id"] for c in imp["callers"]}
    base = Q.impact(st, "FileRepository.create")
    assert sorted(o["fqn"] for o in base["overridden_by"]) == ["MemoryFileRepository.create", "RelationalFileRepository.create"]
    out = cli("impact", "MemoryFileRepository.create", "--db", str(db))
    assert "overrides: FileRepository.create" in out and "d=1 [src] FileRepository.create" not in out
