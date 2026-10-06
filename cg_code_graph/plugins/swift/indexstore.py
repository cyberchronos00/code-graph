"""Read a Swift compiler index store (`swift build --enable-index-store`, Xcode DerivedData `Index.noindex/DataStore`)
through the toolchain's libIndexStore C API (ctypes, the function-pointer `_apply_f` variants).

load(store, roots) -> {source path: [Occurrence]} for the units whose main file lies under one of `roots`.
"""
from __future__ import annotations

import ctypes
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from ...core.env import get as cg_env

DECLARATION, DEFINITION, REFERENCE, READ, WRITE, CALL, DYNAMIC, ADDRESSOF, IMPLICIT = (1 << i for i in range(9))
REL_CHILDOF, REL_BASEOF, REL_OVERRIDEOF, REL_RECEIVEDBY, REL_CALLEDBY, REL_EXTENDEDBY, REL_ACCESSOROF, \
    REL_CONTAINEDBY = (1 << i for i in range(9, 17))

KIND = {1: "module", 5: "enum", 6: "struct", 7: "class", 8: "protocol", 9: "extension", 11: "typealias",
        12: "function", 13: "variable", 14: "field", 15: "enumconstant", 16: "instancemethod", 17: "classmethod",
        18: "staticmethod", 19: "instanceproperty", 20: "classproperty", 21: "staticproperty", 22: "constructor",
        23: "destructor", 25: "parameter"}
CALLABLE = {"function", "instancemethod", "classmethod", "staticmethod", "constructor"}
TYPES = {"enum", "struct", "class", "protocol"}


class StrRef(ctypes.Structure):
    _fields_ = [("data", ctypes.c_char_p), ("length", ctypes.c_size_t)]


def _s(r: StrRef) -> str:
    if not r.data or not r.length:
        return ""
    return ctypes.string_at(r.data, r.length).decode("utf-8", "replace")


@dataclass
class Occurrence:
    line: int
    col: int
    roles: int
    usr: str
    name: str
    kind: str
    subkind: int
    rels: list = field(default_factory=list)     # (roles, usr, name, kind)


def find_lib(swift: str | None = None) -> str | None:
    """libIndexStore.so: CG_LIBINDEXSTORE, else next to the `swift` toolchain (usr/lib)."""
    env = cg_env("LIBINDEXSTORE")
    if env:
        return env if Path(env).exists() else None
    sw = swift or shutil.which("swift")
    if sw:
        base = Path(sw).resolve().parent.parent
        for name in ("libIndexStore.so", "libIndexStore.dylib"):
            for d in (base / "lib", base / "lib" / "swift" / "linux"):
                if (d / name).exists():
                    return str(d / name)
    # macOS: /usr/bin/swift is a shim; the library lives in the selected Xcode's default toolchain
    for dev in (os.environ.get("DEVELOPER_DIR"), "/Applications/Xcode.app/Contents/Developer"):
        if dev:
            p = Path(dev) / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "lib" / "libIndexStore.dylib"
            if p.exists():
                return str(p)
    return None


_LIB = {}


def _lib(path: str):
    if path in _LIB:
        return _LIB[path]
    L = ctypes.CDLL(path)
    vp, u64, cb = ctypes.c_void_p, ctypes.c_uint64, ctypes.CFUNCTYPE
    L.indexstore_store_create.restype = vp
    L.indexstore_store_create.argtypes = [ctypes.c_char_p, ctypes.POINTER(vp)]
    L.indexstore_error_get_description.restype = ctypes.c_char_p
    L.indexstore_error_get_description.argtypes = [vp]
    L.indexstore_store_dispose.argtypes = [vp]
    L.UNIT_CB = cb(ctypes.c_bool, vp, StrRef)
    L.indexstore_store_units_apply_f.argtypes = [vp, ctypes.c_uint, vp, L.UNIT_CB]
    L.indexstore_unit_reader_create.restype = vp
    L.indexstore_unit_reader_create.argtypes = [vp, ctypes.c_char_p, ctypes.POINTER(vp)]
    L.indexstore_unit_reader_dispose.argtypes = [vp]
    for f in ("main_file", "module_name", "working_dir"):
        fn = getattr(L, f"indexstore_unit_reader_get_{f}")
        fn.restype, fn.argtypes = StrRef, [vp]
    L.indexstore_unit_reader_is_system_unit.restype = ctypes.c_bool
    L.indexstore_unit_reader_is_system_unit.argtypes = [vp]
    L.DEP_CB = cb(ctypes.c_bool, vp, vp)
    L.indexstore_unit_reader_dependencies_apply_f.argtypes = [vp, vp, L.DEP_CB]
    L.indexstore_unit_dependency_get_kind.restype = ctypes.c_int
    L.indexstore_unit_dependency_get_kind.argtypes = [vp]
    for f in ("filepath", "name"):
        fn = getattr(L, f"indexstore_unit_dependency_get_{f}")
        fn.restype, fn.argtypes = StrRef, [vp]
    L.indexstore_record_reader_create.restype = vp
    L.indexstore_record_reader_create.argtypes = [vp, ctypes.c_char_p, ctypes.POINTER(vp)]
    L.indexstore_record_reader_dispose.argtypes = [vp]
    L.OCC_CB = cb(ctypes.c_bool, vp, vp)
    L.indexstore_record_reader_occurrences_apply_f.argtypes = [vp, vp, L.OCC_CB]
    L.indexstore_occurrence_get_symbol.restype = vp
    L.indexstore_occurrence_get_symbol.argtypes = [vp]
    L.indexstore_occurrence_get_roles.restype = u64
    L.indexstore_occurrence_get_roles.argtypes = [vp]
    L.indexstore_occurrence_get_line_col.argtypes = [vp, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    L.REL_CB = cb(ctypes.c_bool, vp, vp)
    L.indexstore_occurrence_relations_apply_f.argtypes = [vp, vp, L.REL_CB]
    L.indexstore_symbol_relation_get_roles.restype = u64
    L.indexstore_symbol_relation_get_roles.argtypes = [vp]
    L.indexstore_symbol_relation_get_symbol.restype = vp
    L.indexstore_symbol_relation_get_symbol.argtypes = [vp]
    for f in ("name", "usr"):
        fn = getattr(L, f"indexstore_symbol_get_{f}")
        fn.restype, fn.argtypes = StrRef, [vp]
    for f in ("kind", "subkind", "language"):
        fn = getattr(L, f"indexstore_symbol_get_{f}")
        fn.restype, fn.argtypes = ctypes.c_int, [vp]
    _LIB[path] = L
    return L


class IndexStoreError(RuntimeError):
    pass


def load(store: str | Path, roots: list[str | Path] | None, lib_path: str) -> dict[str, list[Occurrence]]:
    """Occurrences per absolute source path, for non-system units whose main file is under `roots`."""
    L = _lib(lib_path)
    err = ctypes.c_void_p()
    st = L.indexstore_store_create(str(store).encode(), ctypes.byref(err))
    if not st:
        msg = L.indexstore_error_get_description(err) if err else b"?"
        raise IndexStoreError(f"cannot open index store {store}: {msg.decode(errors='replace')}")
    roots_s = [str(Path(r).resolve()) + os.sep for r in roots] if roots else None
    units: list[str] = []

    @L.UNIT_CB
    def on_unit(_ctx, name):
        units.append(_s(name))
        return True

    L.indexstore_store_units_apply_f(st, 0, None, on_unit)
    records: dict[str, str] = {}      # source path -> record name (the newest unit wins)
    for u in units:
        ur = L.indexstore_unit_reader_create(st, u.encode(), ctypes.byref(err))
        if not ur:
            continue
        try:
            if L.indexstore_unit_reader_is_system_unit(ur):
                continue
            main = _s(L.indexstore_unit_reader_get_main_file(ur))
            if not main or (roots_s is not None and not any(main.startswith(r) for r in roots_s)):
                continue

            @L.DEP_CB
            def on_dep(_ctx, dep):
                if L.indexstore_unit_dependency_get_kind(dep) == 2:          # record
                    fp = _s(L.indexstore_unit_dependency_get_filepath(dep))
                    if fp == main:
                        records[fp] = _s(L.indexstore_unit_dependency_get_name(dep))
                return True

            L.indexstore_unit_reader_dependencies_apply_f(ur, None, on_dep)
        finally:
            L.indexstore_unit_reader_dispose(ur)
    out: dict[str, list[Occurrence]] = {}
    sym_cache: dict[int, tuple] = {}

    def sym(p):
        r = sym_cache.get(p)
        if r is None:
            r = (_s(L.indexstore_symbol_get_usr(p)), _s(L.indexstore_symbol_get_name(p)),
                 KIND.get(L.indexstore_symbol_get_kind(p), "other"), L.indexstore_symbol_get_subkind(p))
            sym_cache[p] = r
        return r

    for path, rec in records.items():
        rr = L.indexstore_record_reader_create(st, rec.encode(), ctypes.byref(err))
        if not rr:
            continue
        occs: list[Occurrence] = []
        sym_cache.clear()
        try:
            @L.OCC_CB
            def on_occ(_ctx, o):
                line, col = ctypes.c_uint(), ctypes.c_uint()
                L.indexstore_occurrence_get_line_col(o, ctypes.byref(line), ctypes.byref(col))
                usr, name, kind, sub = sym(L.indexstore_occurrence_get_symbol(o))
                oc = Occurrence(line.value, col.value, L.indexstore_occurrence_get_roles(o), usr, name, kind, sub)

                @L.REL_CB
                def on_rel(_c, rel):
                    rs = sym(L.indexstore_symbol_relation_get_symbol(rel))
                    oc.rels.append((L.indexstore_symbol_relation_get_roles(rel), rs[0], rs[1], rs[2]))
                    return True

                L.indexstore_occurrence_relations_apply_f(o, None, on_rel)
                occs.append(oc)
                return True

            L.indexstore_record_reader_occurrences_apply_f(rr, None, on_occ)
        finally:
            L.indexstore_record_reader_dispose(rr)
        out[path] = occs
    L.indexstore_store_dispose(st)
    return out
