"""Heuristic Kotlin ↔ Java calls, after both syntax layers have declared their nodes.

Kotlin → Java: a call whose receiver type (or a bare constructor name) is a Java class in scope through an
import or the same package. Java → Kotlin: `FooKt.bar()` and `@file:JvmName` facades, `Foo.Companion.x()`,
`@JvmStatic` as `Foo.x()`, and `getX` / `setX` / `isX` on a Kotlin property (the same `READS_PROP` /
`WRITES_PROP` or `CALLS` edges the Kotlin layer uses for properties).
"""
from __future__ import annotations

import re

from ...core.model import HEURISTIC, RESOLVED

_ACC = re.compile(r"(get|set|is)([A-Z]\w*)")


def link_kotlin_to_java(kotlin, java) -> int:
    """Resolve Kotlin calls the Kotlin layer left unresolved when the target is a Java declaration."""
    n = 0
    pending = list(getattr(kotlin, "unresolved_calls", ()) or ())
    for owner, name, recv, line, kf, decl in pending:
        if _added(kotlin, java, owner, name, recv, line, kf, decl):
            n += 1
            kotlin.st["calls_resolved"] = kotlin.st.get("calls_resolved", 0) + 1
            kotlin.st["calls_unresolved"] = max(0, kotlin.st.get("calls_unresolved", 0) - 1)
    stats = getattr(kotlin, "_exported_stats", None)
    if stats is not None and n:
        stats["calls_resolved"] = kotlin.st.get("calls_resolved", 0)
        stats["calls_unresolved"] = kotlin.st.get("calls_unresolved", 0)
        stats["interop_kotlin_to_java"] = stats.get("interop_kotlin_to_java", 0) + n
    kotlin.st["interop_kotlin_to_java"] = kotlin.st.get("interop_kotlin_to_java", 0) + n
    return n


def _added(kotlin, java, owner, name, recv, line, kf, decl) -> bool:
    if recv is None and name[:1].isupper():
        jc = _java_class(java, name, kf)
        if jc is None:
            return False
        kotlin.b.add_edge(owner, jc.id, "INSTANTIATES", kf.rel, line, HEURISTIC, binding="java")
        ctor = java.members.get(jc.fqn, {}).get("<init>", [])
        for m in ctor[:1]:
            kotlin.b.add_edge(owner, m.id, "CALLS", kf.rel, line, HEURISTIC, binding="constructor")
        return True
    if not recv:
        return False
    rname = re.sub(r"[?!]", "", recv).split(".")[-1].strip()
    cls = kotlin.classes.get(decl.cls) if decl is not None and decl.cls else None
    ty = decl.types.get(rname) if decl is not None else None
    if ty is None and cls is not None:
        ty = kotlin._field_type(cls, rname)
    if ty is None and rname[:1].isupper():
        ty = re.sub(r"\s*[({].*$", "", rname, flags=re.S)
    if not ty:
        return False
    short = re.sub(r"[<(].*", "", ty, flags=re.S).rstrip("?! ").split(".")[-1]
    jc = _java_class(java, short, kf)
    if jc is None:
        return False
    members = java._uniq(java._member(jc, name))
    if not members:
        return False
    for m in members[:3]:
        kotlin.b.add_edge(owner, m.id, "CALLS", kf.rel, line, HEURISTIC, binding="java")
    return True


def _java_class(java, short: str, kf):
    fq = kf.imports.get(short)
    if fq and fq in java.classes:
        return java.classes[fq]
    if kf.package and f"{kf.package}.{short}" in java.classes:
        return java.classes[f"{kf.package}.{short}"]
    hits = [java.classes[f"{p}.{short}"] for p in kf.star if f"{p}.{short}" in java.classes]
    if len(hits) == 1:
        return hits[0]
    return None


def java_to_kotlin(builder, name: str, recv_text: str | None, type_name: str | None,
                   package: str, imports: dict, star: list, owner: str, file: str, line: int) -> bool:
    """One Java call whose target is a Kotlin facade, companion, JvmStatic method, or property accessor."""
    if _facade_call(builder, name, recv_text, type_name, package, imports, star, owner, file, line):
        return True
    if recv_text and (recv_text.endswith(".Companion") or recv_text == "Companion"):
        return _companion_call(builder, name, recv_text, package, imports, star, owner, file, line)
    short = _short(type_name or (recv_text if recv_text and recv_text[:1].isupper() else None))
    if not short:
        return False
    cls = _kt_class(builder, short, package, imports, star)
    if cls is None:
        return False
    acc = _ACC.fullmatch(name)
    if acc and _accessor_edge(builder, cls, acc.group(1), acc.group(2), owner, file, line):
        return True
    hit = _kt_method(builder, cls, name)
    if hit is not None:
        builder.add_edge(owner, hit, "CALLS", file, line, HEURISTIC, binding="kotlin")
        return True
    return _jvm_static(builder, cls, name, owner, file, line)


def _facade_call(builder, name, recv_text, type_name, package, imports, star, owner, file, line) -> bool:
    short = _short(type_name or recv_text)
    if not short:
        return False
    for n in builder.nodes.values():
        if n.lang != "kotlin" or n.kind != "file" or n.attrs.get("jvm_name") != short:
            continue
        if not _name_visible(short, n.module or "", package, imports, star):
            continue
        for fn in builder.nodes.values():
            if (fn.lang == "kotlin" and fn.kind == "function" and fn.file == n.file and fn.name == name
                    and fn.attrs.get("kotlin_kind") == "function"):
                builder.add_edge(owner, fn.id, "CALLS", file, line, HEURISTIC, binding="kotlin-facade")
                return True
    return False


def _companion_call(builder, name, recv_text, package, imports, star, owner, file, line) -> bool:
    host = recv_text[: -len(".Companion")] if recv_text.endswith(".Companion") else None
    short = _short(host) if host else None
    if not short:
        return False
    for n in builder.nodes.values():
        if n.lang != "kotlin" or n.kind != "class" or not (n.fqn or "").endswith(f".{short}.Companion"):
            continue
        outer_pkg = ".".join((n.fqn or "").split(".")[:-2])
        if not _name_visible(short, outer_pkg, package, imports, star):
            continue
        for m in builder.nodes.values():
            if m.lang == "kotlin" and m.name == name and (m.fqn or "") == f"{n.fqn}.{name}":
                builder.add_edge(owner, m.id, "CALLS", file, line, HEURISTIC, binding="kotlin-companion")
                return True
    return False


def _accessor_edge(builder, cls, kind: str, raw: str, owner: str, file: str, line: int) -> bool:
    prop = raw[:1].lower() + raw[1:]
    write = kind == "set"
    fid = f"field:{cls.fqn}.{prop}"
    node = builder.nodes.get(fid)
    if node is None and kind == "is":
        node = builder.nodes.get(f"field:{cls.fqn}.{kind}{raw}")
        fid = f"field:{cls.fqn}.{kind}{raw}"
    if node is not None and node.lang == "kotlin" and node.kind == "field":
        builder.add_edge(owner, node.id, "WRITES_PROP" if write else "READS_PROP", file, line, RESOLVED,
                         binding="kotlin-accessor")
        return True
    for m in builder.nodes.values():
        if m.lang != "kotlin" or m.name not in (prop, f"is{raw}" if kind == "is" else prop):
            continue
        if m.attrs.get("property") and (m.fqn or "").rsplit(".", 1)[0] == cls.fqn:
            builder.add_edge(owner, m.id, "CALLS", file, line, HEURISTIC, property="write" if write else "read",
                             binding="kotlin-accessor")
            return True
    return False


def _jvm_static(builder, cls, name, owner, file, line) -> bool:
    comp = f"{cls.fqn}.Companion"
    for m in builder.nodes.values():
        if m.lang != "kotlin" or m.name != name:
            continue
        anns = m.attrs.get("annotations") or []
        if "JvmStatic" not in anns:
            continue
        owner_fqn = (m.fqn or "").rsplit(".", 1)[0]
        if owner_fqn in (cls.fqn, comp):
            builder.add_edge(owner, m.id, "CALLS", file, line, HEURISTIC, binding="kotlin-jvmstatic")
            return True
    return False


def _kt_method(builder, cls, name: str):
    for m in builder.nodes.values():
        if m.lang == "kotlin" and m.name == name and (m.fqn or "") == f"{cls.fqn}.{name}" and m.kind in (
                "method", "function"):
            return m.id
    return None


def _kt_class(builder, short: str, package: str, imports: dict, star: list):
    fq = imports.get(short)
    hits = []
    for n in builder.nodes.values():
        if n.lang != "kotlin" or n.kind != "class" or n.name != short:
            continue
        if fq and n.fqn == fq:
            return n
        if _name_visible(short, ".".join((n.fqn or short).split(".")[:-1]), package, imports, star):
            hits.append(n)
    return hits[0] if len(hits) == 1 else None


def _name_visible(simple: str, owner_pkg: str, package: str, imports: dict, star: list) -> bool:
    fqn = f"{owner_pkg}.{simple}" if owner_pkg else simple
    if imports.get(simple) == fqn:
        return True
    if package and fqn == f"{package}.{simple}":
        return True
    if owner_pkg and owner_pkg == package:
        return True
    return any(fqn.startswith(s + ".") or owner_pkg == s for s in star)


def _short(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"[<(].*", "", text, flags=re.S).strip().rstrip("?! ")
    part = text.split(".")[-1]
    return part or None
