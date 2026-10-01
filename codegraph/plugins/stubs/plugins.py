"""Stub / SCIP-backed plugins for languages beyond the PHP beta.

ScipIndexerPlugin runs an external SCIP indexer and imports the result through
plugins/scip/importer.py. Detection works today; indexing runs only when the indexer
binary is installed (none are installed on the box yet: this is the v1 path).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ...core.plugin import FrameworkPlugin, GraphBuilder, LanguagePlugin, Project
from ..scip.importer import import_scip


class ScipIndexerPlugin(LanguagePlugin):
    def __init__(self, name: str, markers: list[str], command: list[str], lang_key: str, output="index.scip"):
        self.name, self.markers, self.command, self.lang_key, self.output = name, markers, command, lang_key, output

    def detect(self, project: Project) -> bool:
        return any(project.exists(m) for m in self.markers)

    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        if not shutil.which(self.command[0]):
            return {"status": "stub", "reason": f"indexer '{self.command[0]}' not installed", "command": " ".join(self.command)}
        subprocess.run(self.command, cwd=project.root, check=True)
        return import_scip(Path(project.root) / self.output, builder, self.lang_key)


SCIP_PLUGINS = [
    ScipIndexerPlugin("go", ["go.mod"], ["scip-go"], "go"),
    ScipIndexerPlugin("rust", ["Cargo.toml"], ["rust-analyzer", "scip", "."], "rust"),
    ScipIndexerPlugin("c_cpp", ["compile_commands.json"], ["scip-clang", "--compdb-path=compile_commands.json"], "c_cpp"),
    ScipIndexerPlugin("python", ["pyproject.toml", "setup.py"], ["scip-python", "index", "."], "python"),
    ScipIndexerPlugin("java", ["pom.xml", "build.gradle"], ["scip-java", "index"], "java"),
]

# TypeScript/Vue/Nuxt moved to plugins/ts (language) and plugins/nuxt (framework).
