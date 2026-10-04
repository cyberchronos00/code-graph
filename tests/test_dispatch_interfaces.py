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


def test_override_lines_show_file_when_names_collide():
    """#62 item 6: two interfaces named FeedAPI implemented by one class print as two entries with their files."""
    from codegraph.query import override_lines
    res = {"overrides": [
        {"id": "method:src/lib/api/feed/types.ts#FeedAPI.peekLatest", "fqn": "FeedAPI.peekLatest", "of": "x", "edge": "IMPLEMENTED_BY"},
        {"id": "method:src/state/feed/types.ts#FeedAPI.peekLatest", "fqn": "FeedAPI.peekLatest", "of": "x", "edge": "IMPLEMENTED_BY"},
        {"id": "method:src/a.ts#Base.peekLatest", "fqn": "Base.peekLatest", "of": "x", "edge": "OVERRIDDEN_BY"},
        {"id": "method:src/a.ts#Base.peekLatest", "fqn": "Base.peekLatest", "of": "y", "edge": "BOUND_TO"}]}
    assert override_lines(res) == ["overrides: FeedAPI.peekLatest (src/lib/api/feed/types.ts), "
                                   "FeedAPI.peekLatest (src/state/feed/types.ts), Base.peekLatest"]


def test_kotlin_inherited_calls_carry_the_receiver(tmp_path):
    """#62 item 1: a Kotlin call that resolves to an ancestor's member records the receiver class (attrs.recv), so
    `impact A.shared` leaves out `b.shared()` on a sibling subclass."""
    pytest.importorskip("tree_sitter_kotlin")
    src = tmp_path / "proj" / "src" / "main" / "kotlin" / "p"
    src.mkdir(parents=True)
    (src / "M.kt").write_text(textwrap.dedent("""\
        package p

        open class Base {
            open fun run() {}
            fun shared() {}
        }

        class A : Base() {
            override fun run() {}
            fun own() { shared() }
        }

        class B : Base()

        fun useA(a: A) { a.shared() }

        fun useB(b: B) { b.shared() }
        """))
    db = tmp_path / "g.db"
    index_project(tmp_path / "proj", db, "kt")
    st = GraphStore(str(db))
    res = Q.impact(st, "p.A.shared")
    names = {c["fqn"] for c in res["callers"]}
    assert "p.useA" in names and "p.A.own" in names and "p.useB" not in names
    assert res["inherited"][0]["narrowed"]["dropped"] == 1


def test_swift_inherited_calls_carry_the_receiver(tmp_path):
    """#62 item 1: the same for Swift (`a.shared()` on an A, `b.shared()` on a B, both defined in Base)."""
    pytest.importorskip("tree_sitter_swift")
    src = tmp_path / "proj" / "Sources" / "P"
    src.mkdir(parents=True)
    (tmp_path / "proj" / "Package.swift").write_text(
        '// swift-tools-version:5.9\nimport PackageDescription\nlet package = Package(name: "P", targets: [.target(name: "P")])\n')
    (src / "M.swift").write_text(textwrap.dedent("""\
        class Base {
            func run() {}
            func shared() {}
        }

        class A: Base {
            override func run() {}
        }

        class B: Base {}

        func useA(a: A) {
            a.shared()
        }

        func useB(b: B) {
            b.shared()
        }
        """))
    db = tmp_path / "g.db"
    index_project(tmp_path / "proj", db, "sw")
    res = Q.impact(GraphStore(str(db)), "A.shared")
    names = {c["fqn"] for c in res["callers"]}
    assert "useA" in names and "useB" not in names


def test_dart_inherited_calls_carry_the_receiver(tmp_path):
    """#62 item 1: the same for Dart (typed parameter receivers and implicit `this` in a subclass)."""
    from codegraph.plugins.dart.plugin import find_dart
    if find_dart() is None:
        pytest.skip("Dart SDK not found")
    (tmp_path / "proj" / "lib").mkdir(parents=True)
    (tmp_path / "proj" / "pubspec.yaml").write_text('name: dt\nenvironment:\n  sdk: ">=3.0.0 <4.0.0"\n')
    (tmp_path / "proj" / "lib" / "m.dart").write_text(textwrap.dedent("""\
        class Base {
          void run() {}
          void shared() {}
        }

        class A extends Base {
          @override
          void run() {}
          void own() {
            shared();
          }
        }

        class B extends Base {}

        void useA(A a) {
          a.shared();
        }

        void useB(B b) {
          b.shared();
        }
        """))
    db = tmp_path / "g.db"
    index_project(tmp_path / "proj", db, "dt")
    res = Q.impact(GraphStore(str(db)), "A.shared")
    names = {c["fqn"] for c in res["callers"]}
    assert {"lib/m.dart#A.own", "lib/m.dart#useA"} <= names and "lib/m.dart#useB" not in names


def test_php_inherited_calls_carry_the_receiver(tmp_path):
    """#62 item 1: the same for PHP (typed parameters)."""
    (tmp_path / "proj" / "app").mkdir(parents=True)
    (tmp_path / "proj" / "composer.json").write_text('{"name": "x/p", "autoload": {"psr-4": {"App\\\\": "app/"}}}')
    (tmp_path / "proj" / "app" / "M.php").write_text(textwrap.dedent("""\
        <?php
        namespace App;

        class Base { public function run() {} public function shared() {} }
        class A extends Base { public function run() {} }
        class B extends Base {}

        class Client {
            public function useA(A $a) { $a->shared(); }
            public function useB(B $b) { $b->shared(); }
        }
        """))
    db = tmp_path / "g.db"
    index_project(tmp_path / "proj", db, "php")
    res = Q.impact(GraphStore(str(db)), "A::shared")
    names = {c["fqn"] for c in res["callers"]}
    assert "App\\Client::useA" in names and "App\\Client::useB" not in names


TS_OBJ = {
    "tsconfig.json": '{"compilerOptions": {"strict": true, "target": "es2020"}, "include": ["src"]}',
    "package.json": '{"name": "feeds", "devDependencies": {"typescript": "5"}}',
    "src/api.ts": '''
        export interface FeedAPI {
          fetch(cursor: string): Promise<string[]>
          label: (x: number) => string
        }
        export interface Other {
          fetch(cursor: string): Promise<string[]>
        }
        export const discover: FeedAPI = {
          async fetch(cursor) { return [cursor] },
          label: x => String(x),
        }
        export function makeFeed(n: number): FeedAPI {
          return {
            fetch: async c => [c, String(n)],
            label(x) { return `${x}` },
          }
        }
        export const sat = {
          async fetch(c: string) { return [c] },
          label: (x: number) => '',
        } satisfies FeedAPI
        export const either: FeedAPI | Other = { async fetch(c: string) { return [c] }, label: () => '' }
        export const loose = { async fetch(c: string) { return [c] } }
        export class Following {
          async fetch(cursor: string) { return [cursor] }
          label = (x: number) => ''
        }
        export function register(ctor: new () => FeedAPI) { return new ctor() }
        register(Following)
        export function load(api: FeedAPI) { return api.fetch('a') + api.label(1) }
    ''',
}


@needs_ts
def test_ts_object_literals_and_class_values_implement_interfaces(tmp_path):
    st, _ = build(tmp_path, TS_OBJ, "obj")
    A = "src/api.ts#"
    impl = edges(st, "IMPLEMENTED_BY")
    # a typed variable, a factory's return value, `satisfies`: the function members implement the interface's,
    # function-typed properties (`label`) included
    for obj in ("discover", "makeFeed", "sat"):
        for m in ("fetch", "label"):
            assert impl[(f"method:{A}FeedAPI.{m}", f"function:{A}{obj}.{m}")] == ("exact", {"via": ["object_literal"]})
    # a class passed where `new () => FeedAPI` is expected: structural
    conf, a = impl[(f"method:{A}FeedAPI.fetch", f"method:{A}Following.fetch")]
    assert conf == "resolved" and a == {"via": ["structural"], "at": "src/api.ts:29"}
    # a union of two interfaces, an untyped literal: nothing
    assert not [k for k in impl if "either." in k[1] or "loose." in k[1] or "Other." in k[0]]
    imp = Q.impact(st, "makeFeed.fetch")
    assert [o["fqn"] for o in imp["overrides"]] == ["FeedAPI.fetch"]
    assert [c["id"] for c in imp["callers"]] == [f"function:{A}load"]


TS_MIX = {
    "tsconfig.json": '{"compilerOptions": {"strict": true, "target": "es2020"}, "include": ["src"]}',
    "package.json": '{"name": "client", "devDependencies": {"typescript": "5"}}',
    "src/client.ts": '''
        type Constructor<T = {}> = new (...args: any[]) => T
        export class ClientBase {
          doFetch(url: string) { return url }
        }
        function mix<B extends Constructor>(base: B) {
          return { with: (...ms: Array<(b: any) => any>) => ms.reduce((c, m) => m(c), base) as B }
        }
        export interface UsersMix {
          getMe: () => string
          patchMe(name: string): string
        }
        const Users = <T extends Constructor<ClientBase>>(superclass: T) => class extends superclass {
          getMe = () => this.doFetch('/me')
          patchMe(name: string) { return this.doFetch(`/me/${name}`) }
        }
        export interface PostsMix {
          getPost: (id: string) => string
        }
        function Posts<T extends Constructor<ClientBase>>(superclass: T) {
          return class extends superclass {
            getPost = (id: string) => this.doFetch(`/posts/${id}`)
            helper() { return 1 }
          }
        }
        interface Client extends ClientBase, UsersMix, PostsMix {}
        class Client extends mix(ClientBase).with(Users, Posts) {}
        export function load(c: Client) { return c.getMe() + c.patchMe('x') + c.getPost('1') }
    ''',
}


@needs_ts
def test_ts_mixin_members_implement_the_merged_interface(tmp_path):
    st, _ = build(tmp_path, TS_MIX, "mix")
    A = "src/client.ts#"
    impl = edges(st, "IMPLEMENTED_BY")
    # `interface Client extends UsersMix, PostsMix` + `class Client extends mix(ClientBase).with(Users, Posts)`:
    # the mixin class members (arrow and block-bodied mixins) implement the interfaces' members
    for i, m in (("UsersMix.getMe", "Users.getMe"), ("UsersMix.patchMe", "Users.patchMe"), ("PostsMix.getPost", "Posts.getPost")):
        assert impl[(f"method:{A}{i}", f"method:{A}{m}")] == ("resolved", {"via": ["mixin"], "at": "src/client.ts:26"})
    assert not [k for k in impl if k[1].endswith(".helper")]
    calls = edges(st, "CALLS")
    assert (f"function:{A}load", f"method:{A}UsersMix.getMe") in calls
    imp = Q.impact(st, "Users.getMe")
    assert [c["id"] for c in imp["callers"]] == [f"function:{A}load"]


PY_FLOW = {
    "pkg/__init__.py": "",
    "pkg/core.py": '''
        class Base:
            def run(self):
                return 0


        class A(Base):
            pass


        class B(Base):
            pass


        class C(Base):
            def run(self):
                return 2


        class Holder:
            def __init__(self):
                self.x = B()

            def go(self):
                return self.x.run()


        def make(flag):
            if flag:
                return None
            return B()


        def make_either(flag):
            return A() if flag else B()


        def gen():
            yield B()


        def use_factory():
            v = make(True)
            return v.run()


        def use_either():
            return make_either(1).run()


        def use_a():
            return A().run()


        def use_base(x: Base):
            return x.run()
    ''',
}


def test_python_receivers_through_dataflow_and_override_narrowing(tmp_path):
    st, db = build(tmp_path, PY_FLOW, "flow")
    P = "pkg.core."
    calls = edges(st, "CALLS")
    # `self.x = B()` read in another method; an unannotated factory whose returns are all B (None aside)
    assert calls[(f"method:{P}Holder.go", f"method:{P}Base.run")][1]["recv"] == [f"class:{P}B"]
    assert calls[(f"function:{P}use_factory", f"method:{P}Base.run")][1]["recv"] == [f"class:{P}B"]
    # returns of two classes (`A() if flag else B()`): no type, so no receiver either
    assert (f"function:{P}use_either", f"method:{P}Base.run") not in calls
    imp = Q.impact(st, "A.run")
    assert {c["id"] for c in imp["callers"]} == {f"function:{P}use_a", f"function:{P}use_base"}
    # an override: the calls into the base method on receivers that cannot be a C are left out too
    imp = Q.impact(st, "C.run")
    assert {c["id"] for c in imp["callers"]} == {f"function:{P}use_base"}
    assert imp["override_narrowed"][0]["narrowed"] == {"calls": 4, "receiver_typed": 3, "dropped": 3}
    out = cli("impact", "C.run", "--db", str(db))
    assert "C.run overrides Base.run (its callers narrowed to C: 3 of 4 calls on other classes left out)" in out
    assert Q.reaches(st, ["C.run"])["override_narrowed"][0]["narrowed"]["dropped"] == 3


TS_PROPS = {
    "tsconfig.json": '{"compilerOptions": {"strict": true, "target": "es2020", "jsx": "react-jsx"}, "include": ["src"]}',
    "package.json": '{"name": "ui", "devDependencies": {"typescript": "5"}}',
    "src/ui.tsx": '''
        export interface ButtonProps {
          label: string
          onPress: () => void
        }
        export function Button({label, onPress}: ButtonProps) {
          return <button onClick={() => onPress()}>{label}</button>
        }
        export function save() { return 1 }
        export function Screen() {
          return <Button label="Save" onPress={save} />
        }
    ''',
}


@needs_ts
def test_ts_jsx_callback_props_stay_untargeted(tmp_path):
    # #96 item 3: a callback passed as a JSX attribute does not implement the props interface's property, so the
    # property is no member node and `onPress()` has no target (left out on purpose: see docs/limitations.md)
    st, _ = build(tmp_path, TS_PROPS, "props")
    assert not st.q("SELECT id FROM nodes WHERE id LIKE '%ButtonProps.onPress%'")
    assert not edges(st, "IMPLEMENTED_BY")
    calls = edges(st, "CALLS")
    assert [k for k in calls if k[1].endswith("#save")] == [("function:src/ui.tsx#Screen", "function:src/ui.tsx#save")]
    assert calls[("function:src/ui.tsx#Screen", "function:src/ui.tsx#save")][1] == {"ref": True}
