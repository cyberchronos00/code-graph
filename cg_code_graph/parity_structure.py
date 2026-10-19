"""`cg parity --structure` (#93): pairs symbols whose names do not match by what they use, and learns per-project
rename rules from the pairs found. See docs/parity.md, "Matching by structure".

Each still-missing source symbol and each unmatched target symbol gets a feature set from its declaration span and
its outgoing edges:

  l10n:<key>   localization keys (`Localizations.creatingAccount`, `L10n.actionCancel`, `R.string.creating_account`,
               `BitwardenString.x`, `CommonStrings.x`), case and `_` folded so the two platforms' keys meet
  str:<text>   string literals of 5 to 80 characters (URL paths, analytics events, keys), leading `/` dropped
  http:<path>  HTTP endpoints the code calls (HTTP_CALLS), path parameters folded to `{}`
  sym:<target> symbols it calls / instantiates / navigates to that are already paired (the source side is
               rewritten to the target's name, so a call to a paired API on both sides is a shared feature)
  call:<name>  the names of the members it calls (two words or more)
  res:<name>   image and raw resources it uses (`Image("ic_lock")`, `UIImage(named:)`, `Asset.Images.icLock`,
               `Image(.icLock)`, `R.drawable.ic_lock`, `Res.drawable.x`), case and `_` folded; colors are design
               tokens shared by every screen and are left out
  w:<stem>     the stemmed content words of its own name

A use is specific when it appears in at most SPECIFIC_FILES files on each side (files, not symbols, since a type's span
holds its members'); a pair whose shared uses are all generic needs two shared name words, so two screens that only
share `l10n:cancel` / `l10n:yes` and one word do not pair.

Types that are still missing after that are paired by their declared fields (`fields`): stored properties, each under
its wire name when one is declared (Swift `CodingKeys` raw values, Kotlin `@SerialName` / `@SerializedName` /
`@Json(name =)`), and enum cases, kept apart from properties; case and `_` folded. A pair needs MIN_FIELDS shared
fields, an IDF-weighted overlap of at least MIN_FIELD_SCORE, a shared name word or a higher overlap, mutual best and
the margin.

A pair is accepted when it shares a use (any feature but w:) and either two uses or two name words, its IDF-weighted
cosine is at least MIN_SCORE, both symbols are each other's best candidate, the runner-up scores below MARGIN times
the best, and category, UI-ness and architecture role agree (`compatible`). Learned rules come from the
non-exact type pairs: a word tail (`Coordinator` -> `Navigation`) or head (`BW` -> `Bitwarden`) rewrite seen at least
MIN_SUPPORT times, two thirds of the time for that source tail, is applied to the remaining missing types and
top-level symbols. Every match carries its evidence; name matches are left as they are."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

MIN_SCORE = 0.3
MIN_SHARED = 2
MARGIN = 0.8                 # the runner-up must score below MARGIN * best
MIN_SUPPORT = 2
MAX_DF = 40
SPECIFIC_FILES = 3           # a shared use found in more files than this on either side is generic (`l10n:cancel`)
MIN_FIELDS = 3
MIN_FIELD_SCORE = 0.5
PREVIEW = re.compile(r"Preview")           # Compose @Preview functions / PreviewParameterProviders, SwiftUI previews                  # features shared by more symbols than this do not propose candidates
L10N = re.compile(r"\b(?:Localizations|L10n|R\.string|BitwardenString|CommonStrings|[A-Z]\w*Strings)\.([A-Za-z_]\w*)")
STR = re.compile(r'"((?:[^"\\\n]|\\.){5,80})"')
RES = re.compile(r"""\b(?:UI|NS)?Image\(\s*(?:named:\s*)?"([^"\n]+)"|\bAsset\.(?:Images|Assets)\.(\w+)|"""
                 r"""\bImage\(\s*\.(\w+)\s*\)|\b(?:R|Res)\.(?:drawable|mipmap|raw)\.(\w+)""")
WIRE = re.compile(r'@(?:SerialName|SerializedName|JsonProperty|Json\(\s*name\s*=)\s*\(?\s*(?:value\s*=\s*)?"([^"\n]+)"')
RAW_CASE = re.compile(r'\bcase\s+`?(\w+)`?\s*=\s*"([^"\n]+)"')
EDGE_KINDS = ("CALLS", "INSTANTIATES", "NAVIGATES_TO", "HTTP_CALLS", "USES_VALUE", "REFERENCES_FN", "USES_TYPE")


UI_WORDS = {"view", "screen", "sheet", "page", "fragment", "activity", "controller", "vc", "content", "dialog", "card",
            "row", "cell", "button", "item", "items", "field", "list", "menu", "toolbar", "banner"}
# role words of an architecture: a type ending in one only pairs with a type ending in the same group
ROLES = [{"processor", "viewmodel", "vm"}, {"coordinator", "navigation", "navigator", "route", "destination", "router"},
         {"state"}, {"action"}, {"effect", "event"}, {"request", "api", "service", "client", "response", "json"},
         {"repository", "manager", "service", "store", "source"}, {"model", "json", "data", "type", "option"}]
ROLE_OF = defaultdict(set)
for _i, _g in enumerate(ROLES):
    for _w in _g:
        ROLE_OF[_w].add(_i)


def _role(fq: str) -> set:
    ws = _words(_short(fq))
    if len(ws) >= 2 and ws[-2:] == ["view", "model"]:
        return ROLE_OF["viewmodel"]
    return ROLE_OF.get(ws[-1], set()) if ws else set()


def is_ui(fq: str, sym: dict, members: dict) -> bool:
    ws = _words(_short(fq))
    return bool(sym.get("composable") or "body" in members.get(fq, {}) or (ws and ws[-1] in UI_WORDS and ws[-2:] != ["view", "model"]))


def compatible(fq, sc, tq, tc, s_ui, t_ui) -> bool:
    """Same category (a SwiftUI view type also meets a @Composable function), the same UI-ness for types and
    top-level symbols, and no clash of architecture roles (`Coordinator` vs `State`)."""
    if sc != tc and not ({sc, tc} == {"type", "func"} and s_ui and t_ui):
        return False
    if "type" in (sc, tc) and s_ui != t_ui:
        return False
    if sc == tc == "type":
        a, b = _role(fq), _role(tq)
        if a and b and not (a & b):
            return False
    return True


STOP = {"handle", "click", "clicked", "tap", "tapped", "pressed", "get", "set", "is", "on", "did", "will", "the", "to",
        "for", "with", "and", "of", "default", "impl", "internal", "receive", "result", "update", "show", "new", "bitwarden"}
STOP |= UI_WORDS | {w for g in ROLES for w in g} | {"view", "model"}


def _stem(w: str) -> str:
    for suf in ("ments", "ment", "ings", "ing", "ions", "ion", "ers", "er", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def name_words(fq: str) -> set:
    """The stemmed content words of a name (role, UI and event words left out): `DeviceManagementProcessor` and
    `ManageDevicesViewModel` share `devic` and `manag`."""
    return {"w:" + _stem(w) for w in _words(_short(fq)) if w not in STOP and len(w) > 2}


def _fold(s: str) -> str:
    return re.sub(r"[_\W]", "", s).lower()


def _words(name: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", name or "")]


class Side:
    """One graph: its symbols' spans, outgoing edges and source lines."""

    def __init__(self, G: dict):
        self.G = G
        c = sqlite3.connect(G["db"])
        c.row_factory = sqlite3.Row
        self.root = G["root"]
        self.lines = G["lines_of"]
        self.fqn_of, self.span = {}, {}
        self.by_file = defaultdict(list)
        for r in c.execute("SELECT id, fqn, name, file, line, end_line FROM nodes WHERE file IS NOT NULL AND line IS NOT NULL"):
            self.fqn_of[r["id"]] = (r["fqn"] or r["name"] or "").replace(".Companion.", ".")
            self.by_file[r["file"]].append((r["line"], r["end_line"] or r["line"], r["id"]))
            self.span[r["id"]] = (r["file"], r["line"], r["end_line"] or r["line"])
        self.out = defaultdict(list)
        q = f"SELECT src, dst, kind FROM edges WHERE kind IN ({','.join('?' * len(EDGE_KINDS))})"
        for r in c.execute(q, EDGE_KINDS):
            self.out[r["src"]].append((r["dst"], r["kind"]))
        self.http = {r["id"]: (r["name"] or r["id"]) for r in c.execute("SELECT id, name FROM nodes WHERE kind='http'")}
        self.decl = defaultdict(list)          # owner fqn -> [(name, kind, file, line)] of stored fields / enum cases
        for r in c.execute("SELECT kind, fqn, file, line, attrs FROM nodes WHERE kind IN ('field', 'enum_case') "
                           "AND fqn IS NOT NULL AND file IS NOT NULL"):
            if ".Companion." in r["fqn"] or "." not in r["fqn"]:
                continue
            if r["kind"] == "field" and (json.loads(r["attrs"] or "{}").get("property") or "stored") != "stored":
                continue
            o, nm = r["fqn"].rsplit(".", 1)
            self.decl[o].append((nm, r["kind"], r["file"], r["line"]))
        c.close()

    def fields(self, fq: str) -> set:
        """`field:<wire name>` for each stored property declared directly on the type: the explicit wire name
        (`@SerialName("item_title")`, a `CodingKeys` case `title = "item_title"`) or else the property name; `case:<name>`
        for each enum case, so an enum of field names does not meet the type that has those fields."""
        raw = {}
        for nm, kind, file, line in self.decl.get(fq + ".CodingKeys", ()):
            m = RAW_CASE.search(self.text(file, line, line))
            if m:
                raw[m.group(1)] = m.group(2)
        out = set()
        for nm, kind, file, line in self.decl.get(fq, ()):
            wire = raw.get(nm)
            if not wire and kind == "field":
                m = WIRE.search(self.text(file, line, line))
                if not m and line > 1:            # an annotation on its own line above
                    prev = self.text(file, line - 1, line - 1)
                    m = None if re.search(r"\b(?:val|var|let)\s", prev) else WIRE.search(prev)
                wire = m.group(1) if m else None
            k = _fold(wire or nm)
            if len(k) > 1:
                out.add(("case:" if kind == "enum_case" else "field:") + k)
        return out

    def text(self, file: str, a: int, b: int) -> str:
        if file not in self.lines:
            try:
                self.lines[file] = (Path(self.root) / file).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self.lines[file] = []
        return "\n".join(self.lines[file][a - 1:b])

    def features(self, sym: dict, rename) -> set:
        file, a, b = self.span.get(sym["id"], (sym["file"], sym["line"], sym["line"]))
        f = set()
        if file and a:
            src = self.text(file, a, b)
            f |= {"l10n:" + _fold(m) for m in L10N.findall(src)}
            for m in RES.finditer(src):
                f.add("res:" + _fold(next(g for g in m.groups() if g)))
            src = RES.sub(" ", src)
            for m in STR.findall(src):
                s = m.strip().lstrip("/")
                if len(s) >= 5 and re.search(r"[A-Za-z]{3}", s) and not re.search(r"\\\(|\$\{|%[@sd]", s):
                    f.add("str:" + re.sub(r"\s+", " ", s.lower()))
            for la, lb, nid in self.by_file.get(file, ()):
                if a <= la <= b:
                    for dst, kind in self.out.get(nid, ()):
                        if kind == "HTTP_CALLS" or dst in self.http:
                            p = re.sub(r"\{[^}]*\}|(?<=/):\w+", "{}", self.http.get(dst, dst).split(" ")[-1])
                            f.add("http:" + p.lstrip("/").lower())
                            continue
                        dq = self.fqn_of.get(dst)
                        t = rename(dq)
                        if t:
                            f.add("sym:" + t)
                        # the called member's own name (two words or more): `getDevices` on an AuthService and on
                        # an AuthRepository is the same call even when the owners are not paired
                        nm = _short(dq or "").split("(")[0]
                        if kind == "CALLS" and len(_words(nm)) >= 2:
                            f.add("call:" + re.sub(r"^(get|fetch|load)(?=[a-z])", "", _fold(nm)))
        return f


def match(S: dict, T: dict, out: dict, sym_of: dict, cat_of: dict, t_syms: dict, t_cat: dict, learn: bool = True) -> dict:
    ui_s = {fq: is_ui(fq, sym, S["members"]) for fq, sym in sym_of.items()}
    ui_t = {fq: is_ui(fq, sym, T["members"]) for fq, sym in t_syms.items()}
    S["_ui"], T["_ui"] = ui_s, ui_t
    """Adds `learned` and `structure` matches to `out` (rows moved out of `missing`). `sym_of` / `cat_of`: source fqn
    -> symbol / category for every compared source symbol; `t_syms` / `t_cat` the same for the target."""
    used = {r["target"] for r in out["matched"]}
    rules: list = []
    if learn:
        rules = _learn(out["matched"], cat_of)
        _apply_rules(rules, out, sym_of, cat_of, t_syms, t_cat, used)
    _structure(S, T, out, sym_of, cat_of, t_syms, t_cat, used)
    if learn:
        more = [r for r in _learn(out["matched"], cat_of) if r["from"] not in {x["from"] for x in rules}]
        if more:
            _apply_rules(more, out, sym_of, cat_of, t_syms, t_cat, used)
            rules += more
    return {"rules": rules}


def _short(fq: str) -> str:
    return fq.rsplit(".", 1)[-1]


def _learn(matched: list, cat_of: dict) -> list:
    """Word tail / head rewrites from the non-exact type and top-level pairs."""
    seen, total = Counter(), Counter()
    for r in matched:
        if r.get("confidence") in ("exact", "moved", None) or cat_of.get(r["symbol"]) not in ("type", "func", "const"):
            continue
        if r["symbol"].count(".") != r["target"].count(".") and "." in r["symbol"]:
            continue
        sw, tw = _words(_short(r["symbol"])), _words(_short(r["target"]))
        p = 0
        while p < min(len(sw), len(tw)) and sw[p] == tw[p]:
            p += 1
        q = 0
        while q < min(len(sw), len(tw)) - p and sw[-1 - q] == tw[-1 - q]:
            q += 1
        if p and not q and 0 < len(sw) - p <= 2 and 0 < len(tw) - p <= 2:
            k = ("tail", " ".join(sw[p:]), " ".join(tw[p:]))
        elif q and not p and 0 < len(sw) - q <= 2 and 0 < len(tw) - q <= 2:
            k = ("head", " ".join(sw[:len(sw) - q]), " ".join(tw[:len(tw) - q]))
        else:
            continue
        seen[k] += 1
        total[(k[0], k[1])] += 1
    out = []
    for (where, a, b), n in seen.most_common():
        if n >= MIN_SUPPORT and n * 3 >= total[(where, a)] * 2 and not any(x["at"] == where and x["from"] == a for x in out):
            out.append({"at": where, "from": a, "to": b, "support": n})
    return out


def _apply_rules(rules, out, sym_of, cat_of, t_syms, t_cat, used):
    if not rules:
        return
    t_ix = defaultdict(list)
    for fq in t_syms:
        if fq not in used:
            t_ix[(t_cat[fq], " ".join(_words(_short(fq))))].append(fq)
    keep = []
    for row in out["missing"]:
        fq = row["symbol"]
        sw = " ".join(_words(_short(fq)))
        hit = None
        if "owner_matched" not in row:
            for r in rules:
                if r["at"] == "tail" and sw.endswith(" " + r["from"]):
                    want = sw[: -len(r["from"])] + r["to"]
                elif r["at"] == "head" and sw.startswith(r["from"] + " "):
                    want = r["to"] + sw[len(r["from"]):]
                else:
                    continue
                c = [t for t in t_ix.get((cat_of.get(fq), want), ()) if t not in used]
                if len(c) == 1:
                    hit = (c[0], r)
                    break
        if hit:
            used.add(hit[0])
            r = hit[1]
            out["matched"].append(dict(row, target=hit[0], confidence="learned",
                                       evidence=[f"rule {r['at']} {r['from']!r} -> {r['to']!r} (seen {r['support']}x)"]))
        else:
            keep.append(row)
    out["missing"] = keep


def _by_fields(sS, sT, out, sym_of, cat_of, t_syms, t_cat, used, S, T):
    """Types still missing, paired by the fields they declare (`ViewItemState` / `VaultItemState`, a Swift
    `Codable` model and its Kotlin `@Serializable` port)."""
    src = {r["symbol"]: sS.fields(r["symbol"]) for r in out["missing"] if cat_of.get(r["symbol"]) == "type"
           and "owner_matched" not in r}
    src = {k: v for k, v in src.items() if len(v) >= MIN_FIELDS and _short(k) != "CodingKeys"}
    tgt = {fq: sT.fields(fq) for fq in t_syms if t_cat.get(fq) == "type" and fq not in used}
    tgt = {k: v for k, v in tgt.items() if len(v) >= MIN_FIELDS and _short(k) != "CodingKeys"}
    if not src or not tgt:
        return
    df = Counter()
    for fs in list(src.values()) + list(tgt.values()):
        df.update(fs)
    n = len(src) + len(tgt)
    w = {f: math.log(1 + n / c) for f, c in df.items()}
    inv = defaultdict(list)
    for fq, fs in tgt.items():
        for f in fs:
            inv[f].append(fq)

    def score(fq, t):
        a, b = src[fq], tgt[t]
        sh = a & b
        if len(sh) < MIN_FIELDS:
            return 0.0, sh
        sc = sum(w[f] for f in sh) / sum(w[f] for f in a | b)
        words = name_words(fq) & name_words(t)
        if sc < MIN_FIELD_SCORE or (not words and sc < 0.75):
            return 0.0, sh
        return sc, sh | words

    best = {}
    for fq, fs in src.items():
        cands = {t for f in fs if df[f] <= MAX_DF for t in inv[f]
                 if compatible(fq, "type", t, "type", S["_ui"].get(fq), T["_ui"].get(t))}
        scored = sorted(((score(fq, t), t) for t in cands), key=lambda x: -x[0][0])
        if scored and scored[0][0][0] > 0 and (len(scored) == 1 or scored[1][0][0] < MARGIN * scored[0][0][0]):
            best[fq] = (scored[0][1], scored[0][0][0], scored[0][0][1])
    by_t = defaultdict(list)
    for fq, (t, sc, sh) in best.items():
        by_t[t].append((sc, fq))
    keep = []
    for row in out["missing"]:
        got = best.get(row["symbol"])
        if got and max(by_t[got[0]])[1] == row["symbol"] and got[0] not in used:
            t, sc, sh = got
            used.add(t)
            ev = sorted(sh, key=lambda f: (f.startswith("w:"), -w.get(f, 0), f))[:6]
            out["matched"].append(dict(row, target=t, confidence="fields", score=round(sc, 2), evidence=ev))
        else:
            keep.append(row)
    out["missing"] = keep


def _structure(S, T, out, sym_of, cat_of, t_syms, t_cat, used):
    sS, sT = Side(S), Side(T)
    pairs = {r["symbol"]: r["target"] for r in out["matched"]}
    src_f, rows = {}, {r["symbol"]: r for r in out["missing"]}
    for row in out["missing"]:
        sym = sym_of.get(row["symbol"])
        if sym is not None:
            src_f[row["symbol"]] = sS.features(sym, lambda x: pairs.get(x)) | name_words(row["symbol"])
    tgt_f = {fq: sT.features(sym, lambda x: x) | name_words(fq) for fq, sym in t_syms.items()
             if fq not in used and not PREVIEW.search(_short(fq))}
    src_f = {fq: f for fq, f in src_f.items() if not PREVIEW.search(_short(fq))}
    df = Counter()
    for fs in list(src_f.values()) + list(tgt_f.values()):
        df.update(fs)
    spread = [defaultdict(set), defaultdict(set)]          # feature -> files having it, per side
    for k, (fsets, syms) in enumerate(((src_f, sym_of), (tgt_f, t_syms))):
        for fq, fs in fsets.items():
            for f in fs:
                spread[k][f].add(syms[fq]["file"])
    specific = {f for f in df if len(spread[0][f]) <= SPECIFIC_FILES and len(spread[1][f]) <= SPECIFIC_FILES}
    n = max(1, len(src_f) + len(tgt_f))
    w = {f: math.log(1 + n / c) for f, c in df.items()}
    inv = defaultdict(list)
    for fq, fs in tgt_f.items():
        for f in fs:
            if df[f] <= MAX_DF:
                inv[f].append(fq)

    def score(a, b):
        sh = a & b
        nw = sum(1 for f in sh if not f.startswith("w:"))
        # what they use must carry the match: two shared uses, or one use and two shared name words
        if nw < 1 or (nw < MIN_SHARED and len(sh) - nw < 2):
            return 0.0, sh
        if len(sh) - nw < 2 and not any(f in specific for f in sh if not f.startswith("w:")):
            return 0.0, sh                      # only generic uses (`l10n:cancel`, `l10n:yes`) and one name word
        return sum(w[f] for f in sh) / math.sqrt(sum(w[f] for f in a) * sum(w[f] for f in b)), sh

    best_s = {}
    for fq, fs in src_f.items():
        member = "owner_matched" in rows[fq]
        cands = {t for f in fs if df[f] <= MAX_DF for t in inv.get(f, ())
                 if (t_cat.get(t) == cat_of.get(fq) if member else
                     compatible(fq, cat_of.get(fq), t, t_cat.get(t), S["_ui"].get(fq), T["_ui"].get(t)))}
        scored = sorted(((score(fs, tgt_f[t]), t) for t in cands), key=lambda x: -x[0][0])
        if scored and scored[0][0][0] >= MIN_SCORE and (len(scored) == 1 or scored[1][0][0] < MARGIN * scored[0][0][0]):
            best_s[fq] = (scored[0][1], scored[0][0][0], scored[0][0][1])
    # mutual best: no other source symbol scores higher for the same target
    by_t = defaultdict(list)
    for fq, (t, sc, sh) in best_s.items():
        by_t[t].append((sc, fq))
    keep = []
    for row in out["missing"]:
        fq = row["symbol"]
        got = best_s.get(fq)
        if got and max(by_t[got[0]])[1] == fq and got[0] not in used:
            t, sc, sh = got
            used.add(t)
            ev = sorted(sh, key=lambda f: -w[f])[:6]
            out["matched"].append(dict(row, target=t, confidence="structure", score=round(sc, 2), evidence=ev))
        else:
            keep.append(row)
    out["missing"] = keep
    _by_fields(sS, sT, out, sym_of, cat_of, t_syms, t_cat, used, S, T)
