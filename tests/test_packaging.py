"""Packaging (#64): pyproject metadata, extractor dependencies in the user cache, `cg doctor` / `cg setup`, and the
`.cg.yaml` rust.targets setting."""
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from codegraph import __version__
from codegraph.config import ConfigError, load
from codegraph.core import extractors

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_metadata():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["name"] == "cg-code-graph"          # PyPI name; `codegraph` is taken there
    assert meta["project"]["scripts"]["cg"] == "codegraph.cli:main"
    wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
    assert "pypa/gh-action-pypi-publish" in wf and "id-token: write" in wf and "workflow_dispatch" in wf
    assert meta["project"]["dynamic"] == ["version"]
    assert meta["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "codegraph.__version__"}
    deps = " ".join(meta["project"]["dependencies"])
    for d in ("mcp", "pyyaml", "protobuf", "tree-sitter-rust", "tree-sitter-swift"):
        assert d in deps
    manifest = (ROOT / "MANIFEST.in").read_text()
    for d in ("ts/extractor/node_modules", "php/extractor/vendor", "dart/extractor/.bin", "dart/extractor/.dart_tool"):
        assert f"prune codegraph/plugins/{d}" in manifest
    # every extractor source listed for the cache copy exists in the package
    for spec in extractors.SPECS.values():
        for rel in spec.sources:
            assert (spec.pkg / rel).exists(), rel


def _fake_spec(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "extract.mjs").write_text("// v1\n")
    (pkg / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
    spec = extractors.Spec("typescript", pkg, ("extract.mjs", "fw.mjs", "package-lock.json"), "package-lock.json",
                           ("node_modules/typescript/package.json",), "npm", "TypeScript")
    monkeypatch.setitem(extractors.SPECS, "typescript", spec)
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    return pkg


def test_extractor_runs_from_user_cache_without_deps_in_package(tmp_path, monkeypatch):
    pkg = _fake_spec(tmp_path, monkeypatch)
    d = extractors.workdir("typescript")
    assert d.parent == tmp_path / "cache" / "extractors" and d.name.startswith("typescript-")
    assert (d / "extract.mjs").read_text() == "// v1\n" and not (d / "fw.mjs").exists()
    st = extractors.status("typescript")
    assert st == {"installed": False, "dir": str(d), "where": "cache"}
    # dependencies installed there: reused; a newer extractor source with the same lock file is synced into it
    (d / "node_modules" / "typescript").mkdir(parents=True)
    (d / "node_modules" / "typescript" / "package.json").write_text("{}")
    (pkg / "extract.mjs").write_text("// v2\n")
    assert extractors.ensure("typescript") == d          # no install command run: the marker is there
    assert (d / "extract.mjs").read_text() == "// v2\n"
    assert extractors.status("typescript")["installed"]
    # a new lock file: a fresh directory
    (pkg / "package-lock.json").write_text('{"lockfileVersion": 3, "x": 1}\n')
    assert extractors.workdir("typescript") != d
    # a checkout with the dependencies in the package directory keeps using it
    (pkg / "node_modules" / "typescript").mkdir(parents=True)
    (pkg / "node_modules" / "typescript" / "package.json").write_text("{}")
    assert extractors.workdir("typescript") == pkg and extractors.status("typescript")["where"] == "package"


def test_extractor_install_failure_names_the_tool(tmp_path, monkeypatch):
    _fake_spec(tmp_path, monkeypatch)
    monkeypatch.setattr(extractors, "install_command", lambda lang, dart=None: ["cg-no-such-npm", "ci"])
    with pytest.raises(RuntimeError, match="`npm` is not installed"):
        extractors.ensure("typescript")


def test_rust_targets_config(tmp_path, monkeypatch):
    from codegraph.plugins.rust.plugin import rust_targets_setting
    monkeypatch.delenv("CODEGRAPH_RUST_TARGETS", raising=False)
    for text, want in (("off", "off"), ("false", "off"), ("auto", "auto"), ("[windows, macos]", "windows,macos"),
                       ("x86_64-pc-windows-msvc", "x86_64-pc-windows-msvc")):
        (tmp_path / ".cg.yaml").write_text(f"rust:\n  targets: {text}\n")
        cfg = load(tmp_path)
        assert cfg["rust"]["targets"] == want

        class P:
            options = {"config": cfg}
        assert rust_targets_setting(P) == (want, ".cg.yaml rust.targets")
    monkeypatch.setenv("CODEGRAPH_RUST_TARGETS", "0")
    assert rust_targets_setting(P) == ("0", "CODEGRAPH_RUST_TARGETS")      # the environment wins
    monkeypatch.delenv("CODEGRAPH_RUST_TARGETS")
    assert rust_targets_setting(None) == ("auto", "default")
    (tmp_path / ".cg.yaml").write_text("rust:\n  targets: [1]\n")
    with pytest.raises(ConfigError, match="rust.targets"):
        load(tmp_path)


def test_doctor_report_and_cli(tmp_path, monkeypatch):
    from codegraph.doctor import render, report
    monkeypatch.delenv("CODEGRAPH_RUST_TARGETS", raising=False)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")
    (tmp_path / "app.py").write_text("print(1)\n")
    (tmp_path / ".cg.yaml").write_text("rust:\n  targets: off\n")
    r = report(tmp_path)
    assert r["cg"] == __version__ and set(r["extractors"]) == {"typescript", "php", "dart"}
    langs = {x["language"]: x for x in r["languages"]}
    assert set(langs) == {"python", "rust"}                     # only the project's languages
    assert langs["python"]["mode"] == "exact"
    rs = langs["rust"]
    assert rs["mode"] in ("exact", "heuristic", "unavailable") and rs["why"]
    if rs["mode"] == "exact":
        assert rs["targets"] == "off" and "rust.targets" in rs["targets_source"] and "per-target runs off" in rs["why"]
    else:
        assert rs.get("fix")
    txt = render(r)
    assert "languages in" in txt and "update:" in txt
    out = subprocess.run([sys.executable, "-m", "codegraph.cli", "doctor", str(tmp_path), "--json"], capture_output=True,
                         text=True, cwd=ROOT, check=True).stdout
    assert {x["language"] for x in json.loads(out)["languages"]} == {"python", "rust"}
    bad = subprocess.run([sys.executable, "-m", "codegraph.cli", "setup", "cobol"], capture_output=True, text=True, cwd=ROOT)
    assert bad.returncode == 2 and "unknown language" in bad.stderr


def test_doctor_mcp_tool(tmp_path):
    from codegraph import mcp_server
    (tmp_path / "a.py").write_text("x = 1\n")
    txt = mcp_server.doctor(str(tmp_path))
    assert "python" in txt and "exact" in txt


def test_install_script_syntax_and_dry_run(tmp_path):
    sh = ROOT / "install.sh"
    subprocess.run(["sh", "-n", str(sh)], check=True)
    r = subprocess.run(["sh", str(sh), "--dry-run", "--no-extractors", "--version", "v9.9.9", "--with", "c,rust"],
                       capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert "git+https://github.com/cyberchronos00/code-graph@v9.9.9" in r.stderr
    assert not any(l.startswith("cg-install: + sudo") for l in r.stderr.splitlines())   # never runs sudo
    # --with kotlin (#67): the checksum-verified scip-java 0.13.1 launcher next to the coursier 0.12 one
    r = subprocess.run(["sh", str(sh), "--dry-run", "--no-extractors", "--with", "kotlin"], capture_output=True, text=True,
                       env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert "releases/download/v0.13.1/scip-java-v0.13.1.sha256" in r.stderr and "scip-java-0.13.1" in r.stderr
    assert not (tmp_path / ".local" / "bin").exists()            # a dry run writes nothing
    ps = (ROOT / "install.ps1").read_text()
    assert "scip-java 0.13.x" in ps and ps.count("{") == ps.count("}")
    r = subprocess.run(["sh", str(sh), "--bogus"], capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode != 0 and "unknown option" in r.stderr


def test_setup_prune_and_install_lock(tmp_path, monkeypatch):
    """#65: `cg setup --prune` removes extractor installs this cg does not use; one whose install lock is held stays."""
    _fake_spec(tmp_path, monkeypatch)
    cur = extractors.workdir("typescript")
    root = cur.parent
    old, busy, other = root / "typescript-0123456789ab", root / "php-aaaaaaaaaaaa", root / "notes"
    for d in (old, busy, other):
        (d / "x").mkdir(parents=True)
        (d / "x" / "f").write_text("12345")
    assert {p.name for p, _ in extractors.prune(dry_run=True)} >= {old.name, busy.name} and old.exists()
    with extractors.install_lock(busy):
        gone = {p.name: n for p, n in extractors.prune()}
    assert old.name in gone and gone[old.name] >= 5 and busy.name not in gone
    assert not old.exists() and busy.exists() and other.exists() and cur.exists()
    # a second install waits for the lock and then finds the dependencies there: no second install command
    calls = []
    monkeypatch.setattr(extractors, "_run", lambda cmd, d, spec: calls.append(d) or
                        (d / "node_modules" / "typescript").mkdir(parents=True) or
                        (d / "node_modules" / "typescript" / "package.json").write_text("{}"))
    assert extractors.ensure("typescript") == cur and extractors.ensure("typescript") == cur and calls == [cur]
    out = subprocess.run([sys.executable, "-m", "codegraph.cli", "setup", "--prune", "--dry-run"], capture_output=True,
                         text=True, cwd=ROOT, env={**__import__("os").environ, "CODEGRAPH_CACHE": str(tmp_path / "cache")})
    assert out.returncode == 0, out.stderr


def test_doctor_project_checks(tmp_path):
    from codegraph.doctor import render, report
    (tmp_path / "packages" / "a").mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"workspaces": ["packages/*"]}')
    (tmp_path / "packages" / "a" / "package.json").write_text("{}")
    (tmp_path / "packages" / "a" / "tsconfig.json").write_text("{}")
    (tmp_path / "packages" / "a" / "i.ts").write_text("export const a = 1\n")
    (tmp_path / "build.gradle.kts").write_text('plugins { kotlin("jvm") version "2.1.0" }\n')
    (tmp_path / "M.kt").write_text("fun main() {}\n")
    (tmp_path / "ios" / "App.xcodeproj").mkdir(parents=True)
    (tmp_path / "ios" / "App.xcodeproj" / "project.pbxproj").write_text("// !$*UTF8*$!\n{}\n")
    (tmp_path / "ios" / "A.swift").write_text("let a = 1\n")
    pr = {x["language"]: x for x in report(tmp_path)["project"]}
    assert pr["typescript"]["ok"] and "1 package tsconfig" in pr["typescript"]["what"]
    assert pr["kotlin"]["ok"] and "Kotlin 2.1.0" in pr["kotlin"]["what"]
    assert not pr["swift"]["ok"] and "1 Xcode project(s): ios" in pr["swift"]["what"] and "INDEX_STORE" in pr["swift"]["fix"]
    txt = render(report(tmp_path))
    assert "project:" in txt and "fix: point CODEGRAPH_SWIFT_INDEX_STORE" in txt
    (tmp_path / "build.gradle.kts").unlink()
    (tmp_path / "tsconfig.json").write_text("{}")
    pr = {x["language"]: x for x in report(tmp_path)["project"]}
    assert not pr["kotlin"]["ok"] and pr["kotlin"]["fix"] and pr["typescript"]["what"].startswith("tsconfig.json at the root")


def test_doctor_scip_java_releases_for_the_kotlin_version(tmp_path, monkeypatch):
    """#67: `cg doctor <root>` names the installed scip-java releases with their Kotlin ranges and the one that fits."""
    from codegraph.doctor import report
    from codegraph.plugins.kotlin import exact
    (tmp_path / "M.kt").write_text("fun main() {}\n")
    monkeypatch.setattr(exact, "scip_java_candidates", lambda: ["/t/scip-java", "/t/scip-java-0.13.1/scip-java"])
    monkeypatch.setattr(exact, "_generation", lambda t: 13 if "0.13" in t else 12)
    for kv, ok, pick in (("2.2.10", True, "0.13.1"), ("2.0.21", True, "/t/scip-java "), ("2.3.0", False, None)):
        (tmp_path / "build.gradle.kts").write_text(f'plugins {{ kotlin("jvm") version "{kv}" }}\n')
        row = [x for x in report(tmp_path)["project"] if x["language"] == "kotlin" and "scip-java" in x["what"]
               and "installed:" in x["what"]][0]
        assert row["ok"] is ok and "0.13: Kotlin 2.2.0 - 2.2.10" in row["what"]
        if ok:
            assert pick in row["what"].split("(installed")[0] + " "
        else:
            assert "2.2.20+" in row["fix"]
