"""`cg parity` (#85): symbols of a source graph with no counterpart in a target graph, for an app ported between
platforms (Swift / SwiftUI <-> Kotlin / Compose, or any two indexed languages). See docs/parity.md.

Compared: types (class / struct / enum / protocol / interface / object), the members of matched types (methods,
properties, enum cases, constants, Kotlin nested sealed-class objects), and top-level functions / constants. Test
code, test support (`TestHelpers/`, `Mock*`), Swift `Has*` service-locator protocols, extension nodes, Kotlin
companion objects, lifecycle overrides and boilerplate (`body`, `init`, `hash`...) are left out.
A source symbol is matched by, in this order:
  explicit   a comment above the declaration naming its counterpart (`/// Port of: FooView`, `// iOS: Foo.bar`,
             `// Android: FooScreen`) on either side, or the `--map` JSON file {"source name": "target name"}
  exact      the same name in the same category (type / function / constant); members: same name in the matched type
  normalized case and `_` folded; `Default` / `Impl` / `Json` / `RequestModel` and `--strip-prefix` words dropped;
             `VM` and `Processor` read as ViewModel; UI suffixes (View, Screen, Sheet, Page, Fragment, Activity,
             ViewController, VC, Controller) stripped on both sides only (a model `Otp` does not meet `OtpFragment`);
             the same words in another order; a SwiftUI type with `body` also matches a `@Composable` function.
             Members: `get` prefixes and UI event verbs (`deletePressed` / `DeleteClick`) dropped
  fuzzy      the same words with one of them shortened or pluralised (`ItemList` / `ItemListing`)
  moved      a member missing on the counterpart type whose name (three words or more) exists on exactly one other
             target type
A nested type is looked for inside its owner's counterpart (elsewhere by exact name only). Symbols in a file that
parsed with syntax errors go to `unknown`, not `missing`; symbols tagged only for platforms the target does not build
(`attrs.platforms`) go to `platform_only`."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

TYPE_KINDS = {"class", "struct", "enum", "protocol", "interface", "object", "actor", "trait"}
UI_SUFFIXES = ("viewcontroller", "controller", "fragment", "activity", "screen", "sheet", "page", "view", "vc")
EXPLICIT = re.compile(r"(?:port(?:ed)? of|counterpart|ios|android|swift|kotlin)\s*:\s*`?([A-Za-z_][\w.]*)", re.I)


# test support and dependency-injection glue: no counterpart expected on the other platform
SUPPORT_PATH = re.compile(r"(^|/)(\w*TestHelpers?|Mocks?|Fakes?|Stubs?|Fixtures?|testFixtures|PreviewContent)(/|$)")
SUPPORT_NAME = re.compile(r"^(Mock|Fake|Stub|Spy)[A-Z]")
DI_PROTOCOL = re.compile(r"^Has[A-Z]\w*$")          # Swift `protocol HasAuthService` (service-locator composition)


_PREFIXES: list[str] = []          # --strip-prefix words (lower case), set for one parity() run


def _fold(name: str) -> str:
    s = re.sub(r"[_\s`]", "", name or "").lower()
    for p in _PREFIXES:
        if s.startswith(p) and len(s) > len(p) + 2:
            s = s[len(p):]
            break
    s = re.sub(r"vm$", "viewmodel", s)
    s = re.sub(r"processor$", "viewmodel", s)          # Swift processor / Kotlin view model (unidirectional flow)
    s = re.sub(r"^default(?=[a-z]{3})", "", s)          # Swift `DefaultFooService` / Kotlin `FooServiceImpl`
    s = re.sub(r"(?<=[a-z]{3})impl$", "", s)
    s = re.sub(r"(?<=[a-z]{3})json$", "", s)            # Kotlin wire models `FooResponseJson`
    s = re.sub(r"(request|response)model$", r"\1", s)
    return s


def norm(name: str) -> str:
    s = _fold(name)
    for suf in UI_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


def norm_member(name: str) -> str:
    """Members: case / underscores folded (`case fooBar` == `FOO_BAR`), Swift argument labels and Kotlin `()` gone,
    a `get` accessor prefix (`getDefaultUriMatchType()` / `var defaultUriMatchType`) and the UI event verb
    (`deletePressed` / `deleteTapped` / `DeleteClick`) dropped."""
    n = (name or "").split("(")[0]
    n = re.sub(r"^get(?=[A-Z])", "", n)
    s = re.sub(r"[_\s`]", "", n).lower()
    return re.sub(r"(?<=[a-z]{3})(pressed|tapped|clicked|click|tap)$", "", s)


def _kind_of(r) -> str:
    a = json.loads(r["attrs"] or "{}")
    k = a.get("swift_kind") or a.get("kotlin_kind") or r["kind"]
    return "type" if (r["kind"] == "class" or k in TYPE_KINDS) and k != "extension" else r["kind"]


def load(db: str) -> dict:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    meta = dict(c.execute("SELECT key, value FROM meta"))
    st = json.loads(meta.get("stats") or "{}")
    err_files = set()
    for e in ((st.get("coverage") or {}).get("languages") or []):
        for x in e.get("syntax_errors") or []:
            err_files.add(x.get("file"))
    platforms = set(((st.get("platforms") or {}).get("targets")) or [])
    try:
        root = Path(json.loads(meta.get("root") or '"."'))
    except ValueError:
        root = Path(meta.get("root") or ".")
    lines_of: dict = {}
    types, members, top, support = {}, defaultdict(dict), {}, 0
    rows = c.execute("SELECT id, kind, name, fqn, file, line, module, doc, lang, attrs FROM nodes WHERE kind IN "
                     "('class','method','function','enum_case','constant')").fetchall()
    for r in rows:
        a = json.loads(r["attrs"] or "{}")
        if a.get("test") or a.get("placeholder") or not r["file"]:
            continue
        fqn = (r["fqn"] or r["name"] or "").replace(".Companion.", ".")      # Kotlin companion members -> the class
        if fqn.endswith(".Companion") or fqn == "Companion":
            continue
        sym = {"id": r["id"], "name": r["name"], "fqn": fqn, "file": r["file"], "line": r["line"],
               "kind": r["kind"], "lang": r["lang"], "platforms": a.get("platforms"),
               "composable": "Composable" in (a.get("annotations") or []), "module": r["module"],
               "override": bool(a.get("override")),
               "doc": r["doc"] or _comment_above(root, r["file"], r["line"], lines_of)}
        sk = a.get("swift_kind") or a.get("kotlin_kind")
        if sk == "extension":
            continue                 # the extension node itself; its members stay with the extended type
        if SUPPORT_PATH.search(r["file"]) or any(SUPPORT_NAME.match(x) for x in fqn.split(".")) or \
                (sk == "protocol" and DI_PROTOCOL.match(r["name"] or "")):
            support += 1
            continue
        if _kind_of(r) == "type":
            types.setdefault(sym["fqn"], sym)
        elif r["kind"] in ("method", "enum_case", "constant") and r["module"] and "." in (sym["fqn"] or ""):
            owner = sym["fqn"].rsplit(".", 1)[0]
            sym["name"] = sym["fqn"].rsplit(".", 1)[1]
            members[owner].setdefault(sym["name"], sym)
        else:
            top.setdefault(sym["fqn"], sym)
    # members of a type nobody declares here (extensions of library types) stay top-level-ish: keyed by owner
    for owner in [o for o in members if o not in types]:
        for nm, sym in members.pop(owner).items():
            top.setdefault(f"{owner}.{nm}", sym)
    return {"types": types, "members": members, "top": top, "err_files": err_files, "platforms": platforms, "db": db,
            "support": support}


def _first_word(name: str) -> str:
    """`GreenCertificateVC` -> green, `OTPValidation` -> otp: fuzzy matches keep the first word."""
    m = re.match(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", name or "")
    return (m.group(0) if m else name or "").lower()


def _comment_above(root: Path, file: str, line: int | None, cache: dict) -> str:
    """The `//` / `///` / `/* */` comment block right above a declaration (attributes / annotations skipped): the
    graph keeps no doc comment for Swift and Kotlin, and `/// Android: ProfileFragment` lives there."""
    if not line or not file:
        return ""
    if file not in cache:
        try:
            cache[file] = (root / file).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            cache[file] = []
    src, out, i = cache[file], [], line - 2
    while 0 <= i < len(src) and len(out) < 12:
        t = src[i].strip()
        if t.startswith("@") and not out:
            i -= 1
            continue
        if t.startswith(("//", "/*", "*", "*/")):
            out.append(t)
            i -= 1
            continue
        break
    return "\n".join(reversed(out))


NOISE_WORDS = {"json", "impl", "default"}


def _bag(name: str) -> str:
    """The words of a name without their order (and the UI suffix flag): a port that names `AddEditFolder` as
    `FolderAddEdit`."""
    k = key(name)
    ws = [w for w in _words(name) if w not in UI_SUFFIXES]
    if ws and ws[0] in _PREFIXES and len(ws) > 1:
        ws = ws[1:]
    return " ".join(sorted(ws)) + ("|ui" if k.endswith("|ui") else "")


def _words(name: str) -> list[str]:
    ws = [w.lower() for w in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", name or "")]
    return [w for w in ws if w not in NOISE_WORDS] or ws


def _near(a: list[str], b: list[str]) -> bool:
    if a == b:
        return True
    if len(a) == len(b):
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        return len(diff) == 1 and min(len(diff[0][0]), len(diff[0][1])) >= 4 and \
            (diff[0][0].startswith(diff[0][1]) or diff[0][1].startswith(diff[0][0]))
    return False


def _short(fqn: str) -> str:
    return fqn.rsplit(".", 1)[-1]


def _explicit(sym: dict) -> str | None:
    m = EXPLICIT.search(sym.get("doc") or "")
    return m.group(1) if m else None


def key(name: str) -> str:
    """Normalized match key: the stem, plus `|ui` when a UI suffix was stripped, so `CartView` meets `CartScreen` but
    a model type `Otp` does not meet `OtpFragment`."""
    stem = norm(name)
    return stem + ("|ui" if stem != _fold(name) else "")


def _cat(sym: dict, is_type: bool) -> str:
    return "type" if is_type else "const" if sym["kind"] in ("constant", "enum_case") else "func"


def parity(source_db: str, target_db: str, mapping: dict | None = None, fuzzy: bool = True,
           strip_prefixes: list[str] | None = None) -> dict:
    """See the module docstring. `strip_prefixes`: name prefixes one side adds (`Vault` in `VaultAddEditState`
    for `AddEditState`), dropped before normalized matching on both sides."""
    _PREFIXES[:] = [p.lower() for p in strip_prefixes or ()]
    try:
        return _parity(source_db, target_db, mapping, fuzzy)
    finally:
        _PREFIXES.clear()


def _parity(source_db: str, target_db: str, mapping: dict | None, fuzzy: bool) -> dict:
    S, T = load(source_db), load(target_db)
    mapping = mapping or {}
    exact_ix, norm_ix, comp_ix, all_short = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    bag_ix = defaultdict(list)
    t_explicit: dict = {}
    for is_type, coll in ((True, T["types"]), (False, T["top"])):
        for fq, sym in coll.items():
            c, sh = _cat(sym, is_type), _short(fq)
            exact_ix[(c, sh)].append(fq)
            norm_ix[(c, key(sh))].append(fq)
            bag_ix[(c, _bag(sh))].append(fq)
            all_short[sh].append(fq)
            if sym["composable"]:
                comp_ix[norm(sh)].append(fq)
            x = _explicit(sym)          # "Port of: FooView" on the target side
            if x:
                t_explicit.setdefault(x, fq)
                t_explicit.setdefault(_short(x), fq)

    out = {"source": source_db, "target": target_db, "matched": [], "missing": [], "unknown": [], "platform_only": [],
           "skipped": 0}

    def bucket(sym):
        if sym["file"] in S["err_files"]:
            return "unknown"
        pl = set(sym.get("platforms") or ())
        if pl and T["platforms"] and not (pl & T["platforms"]):
            return "platform_only"
        return "missing"

    def find(fq, sym, c, has_body):
        sh = _short(fq)
        for want in (mapping.get(fq), mapping.get(sh), _explicit(sym)):
            if want:
                hit = (want if want in T["types"] or want in T["top"] else None) or (all_short.get(_short(want)) or [None])[0]
                if hit:
                    return hit, "explicit"
        hit = t_explicit.get(fq) or t_explicit.get(sh)
        if hit:
            return hit, "explicit"
        if exact_ix.get((c, sh)):
            return exact_ix[(c, sh)][0], "exact"
        if norm_ix.get((c, key(sh))):
            return norm_ix[(c, key(sh))][0], "normalized"
        if len(_words(sh)) >= 3 and bag_ix.get((c, _bag(sh))):        # `AddEditFolderView` / `FolderAddEditScreen`
            return bag_ix[(c, _bag(sh))][0], "normalized"
        if has_body and comp_ix.get(norm(sh)):            # SwiftUI view -> @Composable function
            return comp_ix[norm(sh)][0], "normalized"
        return None, None

    pending, tmap = [], {}
    t_nested = defaultdict(list)                       # target type -> its nested types
    for fq in T["types"]:
        if "." in fq and fq.rsplit(".", 1)[0] in T["types"]:
            t_nested[fq.rsplit(".", 1)[0]].append(fq)
    for is_type, coll in ((True, S["types"]), (False, S["top"])):
        for fq, sym in sorted(coll.items(), key=lambda kv: (kv[0].count("."), kv[0])):     # outer types first
            c = _cat(sym, is_type)
            owner = fq.rsplit(".", 1)[0] if is_type and "." in fq and fq.rsplit(".", 1)[0] in S["types"] else None
            if owner:
                # a nested type (`State.FormField`) is looked for inside its owner's counterpart; elsewhere only by
                # its exact name, so a generic `Keys` / `FieldType` does not meet an unrelated type
                hit, how = None, None
                inner = t_nested.get(tmap.get(owner), [])
                sh = _short(fq)
                for t in inner:
                    if _short(t) == sh:
                        hit, how = t, "exact"
                        break
                else:
                    for t in inner:
                        if key(_short(t)) == key(sh):
                            hit, how = t, "normalized"
                            break
                if not hit:
                    h2, how2 = find(fq, sym, c, False)
                    if how2 in ("explicit", "exact"):
                        hit, how = h2, how2
            else:
                hit, how = find(fq, sym, c, is_type and "body" in S["members"].get(fq, {}))
            if hit:
                if is_type:
                    tmap[fq] = hit
                out["matched"].append(_row(sym, hit, how))
            else:
                pending.append((c, fq, sym, is_type, owner))
    # fuzzy, word level: same category and UI-suffix flag, same words but one shortened or pluralised
    # (`ItemList` / `ItemListing`, `Config` / `Configuration`, `PendingLogins` / `PendingLogin`); a word more or less
    # (`LoginTOTPState` / `LoginState`) is not a match
    pool = defaultdict(list)
    if fuzzy:
        for (c, k), fqs in norm_ix.items():
            w = _words(_short(fqs[0]))
            if w:
                pool[(c, k.endswith("|ui"), w[0], w[-1])].append((w, fqs[0]))
    for c, fq, sym, is_type, owner in pending:
        w = _words(_short(fq))
        hit = None
        if fuzzy and len(w) >= 2 and not owner:
            for tw, tfq in pool.get((c, key(_short(fq)).endswith("|ui"), w[0], w[-1]), ()):
                if _near(w, tw):
                    hit = tfq
                    break
        if hit:
            if is_type:
                tmap[fq] = hit
            out["matched"].append(_row(sym, hit, "fuzzy"))
        else:
            out[bucket(sym)].append(_row(sym, None, None))
    # members of matched types
    t_member_owner = defaultdict(set)                 # normalized member name -> target owners
    for o, ms in T["members"].items():
        for k in ms:
            t_member_owner[norm_member(k)].add(o)
    for sfq, tfq in tmap.items():
        tm = dict(T["members"].get(tfq, {}))
        for nt in t_nested.get(tfq, ()):               # Kotlin sealed-class actions / events: `data object LockClick`
            tm.setdefault(_short(nt), T["types"][nt])
        tm_norm = {norm_member(k): k for k in tm}
        for nm, sym in sorted(S["members"].get(sfq, {}).items()):
            if nm in ("body", "init", "deinit", "hash", "description", "encode", "hashValue") or sym["override"]:
                out["skipped"] += 1          # lifecycle / platform overrides (viewDidLoad, onCreate) and boilerplate
                continue
            if nm in tm:
                out["matched"].append(_row(sym, f"{tfq}.{nm}", "exact"))
            elif norm_member(nm) in tm_norm:
                out["matched"].append(_row(sym, f"{tfq}.{tm_norm[norm_member(nm)]}", "normalized"))
            elif len(_words(nm)) >= 3 and len(t_member_owner.get(norm_member(nm), ())) == 1:
                # a specific name (three words or more) declared on exactly one other target type: moved there
                o = next(iter(t_member_owner[norm_member(nm)]))
                hit = next(k for k in T["members"][o] if norm_member(k) == norm_member(nm))
                out["matched"].append(_row(sym, f"{o}.{hit}", "moved"))
            else:
                out[bucket(sym)].append(_row(sym, None, None, owner_match=tfq))
    counts = defaultdict(int)
    for r in out["matched"]:
        counts[r["confidence"]] += 1
    out["summary"] = {"source_symbols": len(out["matched"]) + len(out["missing"]) + len(out["unknown"]) +
                      len(out["platform_only"]), "matched": dict(counts), "skipped_overrides": out["skipped"],
                      "skipped_test_support": S["support"],
                      "missing": len(out["missing"]), "unknown": len(out["unknown"]),
                      "platform_only": len(out["platform_only"])}
    return out


def _row(sym, target, how, owner_match=None) -> dict:
    r = {"symbol": sym["fqn"], "kind": sym["kind"], "file": sym["file"], "line": sym["line"],
         "module": _group(sym["file"])}
    if target:
        r.update(target=target, confidence=how)
    if owner_match:
        r["owner_matched"] = owner_match
    return r


def _group(file: str) -> str:
    """Directory group: two levels, after a Gradle `src/<set>/<java|kotlin>/` and its package path."""
    f = file or ""
    m = re.search(r"(?:^|/)src/\w+/(?:java|kotlin)/(.*)$", f)
    if m:
        pkg = m.group(1).split("/")[:-1]
        head = f[: m.start()].strip("/")
        tail = "/".join(pkg[3:5] if len(pkg) > 3 else pkg[-2:])
        return "/".join(x for x in (head, tail) if x)
    parts = Path(f).parts
    return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if parts else "")


def render(res: dict, max_items: int = 200) -> str:
    s = res["summary"]
    m = s["matched"]
    lines = [f"parity: {res['source']} -> {res['target']}",
             f"{s['source_symbols']} source symbols: {sum(m.values())} matched "
             f"({', '.join(f'{k} {v}' for k, v in sorted(m.items()))}), {s['missing']} missing, "
             f"{s['unknown']} unknown (syntax errors), {s['platform_only']} platform-only"]
    by = defaultdict(list)
    for r in res["missing"]:
        by[r["module"]].append(r)
    if by:
        lines += ["", f"== MISSING in target: {s['missing']}"]
        shown = 0
        for mod in sorted(by, key=lambda k: -len(by[k])):
            lines.append(f"  {mod or '.'}  ({len(by[mod])})")
            for r in by[mod]:
                if shown >= max_items:
                    break
                shown += 1
                lines.append(f"    {r['symbol']}  [{r['kind']}] {r['file']}:{r['line']}")
        if shown < s["missing"]:
            lines.append(f"  ... {s['missing'] - shown} more (--json for all)")
    if res["unknown"]:
        lines += ["", f"== UNKNOWN (file parsed with syntax errors): {s['unknown']}"]
        lines += [f"    {r['symbol']}  {r['file']}:{r['line']}" for r in res["unknown"][:max_items]]
    return "\n".join(lines)
