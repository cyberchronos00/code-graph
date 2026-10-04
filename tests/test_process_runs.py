"""Tests that run the project's programs in a subprocess, outside Python (issue #60)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_python_tests import build, edges  # noqa: E402

RUST = {
    "Cargo.toml": '''
        [package]
        name = "kv"
        version = "0.1.0"
        edition = "2021"

        [[bin]]
        name = "kvtool"
        path = "src/bin/kvtool.rs"
        ''',
    "src/main.rs": '''
        fn main() {
            run();
        }

        fn run() {}
        ''',
    "src/bin/kvtool.rs": '''
        fn main() {
            println!("tool");
        }
        ''',
    "tests/cli.rs": '''
        use std::process::Command;

        #[test]
        fn runs_tool() {
            let out = Command::new(env!("CARGO_BIN_EXE_kvtool")).arg("x").output().unwrap();
            assert!(out.status.success());
        }

        #[test]
        fn runs_main_with_assert_cmd() {
            let mut cmd = assert_cmd::Command::cargo_bin("kv").unwrap();
            cmd.assert().success();
        }

        #[test]
        fn runs_pkg_bin() {
            assert_cmd::Command::cargo_bin(env!("CARGO_PKG_NAME")).unwrap().assert().success();
        }

        #[test]
        fn runs_git() {
            Command::new("git").arg("status").output().unwrap();
        }
        ''',
}

NODE = {
    "tsconfig.json": '{"compilerOptions": {"allowJs": true, "checkJs": false}, "include": ["src", "test", "tools"]}',
    "package.json": '''
        {"name": "@acme/tool", "version": "1.0.0", "bin": {"acme": "src/cli.js"}, "devDependencies": {"jest": "29", "execa": "8"}}
        ''',
    "src/cli.js": '''
        const { run } = require("./run");

        function main(argv) {
          return run(argv);
        }

        main(process.argv.slice(2));
        ''',
    "src/run.js": '''
        function run(args) { return args.length; }
        module.exports = { run };
        ''',
    "src/build.js": '''
        export function build() { return 1; }
        build();
        ''',
    "tools/gen.js": "console.log(1);\n",
    "test/tools/gen.js": '''
        function helper() { return 1; }
        module.exports = { helper };
        ''',
    "test/cli.test.js": '''
        const { spawnSync, execSync, fork } = require("child_process");
        const path = require("path");
        const { execa } = require("execa");

        test("spawn node", () => {
          const r = spawnSync("node", [path.join(__dirname, "..", "src", "cli.js"), "x"]);
          expect(r.status).toBe(0);
        });

        test("exec string", () => {
          execSync("node src/build.js --fast");
        });

        test("fork", () => {
          fork(path.resolve(__dirname, "../src/cli.js"));
        });

        test("execa bin", async () => {
          await execa("acme", ["x"]);
        });

        test("exec path", () => {
          spawnSync(process.execPath, ["src/cli.js"]);
        });

        test("not ours", () => {
          execSync("git status");
        });

        test("top-level script without a node", () => {
          spawnSync("node", [path.join(__dirname, "..", "tools", "gen.js")]);
        });
        ''',
}

DART = {
    "pubspec.yaml": '''
        name: tool
        environment:
          sdk: ">=3.0.0 <4.0.0"
        dev_dependencies:
          test: any
        ''',
    "bin/tool.dart": '''
        import 'package:tool/run.dart';

        void main(List<String> args) {
          run(args);
        }
        ''',
    "lib/run.dart": '''
        int run(List<String> args) => args.length;
        ''',
    "test/cli_test.dart": '''
        import 'dart:io';
        import 'package:test/test.dart';

        void main() {
          test('runs the tool', () async {
            final r = await Process.run('dart', ['run', 'bin/tool.dart', 'x']);
            expect(r.exitCode, 0);
          });
          test('git', () async {
            await Process.run('git', ['status']);
          });
        }
        ''',
}


def via_sub(st):
    out = {}
    # Rust #[test] fns and Dart test files are functions of their own (entry_kind test): CALLS; others TEST_CALLS
    for s, d, a in edges(st, "TEST_CALLS") + edges(st, "CALLS"):
        if a.get("via") == "subprocess":
            out.setdefault(s, set()).add((d, a["how"]))
    return out


def test_rust_cargo_bins(tmp_path):
    st = build(tmp_path, "rs", RUST)
    got = {s.rsplit("::", 1)[-1]: v for s, v in via_sub(st).items()}
    assert got["runs_tool"] == {("function:kvtool::main", "cargo bin")}, got
    assert got["runs_main_with_assert_cmd"] == {("function:kv::main", "cargo bin")}, got
    assert got["runs_pkg_bin"] == {("function:kv::main", "cargo bin")}, got
    assert "runs_git" not in got


def test_node_child_process_and_execa(tmp_path):
    st = build(tmp_path, "js", NODE)
    got = via_sub(st)
    dsts = sorted({d for v in got.values() for d, _h in v})
    # ../tools/gen.js from test/ is tools/gen.js (outside the source dirs: a source file because a test runs it,
    # #106), never test/tools/gen.js
    assert dsts == ["module:src/build.js", "module:src/cli.js", "module:tools/gen.js"], got
    hows = sorted(h for v in got.values() for _d, h in v)
    assert hows.count("package bin") == 1 and len(hows) == 6, got
    assert st.stats["process_runs"]["linked"] == 6
    assert "script_without_node" not in st.stats["process_runs"], st.stats["process_runs"]


def test_dart_process_run(tmp_path):
    st = build(tmp_path, "dart", DART)
    got = via_sub(st)
    assert {d for v in got.values() for d, _h in v} == {"function:bin/tool.dart#main"}, got
    assert sum(len(v) for v in got.values()) == 1


LARAVEL = {
    "composer.json": '{"require": {"laravel/framework": "^11.0"}, "autoload": {"psr-4": {"App\\\\": "app/"}}}',
    "artisan": "",
    "app/Console/Commands/PruneOrders.php": """
        <?php
        namespace App\\Console\\Commands;
        use Illuminate\\Console\\Command;
        class PruneOrders extends Command {
            protected $signature = 'orders:prune {--days=30}';
            public function handle(): int { return $this->prune(); }
            private function prune(): int { return 0; }
        }
        """,
    "tests/Feature/PruneOrdersTest.php": """
        <?php
        namespace Tests\\Feature;
        use Symfony\\Component\\Process\\Process;
        use Tests\\TestCase;
        class PruneOrdersTest extends TestCase {
            public function test_in_process(): void {
                $this->artisan('orders:prune --days=1')->assertExitCode(0);
            }
            public function test_subprocess(): void {
                $p = new Process(['php', 'artisan', 'orders:prune', '--days=2']);
                $p->run();
            }
            public function test_shell(): void {
                exec('php artisan orders:prune');
            }
            public function test_other(): void {
                exec('php artisan unknown:cmd');
            }
        }
        """,
}


def test_laravel_artisan_from_tests(tmp_path):
    from sample import needs_php
    needs_php()
    st = build(tmp_path, "lv", LARAVEL)
    got = {}
    for s, d, a in edges(st, "TEST_CALLS"):
        if d.startswith("command:"):
            got.setdefault(s.rsplit("::", 1)[-1].rsplit(".", 1)[-1], set()).add((d, a.get("via"), a.get("how")))
    cmd = next(iter(got.get("test_in_process", {("?",)})))[0]
    assert got["test_in_process"] == {(cmd, "$this->artisan", None)}, got
    assert got["test_subprocess"] == {(cmd, "subprocess", "artisan")}, got
    assert got["test_shell"] == {(cmd, "subprocess", "artisan")}, got
    assert not any(k.endswith("test_other") for k in got)


def test_dart_script_from_a_local_variable(tmp_path):
    files = dict(DART)
    files["test/snap_test.dart"] = '''
        import 'dart:io';
        import 'package:path/path.dart' as p;

        Future<void> compile() async {
          var scriptPath = p.normalize(p.join(Directory.current.path, '../bin/tool.dart'));
          await Process.run(Platform.resolvedExecutable, ['--snapshot=x.snapshot', scriptPath]);
        }
        '''
    st = build(tmp_path, "dart2", files)
    got = via_sub(st)
    assert got.get("function:test/snap_test.dart#compile") == {("function:bin/tool.dart#main", "dart script")}, got


PLAIN_JS = {
    # no tsconfig / jsconfig and no src/ or app/: package.json main / bin / files name the source dirs (#94)
    "package.json": '''
        {"name": "lintish", "version": "1.0.0", "main": "./lib/api.js", "bin": {"lintish": "./bin/lintish.js"},
         "files": ["bin", "lib", "index.js"], "devDependencies": {"typescript": "5", "mocha": "10"}}
        ''',
    "index.js": '''
        module.exports = require("./lib/api");
        ''',
    "lib/api.js": '''
        const { execute } = require("./cli");
        module.exports = { execute };
        ''',
    "lib/cli.js": '''
        function execute(args) { return args.length; }
        module.exports = { execute };
        ''',
    "bin/lintish.js": '''
        #!/usr/bin/env node
        "use strict";
        const cli = require("../lib/cli");
        (async function main() {
          process.exitCode = cli.execute(process.argv.slice(2));
        })();
        ''',
    "tests/bin/lintish.js": '''
        const childProcess = require("child_process");
        const path = require("path");
        const EXECUTABLE_PATH = path.resolve(path.join(__dirname, "../../bin/lintish.js"));
        function runLintish(args) {
          return childProcess.spawn(process.execPath, [EXECUTABLE_PATH, ...args]);
        }
        describe("bin/lintish.js", () => {
          it("exits", () => { runLintish(["--version"]); });
        });
        ''',
}

BIN_BESIDE_SRC = {
    "tsconfig.json": '{"compilerOptions": {"allowJs": true, "checkJs": false}, "include": ["src"]}',
    "package.json": '{"name": "tool", "version": "1.0.0", "bin": {"tool": "bin/tool.js"}, "devDependencies": {"jest": "29"}}',
    "src/run.js": '''
        function run(args) { return args.length; }
        module.exports = { run };
        ''',
    "bin/tool.js": '''
        #!/usr/bin/env node
        require("../src/run").run(process.argv.slice(2));
        ''',
    "test/tool.test.js": '''
        const { execFileSync } = require("child_process");
        test("tool", () => { execFileSync("node", ["bin/tool.js", "x"]); });
        ''',
}


def test_plain_js_package_without_config_indexes_its_package_dirs(tmp_path):
    st = build(tmp_path, "plainjs", PLAIN_JS)
    mods = {r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='module'")}
    assert {"module:lib/api.js", "module:lib/cli.js", "module:bin/lintish.js", "module:index.js"} <= mods, mods
    got = via_sub(st)
    assert got.get("function:tests/bin/lintish.js#runLintish") == {("module:bin/lintish.js", "node script")}, got
    assert "script_without_node" not in st.stats["process_runs"], st.stats["process_runs"]
    # the bin script's top-level code reaches the library: `cg tests` counts the test for lib/cli.js#execute
    calls = {d for _s, d, _a in edges(st, "CALLS", src="module:bin/lintish.js")}
    assert "function:lib/cli.js#execute" in calls, calls


def test_bin_script_outside_the_source_dirs_is_a_source_file(tmp_path):
    st = build(tmp_path, "binsrc", BIN_BESIDE_SRC)
    got = via_sub(st)
    assert got == {"test:test/tool.test.js#tool": {("module:bin/tool.js", "node script")}}, got
    calls = {d for _s, d, _a in edges(st, "CALLS", src="module:bin/tool.js")}
    assert "function:src/run.js#run" in calls, calls


SCRIPT_OUTSIDE = {
    "tsconfig.json": '{"compilerOptions": {"allowJs": true, "checkJs": false}, "include": ["src"]}',
    "package.json": '{"name": "app", "version": "1.0.0", "devDependencies": {"jest": "29"}}',
    "src/db.js": '''
        export function seedRows(n) { return n; }
        ''',
    "scripts/seed.mjs": '''
        import { seedRows } from "../src/db.js";
        export function seed() { return seedRows(3); }
        seed();
        ''',
    "scripts/unused.js": "console.log(1);\n",
    "test/seed.test.js": '''
        const { spawnSync, execSync } = require("child_process");
        const SEED = "scripts/seed.mjs";
        test("seed", () => { spawnSync(process.execPath, [SEED]); });
        test("seed again", () => { execSync("node ./scripts/seed.mjs --rows 3"); });
        ''',
}


def test_script_outside_the_source_dirs_that_a_test_runs_is_a_source_file(tmp_path):
    st = build(tmp_path, "scriptout", SCRIPT_OUTSIDE)
    mods = {r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='module'")}
    assert "module:scripts/seed.mjs" in mods and "module:scripts/unused.js" not in mods, mods
    got = via_sub(st)
    assert got == {"test:test/seed.test.js#seed": {("module:scripts/seed.mjs", "node script")},
                   "test:test/seed.test.js#seed again": {("module:scripts/seed.mjs", "node script")}}, got
    calls = {d for _s, d, _a in edges(st, "CALLS", src="function:scripts/seed.mjs#seed")}
    assert "function:src/db.js#seedRows" in calls, calls
