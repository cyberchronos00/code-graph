"""Tests that run the project's programs in a subprocess link to the entry point (issue #52)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cg_code_graph import query as Q  # noqa: E402
from test_python_tests import build, edges  # noqa: E402

PROJECT = {
    "pyproject.toml": '''
        [project]
        name = "tool"
        [project.scripts]
        mytool = "pkg.cli:main"
        ''',
    "pkg/__init__.py": "",
    "pkg/core.py": '''
        def sync(path):
            return walk(path)

        def walk(path):
            return [path]

        def report():
            return "ok"
        ''',
    "pkg/cli.py": '''
        import sys
        from pkg.core import sync, report

        def main(argv=None):
            args = sys.argv[1:] if argv is None else argv
            if args and args[0] == "sync":
                return sync(args[1])
            return report()

        if __name__ == "__main__":
            raise SystemExit(main())
        ''',
    "pkg/__main__.py": '''
        from pkg.cli import main
        main()
        ''',
    "scripts/migrate.py": '''
        from pkg.core import walk

        if __name__ == "__main__":
            walk(".")
        ''',
    "tests/helpers.py": '''
        import subprocess
        import sys

        def run_cli(*args, **kw):
            return subprocess.run([sys.executable, "-m", "pkg.cli", *args], capture_output=True, **kw)

        def run_module(module, *args):
            cmd = [sys.executable, "-X", "dev", "-m", module]
            return subprocess.run(cmd + list(args), check=False)

        def run_tool(*args):
            return runner_inner(["mytool", *args])

        def runner_inner(argv):
            return subprocess.check_output(argv)
        ''',
    "tests/test_cli.py": '''
        import asyncio
        import os
        import shutil
        import subprocess
        import sys
        from pathlib import Path

        from tests.helpers import run_cli, run_module, run_tool

        HERE = Path(__file__).parent
        PY = sys.executable

        def test_dash_m(tmp_path):
            r = subprocess.run([sys.executable, "-m", "pkg.cli", "sync", str(tmp_path)], capture_output=True)
            assert r.returncode == 0

        def test_dash_m_package():
            subprocess.check_call([PY, "-mpkg"])

        def test_dash_c():
            subprocess.run([sys.executable, "-c", "from pkg.core import sync; sync('.')"], check=True)

        def test_dash_c_module_import():
            subprocess.run(["python3", "-c", "import pkg.core as c; c.report()"], check=True)

        def test_script_path():
            subprocess.run([sys.executable, str(HERE.parent / "scripts" / "migrate.py")], check=True)

        def test_console_script():
            subprocess.run(["mytool", "sync", "."], check=True)

        def test_console_script_which():
            subprocess.run([shutil.which("mytool"), "sync", "."], check=True)

        def test_shell_string():
            os.system("python -m pkg.cli sync .")

        async def test_async_exec():
            await asyncio.create_subprocess_exec(sys.executable, "-m", "pkg.cli", "sync", ".")

        def test_helper():
            assert run_cli("sync", ".").returncode == 0

        def test_param_helper():
            run_module("pkg.cli", "sync", ".")

        def test_two_hop_helper():
            run_tool("sync", ".")

        def test_not_ours():
            subprocess.run(["git", "status"], check=True)
            subprocess.run([sys.executable, "-m", "pip", "install", "x"], check=True)
            subprocess.run([sys.executable, "-c", "import json; json.dumps(1)"], check=True)
            run_module("venv", "/tmp/x")
        ''',
}


def names(res, key):
    return sorted(t["name"] for t in res[key])


def test_subprocess_runs_link_to_entry_points(tmp_path):
    st = build(tmp_path, "subp", PROJECT)
    sub = st.stats["plugins"]["python"]["subprocess"] if "plugins" in st.stats else None
    calls = edges(st, "TEST_CALLS")
    via = [(s, d, a) for s, d, a in calls if a.get("via") == "subprocess"]
    by_test = {}
    for s, d, a in via:
        by_test.setdefault(s.rsplit(".", 1)[-1], set()).add(d)
    assert by_test["test_dash_m"] == {"script:pkg.cli"}
    assert by_test["test_dash_m_package"] == {"script:pkg.__main__"}
    assert by_test["test_dash_c"] == {"function:pkg.core.sync"}
    assert by_test["test_dash_c_module_import"] == {"function:pkg.core.report"}
    assert by_test["test_script_path"] == {"script:scripts.migrate"} or by_test["test_script_path"] == {"script:migrate"}
    assert by_test["test_console_script"] == {"script:console_scripts:mytool"}
    assert by_test["test_console_script_which"] == {"script:console_scripts:mytool"}
    assert by_test["test_shell_string"] == {"script:pkg.cli"}
    assert by_test["test_async_exec"] == {"script:pkg.cli"}
    assert by_test["test_param_helper"] == {"script:pkg.cli"}                  # module bound at the call site
    # the program is fixed inside run_tool (passed on to runner_inner): run_tool runs it, its callers reach it
    assert "test_two_hop_helper" not in by_test and by_test["run_tool"] == {"script:console_scripts:mytool"}
    assert by_test["run_cli"] == {"script:pkg.cli"}
    assert "test_not_ours" not in by_test                                       # git, -m pip, json, -m venv
    assert set(by_test) == {"test_dash_m", "test_dash_m_package", "test_dash_c", "test_dash_c_module_import",
                            "test_script_path", "test_console_script", "test_console_script_which", "test_shell_string",
                            "test_async_exec", "test_param_helper", "run_tool", "run_cli"}
    print(sub)
    assert sub["linked"] == 12 and sub["runners"] == 2, sub
    a = next(a for s, d, a in via if s.endswith("test_dash_m"))
    assert a["how"] == "-m" and a["command"].startswith("python -m pkg.cli")
    a = next(a for s, d, a in via if s.endswith("test_param_helper"))
    assert a["helper"] == "run_module"
    # cg tests: each subprocess test reaches the code the CLI runs
    res = Q.tests_covering(st, "pkg.core.walk")
    trans = set(names(res, "transitive")) | set(names(res, "direct"))
    for t in ("test_dash_m", "test_dash_m_package", "test_dash_c", "test_script_path", "test_console_script",
              "test_console_script_which", "test_shell_string", "test_async_exec", "test_helper", "test_param_helper",
              "test_two_hop_helper"):
        assert t in trans, (t, sorted(trans))
    assert "test_not_ours" not in trans and "test_dash_c_module_import" not in trans
    assert sub["outside_project"] + sub.get("code_without_project_calls", 0) == 4, sub


CLICK = {
    "app/__init__.py": "",
    "app/work.py": '''
        def crunch(n):
            return n * 2
        ''',
    "app/cli.py": '''
        import click
        import typer
        from app.work import crunch

        @click.group()
        def cli():
            pass

        @cli.command()
        @click.argument("n", type=int)
        def run(n):
            click.echo(crunch(n))

        tapp = typer.Typer()

        @tapp.command()
        def go(n: int):
            print(crunch(n))
        ''',
    "tests/test_click.py": '''
        from click.testing import CliRunner
        from typer.testing import CliRunner as TyperRunner
        from app.cli import cli, run, tapp

        def test_click_group():
            assert CliRunner().invoke(cli, ["run", "2"]).exit_code == 0

        def test_click_command():
            runner = CliRunner()
            assert runner.invoke(run, ["2"]).output == "4\\n"

        def test_typer():
            assert TyperRunner().invoke(tapp, ["2"]).exit_code == 0
        ''',
}


def test_click_and_typer_runners_reach_the_command(tmp_path):
    st = build(tmp_path, "clk", CLICK)
    res = Q.tests_covering(st, "app.work.crunch")
    got = set(names(res, "transitive")) | set(names(res, "direct"))
    assert {"test_click_command", "test_click_group", "test_typer"} <= got, (got, res)


def test_bare_script_names_match_only_the_project_root(tmp_path):
    files = {
        "manage.py": "if __name__ == '__main__':\n    print(1)\n",
        "sample/manage.py": "if __name__ == '__main__':\n    print(2)\n",
        "sample/tools/gen.py": "if __name__ == '__main__':\n    print(3)\n",
        "tests/test_scripts.py": '''
            import subprocess, sys

            def test_root_manage():
                subprocess.run([sys.executable, "manage.py", "check"])

            def test_tmp_manage(tmp_path):
                subprocess.run([sys.executable, "./run.py"], cwd=tmp_path)

            def test_nested():
                subprocess.run([sys.executable, "tools/gen.py"])
            ''',
    }
    st = build(tmp_path, "bare", files)
    got = {(s.rsplit(".", 1)[-1], d) for s, d, a in edges(st, "TEST_CALLS") if a.get("via") == "subprocess"}
    assert got == {("test_root_manage", "script:manage"), ("test_nested", "script:tools.gen")}, got


# ---------------------------------------------------------------- #60: step-by-step argv, runners, imports, templates
MORE = {
    "pyproject.toml": '''
        [project]
        name = "tool"
        dependencies = ["pytest", "scripttest", "sh", "plumbum"]
        [project.scripts]
        mytool = "pkg.cli:main"
        ''',
    "pkg/__init__.py": "",
    "pkg/cli.py": '''
        def main():
            return 0

        if __name__ == "__main__":
            main()
        ''',
    "pkg/plugin.py": '''
        REGISTRY = {}
        REGISTRY["x"] = 1
        ''',
    "conf/manage.py-tpl": '''
        import sys

        def main():
            from pkg.cli import main as run
            run()

        if __name__ == "__main__":
            main()
        ''',
    "tests/test_more.py": '''
        import shutil
        import subprocess
        import sys
        from pathlib import Path

        import sh
        from plumbum import local
        from scripttest import TestFileEnvironment

        HERE = Path(__file__).parent

        def test_appended():
            cmd = [sys.executable]
            cmd.append("-m")
            cmd += ["pkg.cli"]
            cmd.extend(["--verbose"])
            subprocess.run(cmd, check=True)

        def test_loop_built():
            cmd = []
            for part in ["mytool", "sync"]:
                cmd.append(part)
            subprocess.run(cmd)

        def test_insert_front():
            cmd = ["-m", "pkg.cli"]
            cmd.insert(0, sys.executable)
            subprocess.run(cmd)

        def test_config_value(config):
            cmd = config["command"]
            cmd.append("--x")
            subprocess.run(cmd)

        def test_scripttest(tmp_path):
            env = TestFileEnvironment(str(tmp_path))
            env.run("mytool", "sync")

        def test_pytester(pytester):
            pytester.run(sys.executable, "-m", "pkg.cli")

        def test_sh():
            sh.mytool("sync")

        def test_sh_command():
            sh.Command("mytool")("sync")

        def test_plumbum():
            local["python"]["-m", "pkg.cli"]()

        def test_import_only():
            subprocess.run([sys.executable, "-c", "import pkg.plugin"], check=True)

        def test_copied_template(tmp_path):
            shutil.copyfile(HERE.parent / "conf" / "manage.py-tpl", tmp_path / "manage.py")
            subprocess.run([sys.executable, str(tmp_path / "manage.py"), "check"])

        def test_copied_module(tmp_path):
            (tmp_path / "run.py").write_text((HERE.parent / "pkg" / "cli.py").read_text())
            subprocess.run([sys.executable, str(tmp_path / "run.py")])
        ''',
    "tests/support.py": '''
        from scripttest import TestFileEnvironment

        class Env(TestFileEnvironment):
            def tool(self, *args):
                return self.run("mytool", *args)
        ''',
    "tests/test_support.py": '''
        from tests.support import Env

        def test_env_helper(tmp_path):
            Env(str(tmp_path)).tool("sync")
        ''',
}


def test_step_by_step_argv_runners_imports_and_templates(tmp_path):
    st = build(tmp_path, "more", MORE)
    sub = st.stats["plugins"]["python"]["subprocess"]
    by_test = {}
    for s, d, a in edges(st, "TEST_CALLS"):
        if a.get("via") == "subprocess":
            by_test.setdefault(s.rsplit(".", 1)[-1], set()).add((d, a["how"]))
    assert by_test["test_appended"] == {("script:pkg.cli", "-m")}
    assert by_test["test_loop_built"] == {("script:console_scripts:mytool", "console script")}
    assert by_test["test_insert_front"] == {("script:pkg.cli", "-m")}
    assert "test_config_value" not in by_test                     # a runtime config value: unknown
    assert by_test["test_scripttest"] == {("script:console_scripts:mytool", "console script")}
    assert by_test["test_pytester"] == {("script:pkg.cli", "-m")}
    assert by_test["test_sh"] == by_test["test_sh_command"] == {("script:console_scripts:mytool", "console script")}
    assert by_test["test_plumbum"] == {("script:pkg.cli", "-m")}
    assert by_test["test_import_only"] == {("module:pkg.plugin", "-c import")}
    assert by_test["test_copied_template"] == {("function:pkg.cli.main", "copied template")}
    assert by_test["test_copied_module"] == {("script:pkg.cli", "copied script")}
    # a project subclass of the installed runner: its method is the runner, the program fixed inside it
    assert by_test["tool"] == {("script:console_scripts:mytool", "console script")}
    assert sub["external_runner_calls"] >= 3 and sub["linked_copied_script"] == 2, sub
    a = next(a for s, d, a in edges(st, "TEST_CALLS") if s.endswith("test_copied_template") and a.get("via") == "subprocess")
    assert a["copied_from"] == "conf/manage.py-tpl"
