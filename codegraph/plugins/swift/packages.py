"""SwiftPM modules (#90): the targets of every local `Package.swift`, the folder each one compiles and the targets
it depends on (its own `dependencies:`, `.product(name:package:)` of local packages, transitively). A file in a
package target sees only its own module and those dependencies, never app, extension, preview or test code; a file
outside every package target (an Xcode app, extension or preview folder) sees everything it links."""
from __future__ import annotations

import re
from pathlib import Path

TARGET = re.compile(r"\.(target|executableTarget|testTarget|macro|plugin|systemLibrary|binaryTarget)\s*\(\s*name\s*:\s*\"([^\"]+)\"")
PRODUCT = re.compile(r"\.(?:library|executable|plugin)\s*\(\s*name\s*:\s*\"([^\"]+)\"[^)]*?targets\s*:\s*\[([^\]]*)\]", re.S)
DEP_PRODUCT = re.compile(r"\.product\s*\(\s*name\s*:\s*\"([^\"]+)\"")
DEP_NAMED = re.compile(r"\.(?:target|byName)\s*\(\s*name\s*:\s*\"([^\"]+)\"")
DEP_STRING = re.compile(r"(?<![:\w])\s*\"([^\"]+)\"")


def _call_body(txt: str, start: int) -> tuple[str, int]:
    """The text of the parenthesised call opening at or after `start`, up to its closing parenthesis (parentheses
    inside string literals do not count), and the offset of that parenthesis."""
    i = txt.find("(", start)
    depth, j = 0, i
    while 0 <= j < len(txt):
        c = txt[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return txt[i + 1:j], j
        elif c == '"':
            k = txt.find('"', j + 1)
            if k < 0:
                break
            j = k
        j += 1
    return txt[i + 1:], len(txt)


def _array(body: str, key: str) -> str | None:
    m = re.search(rf"\b{key}\s*:\s*\[", body)
    if not m:
        return None
    depth, i = 0, m.end() - 1
    for j in range(i, len(body)):
        if body[j] == "[":
            depth += 1
        elif body[j] == "]":
            depth -= 1
            if depth == 0:
                return body[i + 1:j]
    return body[i + 1:]


class SwiftPM:
    def __init__(self, root: Path, files: list[str] | None = None):
        """`files`: the project's Swift files as the index walks them (skip presets applied); the manifests are the
        `Package.swift` among them (default: the root and up to three folders down)."""
        self.root = root
        self._files = files
        self.targets: dict[str, dict] = {}       # "<package dir>:<name>" -> {name, path, deps (raw), kind}
        self.products: dict[str, set[str]] = {}  # product name -> target keys
        self.manifests: list[str] = []
        for pk in sorted(self._manifests()):
            self._parse(pk)
        by_name: dict[str, list[str]] = {}
        for k, t in self.targets.items():
            by_name.setdefault(t["name"], []).append(k)
        self.visible: dict[str, set[str]] = {}
        direct = {}
        for k, t in self.targets.items():
            pkg = k.rsplit(":", 1)[0]
            out = set()
            for kind, name in t["deps"]:
                same = f"{pkg}:{name}"
                if kind != "product" and same in self.targets:
                    out.add(same)
                elif name in self.products:
                    out |= self.products[name]
                elif kind != "product":
                    out |= set(by_name.get(name, ()))
            direct[k] = out
        for k in self.targets:
            seen, todo = {k}, [k]
            while todo:
                for d in direct.get(todo.pop(), ()):
                    if d not in seen:
                        seen.add(d)
                        todo.append(d)
            self.visible[k] = seen
        every = set(self.targets)
        for k, t in self.targets.items():
            if t.get("deps_unknown"):
                self.visible[k] = every
        # longest target folder first
        self._paths = sorted(((t["path"], k) for k, t in self.targets.items() if t["path"] is not None),
                             key=lambda x: -len(x[0]))
        self._cache: dict[str, str | None] = {}

    def _manifests(self):
        if self._files is not None:
            return [self.root / f for f in self._files if f.rsplit("/", 1)[-1] == "Package.swift" and f.count("/") <= 3]
        out = []
        for depth in range(0, 4):
            for p in self.root.glob("/".join(["*"] * depth + ["Package.swift"])):
                # (the index passes its file list, skip presets applied; this walk is for direct use)
                if not any(x.startswith(".") for x in p.relative_to(self.root).parts[:-1]):
                    out.append(p)
        return out

    def _parse(self, pk: Path):
        try:
            txt = pk.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        txt = re.sub(r"//[^\n]*|/\*.*?\*/", " ", txt, flags=re.S)
        d = pk.parent.relative_to(self.root).as_posix()
        d = "" if d == "." else d
        self.manifests.append(pk.relative_to(self.root).as_posix())
        end = -1
        for m in TARGET.finditer(txt):
            if m.start() < end:
                continue          # `.target(name: "Csqlite3")` in another target's `dependencies:`
            kind, name = m.groups()
            body, end = _call_body(txt, m.start())
            deps = []
            arr = _array(body, "dependencies")
            # dependencies the manifest computes (`dependencies: shared`, `[...] + extra`): unknown, so the target
            # may name any package target (never app code)
            unknown = bool(re.search(r"\bdependencies\s*:", body)) and (arr is None or bool(re.sub(
                r"\.\w+\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)|\"[^\"]*\"|[\s,]", "", arr)) or bool(re.search(
                r"\bdependencies\s*:\s*\[" + re.escape(arr) + r"\]\s*\+", body)))
            if arr:
                for x in DEP_PRODUCT.finditer(arr):
                    deps.append(("product", x.group(1)))
                for x in DEP_NAMED.finditer(arr):
                    deps.append(("target", x.group(1)))
                plain = re.sub(r"\.\w+\s*\((?:[^()]|\([^()]*\))*\)", " ", arr)      # drop .product(...) / .target(...)
                for x in DEP_STRING.finditer(plain):
                    deps.append(("name", x.group(1)))
            pm = re.search(r"\bpath\s*:\s*\"([^\"]*)\"", body)
            if kind in ("binaryTarget", "plugin") and not pm:
                path = None
            else:
                sub = pm.group(1) if pm else (f"Tests/{name}" if kind == "testTarget" else
                                               f"Plugins/{name}" if kind == "plugin" else f"Sources/{name}")
                path = "/".join(x for x in (d, sub.strip("/")) if x)
            self.targets[f"{d}:{name}"] = {"name": name, "path": path, "deps": deps, "kind": kind,
                                          "deps_unknown": unknown}
        for m in PRODUCT.finditer(txt):
            names = re.findall(r"\"([^\"]+)\"", m.group(2))
            self.products.setdefault(m.group(1), set()).update(f"{d}:{n}" for n in names)

    def module_of(self, rel: str) -> str | None:
        """The package target compiling the file, or None (app / extension / preview code, or no package)."""
        if rel in self._cache:
            return self._cache[rel]
        hit = None
        for p, k in self._paths:
            if rel == p or rel.startswith(p + "/"):
                hit = k
                break
        self._cache[rel] = hit
        return hit

    def sees(self, caller_rel: str, callee_rel: str) -> bool:
        """May code in `caller_rel` name a declaration of `callee_rel`?"""
        m = self.module_of(caller_rel)
        if m is None:
            return True
        dm = self.module_of(callee_rel)
        return dm is not None and dm in self.visible.get(m, {m})

    def __bool__(self):
        return bool(self.targets)
