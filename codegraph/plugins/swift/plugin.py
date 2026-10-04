"""Swift language plugin (iOS / macOS apps, SwiftUI, Vapor).

Heuristic mode: a tree-sitter-swift syntax layer, so repositories index on Linux without Xcode or a Swift toolchain.
Declarations (classes, structs, enums, actors, protocols, extensions, functions, methods, initializers, SwiftUI `body`
properties) become nodes; calls are resolved by name: the enclosing type, its extensions and supertypes, then a
parameter / property type (`api.book(id)` with `let api: BooksAPI`), then a project-wide unique name. Every resolved
reference is labelled `heuristic`. Extensions are merged into the type they extend.

Exact mode (exact.py, indexstore.py): the compiler's index store (`swift build --enable-index-store`, run with
CODEGRAPH_SWIFT_INDEX=1 for a SwiftPM package, or an existing store / Xcode DerivedData via
CODEGRAPH_SWIFT_INDEX_STORE) read through libIndexStore replaces the call / constructor edges of every file the store
covers; files it does not cover keep heuristic edges, and `cg coverage` names the mode and the reason.

Framework facts read from the same syntax tree:
  entry points  `@main` types (main), `UIApplicationDelegate` / `UISceneDelegate` / `App` lifecycle callbacks,
                `BGTaskScheduler.shared.register(forTaskWithIdentifier:)` handlers (queue_job); tests: XCTest `test*`
                methods of XCTestCase subclasses (attrs.framework xctest) and Swift Testing `@Test` functions, a
                parameterized `@Test(arguments:)` included (swift-testing; display name, tags and traits from the
                attribute); `@Suite` types carry attrs.suite. Test files: Tests/, *Tests/ targets, or `import
                XCTest` / `import Testing`
  SwiftUI       `NavigationLink(destination: V())`, `.navigationDestination { V() }`, `.sheet` / `.fullScreenCover` /
                `.popover { V() }`, `TabView` children and the `WindowGroup` root, `navigationDestination(for: T.self)`
                cases matched to `NavigationLink(value: T.x)` / `router.navigate(to: .x)`: the target views become page nodes
                (`page:swift:<View>`, ROUTES_TO its `body`) with NAVIGATES_TO edges; UIKit
                `pushViewController(V(), ...)` / `present(V(), ...)` likewise
  HTTP clients  URLSession (`data(from:)`, `data(for:)`, `dataTask`, `upload(for:)`) with the URL built in the same
                function (`URL(string: "...")`, `appendingPathComponent("...")`, `"\\(baseURL)/..."` templates) and
                `httpMethod = "POST"`; Alamofire `AF.request(url, method: .post)` -> http:<METHOD> <path>
  Vapor         `app.get("orders", ":id") { }`, `routes.post("x", use: handler)`, `grouped("v1")` / `group("v1") { }`
                prefixes, middleware passed to `grouped(...)` (`User.authenticator()`, `User.guardMiddleware()`) as
                route guards, `RouteCollection.boot(routes:)` -> route:<METHOD> <uri>
  Moya          `TargetType` enums (baseURL + per-case path / method) -> one http node per case (HTTP_CALLS from the
                enum), `provider.request(.case)` / `requestPublisher` call sites -> HTTP_CALLS from the caller
  Fluent        `Model` classes with `static let schema = "todos"` -> table:todos (MAPS_TO_TABLE); migrations'
                `database.schema("todos")...create()` -> WRITES_TABLE; `Todo.query(on:)` / `Todo.find` -> READS_TABLE
                (WRITES_TABLE when the chain deletes / updates); `todo.save(on:)` / `.delete(on:)` -> WRITES_TABLE
  platforms     `#if os(iOS)` / `#elseif os(macOS)` / `#else` blocks feed the platform tags (docs/platforms.md)
"""
from __future__ import annotations

import os
import re
import time
from types import SimpleNamespace
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ...core.syntax_errors import tree_spans
from ...core.fsutil import keep_file
from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.paths import rules as path_rules
from ...core.plugin import GraphBuilder, LanguagePlugin, Project
from ..native.ts import TreeSitterMissing

VALUE_SITES = frozenset({"navigation_expression", "call_expression", "simple_identifier", "switch_statement",
                         "equality_expression", "property_declaration", "parameter"})

EXTS = (".swift",)
BRANCH_STOP = frozenset({"function_declaration", "init_declaration", "class_declaration", "protocol_declaration",
                         "computed_property", "subscript_declaration", "source_file", "property_declaration"})
VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}
TEST_IMPORTS = {"XCTest", "Testing"}
TEST_PATH = re.compile(r"(^|/)(Tests?|\w+Tests|\w+UITests)/|Tests?\.swift$")
TEMPLATE = re.compile(r"\\\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
LIFECYCLE = re.compile(r"^(application\w*|scene\w*|sceneDid\w*|sceneWill\w*|viewDidLoad|viewWillAppear|viewDidAppear|"
                       r"userNotificationCenter|perform|handle|body)$")
LIFECYCLE_BASES = {"UIApplicationDelegate", "UIWindowSceneDelegate", "UISceneDelegate", "NSApplicationDelegate",
                   "UIViewController", "AppIntent", "Widget", "WKApplicationDelegate"}
URLSESSION = re.compile(r"\b(data|dataTask|upload|uploadTask|download|downloadTask|bytes)\s*\(\s*(from|for|with)\s*:")
# Sequence / Collection / Dictionary / Optional / String members: a call of one of these on a receiver of unknown type
# is not resolved to a same-named project method by the unique-name fallback
STDLIB_METHODS = {
    "first", "last", "filter", "reduce", "sorted", "sort", "enumerated", "compactMap", "flatMap", "forEach",
    "contains", "append", "insert", "remove", "removeAll", "removeFirst", "removeLast", "joined", "reversed",
    "prefix", "suffix", "dropFirst", "dropLast", "min", "max", "split", "firstIndex", "lastIndex", "mapValues",
    "compactMapValues", "merge", "merging", "updateValue", "index", "starts", "allSatisfy", "lowercased",
    "uppercased", "replacingOccurrences", "trimmingCharacters", "components", "hasPrefix", "hasSuffix", "zip",
    "shuffled", "partition", "subtracting", "union", "intersection", "formUnion", "isEmpty", "count", "makeIterator",
    "withContiguousStorageIfAvailable", "encode", "decode", "description", "hash", "elementsEqual"}
# selectors (name + argument labels) of SDK members commonly called on receivers cg cannot type: a call with one of
# these selectors on an unknown receiver is not bound to a same-selector project method (#70)
SDK_SELECTORS = {
    "contains(_:)", "contains(where:)", "first(where:)", "firstIndex(of:)", "firstIndex(where:)", "remove(at:)",
    "append(_:)", "append(contentsOf:)", "insert(_:at:)", "insert(_:)", "remove(_:)", "index(of:)", "index(_:offsetBy:)",
    "filter(_:)", "map(_:)", "compactMap(_:)", "sorted(by:)", "joined(separator:)", "split(separator:)",
    "accessibilityIdentifier(_:)", "accessibilityLabel(_:)", "accessibilityHint(_:)", "accessibilityValue(_:)",
    "accessibilityAddTraits(_:)", "accessibilityHidden(_:)", "font(_:)", "weight(_:)", "bold()", "bold(_:)",
    "italic()", "foregroundColor(_:)", "foregroundStyle(_:)", "background(_:)", "overlay(_:)", "padding(_:)",
    "padding()", "padding(_:_:)", "opacity(_:)", "tint(_:)", "tag(_:)", "id(_:)", "frame(width:height:)",
    "disabled(_:)", "hidden()", "navigationTitle(_:)", "offset(x:y:)", "scaleEffect(_:)", "rotationEffect(_:)",
    "clipShape(_:)", "cornerRadius(_:)", "shadow(radius:)", "animation(_:value:)", "transition(_:)", "zIndex(_:)",
    "onAppear(perform:)", "onDisappear(perform:)", "onTapGesture(perform:)", "task(_:)", "task(id:_:)",
    "resume()", "resume(returning:)", "resume(throwing:)", "resume(with:)", "yield(_:)", "finish()",
    "post(name:object:)", "post(name:object:userInfo:)", "post(_:)", "addObserver(_:selector:name:object:)",
    "removeObserver(_:)", "open(_:)", "open(_:options:completionHandler:)", "canOpenURL(_:)",
    "draw(_:at:)", "draw(_:in:)", "draw(_:at:anchor:)", "fill(_:with:)", "stroke(_:with:lineWidth:)",
    "read(_:maxLength:)", "write(_:maxLength:)", "cancel()", "send(_:)", "sink(receiveValue:)",
    "set(_:forKey:)", "value(forKey:)", "object(forKey:)", "removeObject(forKey:)", "string(forKey:)",
    "async(execute:)", "asyncAfter(deadline:execute:)", "sync(execute:)", "sleep(nanoseconds:)", "sleep(for:)",
    "dataTask(with:)", "data(from:)", "data(for:)", "encode(_:)", "decode(_:from:)", "start()", "stop()",
    "reloadData()", "dismiss(animated:completion:)", "present(_:animated:completion:)", "layoutIfNeeded()",
    "setNeedsLayout()", "addSubview(_:)", "removeFromSuperview()", "becomeFirstResponder()", "resignFirstResponder()",
}
# argument labels of standard-library collection members (`xs.first(where:)`, `s.split(separator:)`): a call of a
# STDLIB_METHODS name with only these labels stays unbound on an unknown receiver; other labels name a project method
STDLIB_LABELS = {"_", "where", "at", "by", "of", "into", "separator", "with", "contentsOf", "keepingCapacity",
                 "maxSplits", "omittingEmptySubsequences", "offsetBy", "limitedBy", "from", "to", "through",
                 "uniquingKeysWith", "isIncluded", "options", "range", "locale", "in", "forKey"}
PRESENT = {"sheet", "fullScreenCover", "popover", "navigationDestination"}
CONTAINER_VIEWS = {"Text", "Image", "Label", "Button", "VStack", "HStack", "ZStack", "List", "Group", "NavigationStack",
                   "NavigationView", "ScrollView", "Form"}
NAV_RECV = re.compile(r"(?i)path|router|navigat|stack|coordinator")
OS_PLATFORM = {"iOS": "ios", "iPadOS": "ios", "watchOS": "watchos", "tvOS": "tvos", "visionOS": "visionos", "macOS": "macos",
               "OSX": "macos", "Linux": "linux", "Windows": "windows", "Android": "android", "WASI": "web"}
# `#if canImport(X)`: the SDK framework implies the platform (UIKit: iOS family, AppKit: macOS)
# (Apple-only frameworks: iOS or macOS; swift-corelibs FoundationNetworking: not Apple)
IMPORT_PLATFORM = {"UIKit": ("ios", "tvos", "visionos"), "WatchKit": "watchos",
                   "MobileCoreServices": ("ios", "tvos", "watchos", "visionos"), "AppKit": "macos", "Cocoa": "macos",
                   "Glibc": "linux", "Musl": "linux", "WinSDK": "windows", "ucrt": "windows", "Android": "android",
                   "WASILibc": "web"}
APPLE_ONLY = {"Darwin", "Security", "Network", "SystemConfiguration", "UniformTypeIdentifiers", "Combine", "SwiftUI",
              "CoreServices", "CoreLocation", "CoreData", "CoreGraphics", "CoreFoundation", "ObjectiveC", "os",
              "StoreKit", "AVFoundation", "Metal", "CryptoKit", "UserNotifications", "WidgetKit"}
TYPE_DECLS = ("class_declaration", "protocol_declaration")
# a read of one of these on a receiver of unknown type is not bound to a project computed property by name (#72):
# far more often a stored or SDK property of the same name
COMMON_PROPS = {
    "id", "name", "title", "value", "count", "first", "last", "isEmpty", "description", "debugDescription", "text",
    "url", "type", "kind", "data", "date", "key", "label", "image", "color", "font", "state", "status", "items",
    "content", "message", "error", "result", "index", "size", "width", "height", "frame", "bounds", "view",
    "isEnabled", "isHidden", "isSelected", "isLoading", "rawValue", "hashValue", "body", "path", "host", "string",
    "startIndex", "endIndex", "indices", "keys", "values", "lowercased", "uppercased", "shared", "default"}
ACCESSOR_CLAUSES = {"computed_getter": "get", "computed_setter": "set", "computed_modify": "modify",
                    "willset_clause": "willSet", "didset_clause": "didSet"}
# a bare identifier under one of these parents is not a property read (declaration names, labels, members, types)
NOT_A_READ = {"navigation_suffix", "pattern", "lambda_parameter", "parameter", "value_argument_label",
              "function_declaration", "class_declaration", "protocol_declaration", "enum_entry", "import_declaration",
              "user_type", "type_identifier", "tuple_type_item", "capture_list_item", "value_binding_pattern", "switch_pattern",
              "key_path_expression", "attribute", "protocol_function_declaration", "typealias_declaration",
              "init_declaration", "property_declaration", "protocol_property_declaration", "inheritance_specifier",
              "macro_invocation", "external_macro_definition", "macro_declaration", "precedence_group_declaration"}
# an unknown receiver whose selector fits more project methods than this gets no candidate edges (#83 item 6)
MAX_CANDIDATES = 5
# standard-library mutating methods on a stored collection / Bool / Optional: `items.append(x)` writes `items` (#88)
MUTATING_METHODS = frozenset({
    "append", "appendContents", "insert", "remove", "removeAll", "removeFirst", "removeLast", "removeSubrange",
    "removeValue", "popFirst", "popLast", "replaceSubrange", "sort", "reverse", "shuffle", "swapAt", "toggle",
    "updateValue", "merge", "formUnion", "formIntersection", "formSymmetricDifference", "subtract", "negate",
    "move", "partition", "reserveCapacity"})
STATIC_MODS = {"static", "class"}
# a receiver whose SDK type is certain (`let r = UIGraphicsPDFRenderer(...)`, `var inside = false`,
# `Path { p in }`) does not reach a project extension of one of these other concrete SDK types: value types and
# final classes no SDK value of another type can be. `r.pdfData { }` does not reach `extension CGImage { func
# pdfData() }` (#83); an extension of a protocol or open class (`View`, `Reducer`, `UIView`) still can.
CONCRETE_SDK = {
    "Bool", "Int", "Int8", "Int16", "Int32", "Int64", "UInt", "UInt8", "UInt16", "UInt32", "UInt64", "Double",
    "Float", "CGFloat", "Decimal", "String", "Substring", "Character", "Array", "ArraySlice", "Dictionary", "Set",
    "Optional", "Result", "Range", "ClosedRange", "Data", "Date", "DateComponents", "URL", "URLComponents",
    "URLRequest", "UUID", "Calendar", "Locale", "TimeZone", "IndexPath", "IndexSet", "AttributedString",
    "CGRect", "CGPoint", "CGSize", "CGVector", "CGAffineTransform", "CGImage", "CGColor", "CGPath", "CGContext",
    "Image", "Color", "Font", "Text", "Path", "Angle", "Edge", "EdgeInsets", "Animation", "Binding", "LocalizedStringKey",
    "UIImage", "UIColor", "UIFont", "UIBezierPath", "NSImage", "NSColor", "NSFont", "NSBezierPath",
    "NSAttributedString", "UIGraphicsPDFRenderer", "UIGraphicsImageRenderer", "JSONDecoder", "JSONEncoder",
    "DateFormatter", "NumberFormatter", "URLSession", "FileManager", "UserDefaults", "Bundle", "NotificationCenter",
    "DispatchQueue", "GeometryProxy", "ScrollViewProxy", "GraphicsContext"}
# the first closure parameter of these SDK builders (`Path { p in }`, `GeometryReader { proxy in }`): that SDK type
CLOSURE_PARAM_TYPES = {"Path": "Path", "GeometryReader": "GeometryProxy", "ScrollViewReader": "ScrollViewProxy",
                       "Canvas": "GraphicsContext"}
CLOSURE_PARAM = re.compile(r"\b(" + "|".join(CLOSURE_PARAM_TYPES) + r")\s*(?:\([^()]*\))?\s*\{\s*(\w+)\s*(?:,\s*\w+\s*)?in\b")
# `let x = <literal>`: the literal's standard-library type
LITERAL_TYPES = (("Bool", r"(?:true|false)\b"), ("Double", r"-?\d+\.\d+\b"), ("Int", r"-?\d+\b"), ("String", r'"'))
LOCAL_DECL = re.compile(
    r"\b(?:let|var)\s+(\w+)\s*(?::\s*(?:some\s+|any\s+)?([A-Z][\w.]*))?\s*(?:=\s*(?:try\s*[?!]?\s+)?(?:await\s+)?"
    r"(?:([A-Z]\w*)\s*(?:<[^=\n]*?>)?\s*\(|((?:true|false)\b|-?\d+\.\d+\b|-?\d+\b|\"))?)?")


def _static(d) -> bool:
    return bool(STATIC_MODS & d.modifiers)


def parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_swift as m
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise TreeSitterMissing(f"tree-sitter grammar for swift not installed ({e}); "
                                f"pip install tree-sitter tree-sitter-swift") from e
    return Parser(Language(m.language()))


def source_files(root: Path, project=None) -> list[str]:
    rules = path_rules(project, "swift")
    out = []
    for dp, dn, fn in os.walk(root):
        rd = os.path.relpath(dp, root).replace(os.sep, "/")
        rd = "" if rd == "." else rd
        dn[:] = sorted(rules.prune(rd, dn, dot=True))
        for f in sorted(fn):
            if f.endswith(EXTS):
                rel = f"{rd}/{f}" if rd else f
                if keep_file(os.path.join(dp, f)) and not rules.excluded(rel):
                    out.append(rel)
    return out


def template(raw: str) -> str:
    """Swift string literal source -> URL template: "orders/\\(id)" -> orders/{id}, "\\(baseURL)/x" -> {baseURL}/x."""
    s = raw.strip()
    if s.startswith('"""'):
        s = s[3:-3]
    elif s.startswith('"'):
        s = s[1:-1]

    def rep(m):
        name = re.sub(r"[^\w.]", "", m.group(1).split("(")[0]).split(".")[-1] or "?"
        return "{" + name + "}"
    return TEMPLATE.sub(rep, s)


def split_url(t: str) -> tuple[str | None, str]:
    t = t.split("?")[0].split("#")[0]
    m = re.match(r"^([a-zA-Z][\w+.-]*://[^/]*)(.*)$", t)
    origin = None
    if m:
        origin, t = m.group(1), m.group(2)
    else:
        m = re.match(r"^(\{[^{}/]*\})(/.*|)$", t)
        if m:
            origin, t = m.group(1), m.group(2)
    return origin, ("/" + t.lstrip("/")) if t else "/"


def join_path(base: str, p: str) -> str:
    if not p:
        return base or ""
    return (base.rstrip("/") + "/" + p.lstrip("/")) if base else p


def vapor_seg(s: str) -> str:
    return "{" + s[1:] + "}" if s.startswith(":") else s


@dataclass
class Decl:
    id: str
    kind: str
    name: str
    fqn: str
    file: str
    line: int
    end: int
    cls: str | None = None
    supers: list = field(default_factory=list)
    attributes: list = field(default_factory=list)
    modifiers: set = field(default_factory=set)
    types: dict = field(default_factory=dict)
    test: bool = False
    ranges: list = field(default_factory=list)      # property node: [(first line, last line, "get" | "didSet" ...)]
    owner: str | None = None    # the containing type's node id when it is a per-`#if`-branch variant (`class:T@9`)


RAW_IDENT_DECL = re.compile(rb"\b(?:func|struct|class|enum|actor|extension|protocol|case|let|var)\s+`[^`\"\n\\]*[^\w`\"\n\\]")
RAW_IDENT = re.compile(rb"`([^`\n]*)`")       # backticks pair up left to right on a line


def _raw_span(m) -> bytes:
    body = m.group(1)
    if not body or re.fullmatch(rb"\w+", body) or re.search(rb"[\"\\]", body):
        return m.group(0)
    return b"`" + re.sub(rb"\W", b"_", body) + b"`"


def _raw_identifiers(src: bytes) -> bytes:
    """Swift 6.2 raw identifiers (`` @Test func `Sums the items`() ``, `` struct `Pricing tests` ``) are not in the
    tree-sitter grammar: in a file that declares one, each backticked span with a space or punctuation is parsed as
    a same-length plain identifier (`_` for every other byte), so byte offsets, and the names read from the
    original source, stay as written."""
    if b"`" not in src or not RAW_IDENT_DECL.search(src):
        return src
    return RAW_IDENT.sub(_raw_span, src)


SOURCE_LOCATION_DIRECTIVE = re.compile(rb"^[ \t]*#sourceLocation\s*\([^)\n]*\)[ \t]*$", re.M)
UNDERSCORE_MACRO = re.compile(rb"(?<![\w#])#_[A-Za-z]\w*\b")
IF_BLOCK = re.compile(rb"^[ \t]*#if\b[^\n]*\n(?:(?![ \t]*#if\b)[^\n]*\n)*?[ \t]*#endif\b[^\n]*$", re.M)
DIRECTIVE_LINE = re.compile(rb"^[ \t]*#(?:if|elseif|else|endif)\b[^\n]*$", re.M)
ATTRIBUTES_ONLY = re.compile(rb"\s*(?:@[\w.]+(?:\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?\s*)+")


# `typealias Client = A.Client` / `  & B.Client`, `let total = base` / `  * word.count`: a binary operator that starts a
# continuation line (the grammar closes the statement at the line end, and a type alias then takes the enclosing type
# with it); the operator moves to the end of the line before, same byte length and line count
CONTINUATION = re.compile(rb"(?<=[\w)\]}?!>\"])([ \t]*)\n([ \t]*)(&&|\|\||\?\?|==|!=|<=|>=|\.\.<|\.\.\.|[&|+*/%<>-])([ \t]+)(?=\S)")


# `()` as a value (`.right(())`, `const(())`, `{ _ in () }`, `cond ? x : ()`) and as an empty associated-value
# pattern (`case .right():`); `@convention(c)` in a type: not in the grammar, rewritten to a same-length `0` / blanks
EMPTY_TUPLE_VALUE = re.compile(rb"(?:(?<=\()|(?<=,)|(?<=\bin)|(?<=\breturn)|(?<=\?\?)|(?<=[^:]:))([ \t]*)\(\)"
                               rb"(?=[ \t]*(?:[),}\]\n]|//))(?![ \t]*\)[ \t]*(?:->|throws|async|rethrows))")
EMPTY_CASE_PATTERN = re.compile(rb"(\bcase[ \t]+\.\w+)\(\)(?=[ \t]*[:,])")
CONVENTION = re.compile(rb"@convention\(\w+\)")
# `if let x = try? await f()`, `while try await g()`: `try` before `await` in a condition does not parse; `try` is blanked
TRY_AWAIT = re.compile(rb"(?<![\w.])try[?!]?(?=[ \t]+await\b)")
# `x as? String ?? ""`: a cast before `??` does not parse; the cast is blanked (`x            ?? ""`)
CAST_COALESCE = re.compile(rb"(?<![\w.])as[?!]?[ \t]+(?:[\w.]+(?:<[^<>\n]*>)?|\[[^\[\]\n]*\])\??(?=[ \t]*\?\?)")


def _empty_tuple(m, src: bytes) -> bytes:
    """`0 ` for a `()` value; unchanged inside generic arguments (`Tagged<((), email: ()), String>` is a type)."""
    pre = src[src.rfind(b"\n", 0, m.start()) + 1:m.start()].replace(b"->", b"")
    if pre.count(b"<") > pre.count(b">") or b"typealias" in pre:
        return m.group(0)
    return m.group(1) + b"0 "


def _continuation(m) -> bytes:
    trail, indent, op, gap = m.group(1), m.group(2), m.group(3), m.group(4)
    return b" " + op + trail + b"\n" + indent + gap[:-1] if gap else m.group(0)


def _blank(m) -> bytes:
    return re.sub(rb"[^\n]", b" ", m.group(0))


def _attribute_block(m, src: bytes, blocks: list) -> bytes:
    """`#if os(macOS)` / `@Test` / `#endif` above a declaration: the directive lines are blanked so the attribute
    attaches to the declaration that follows; (first line, last line, `#if` line text) goes to `blocks`."""
    block = m.group(0)
    if not ATTRIBUTES_ONLY.fullmatch(DIRECTIVE_LINE.sub(b"", block)):
        return block
    first = src.count(b"\n", 0, m.start()) + 1
    dirs = DIRECTIVE_LINE.findall(block)
    blocks.append((first, first + block.count(b"\n"),
                   dirs[0].strip().decode("utf-8", "replace") if len(dirs) == 2 else None))
    return DIRECTIVE_LINE.sub(_blank, block)


def _join_continuations(src: bytes) -> bytes:
    """Apply CONTINUATION, except after a line that ends in a `//` comment (the operator would be commented out)."""
    if b"\n" not in src:
        return src
    def sub(m):
        line_start = src.rfind(b"\n", 0, m.start()) + 1
        if b"//" in src[line_start:m.start()] or b"/*" in src[line_start:m.start()]:
            return m.group(0)
        return _continuation(m)
    return CONTINUATION.sub(sub, src)


def _preprocess(src: bytes, blocks: list | None = None) -> tuple[bytes, list[str]]:
    """Valid Swift the tree-sitter grammar does not parse, rewritten in place with the same byte length (offsets and
    line numbers stay those of the file; names are read from the original source): raw identifiers, a
    `#sourceLocation(...)` directive (blanked), a `#_sourceLocation`-style macro expression (a same-length `#line`),
    and an `#if` block holding only attributes (its directive lines blanked, #73). Returns the source to parse and
    what was rewritten."""
    out, what = _raw_identifiers(src), []
    if out is not src:
        what.append("raw_identifiers")
    if b"#sourceLocation" in out:
        new = SOURCE_LOCATION_DIRECTIVE.sub(_blank, out)
        if new != out:
            out = new
            what.append("source_location_directives")
    if b"#_" in out:
        new = UNDERSCORE_MACRO.sub(lambda m: b"#line" + b" " * (len(m.group(0)) - 5) if len(m.group(0)) >= 5
                                   else m.group(0), out)
        if new != out:
            out = new
            what.append("underscore_macros")
    if b"()" in out:
        new = EMPTY_CASE_PATTERN.sub(lambda m: m.group(1) + b"  ", EMPTY_TUPLE_VALUE.sub(lambda m: _empty_tuple(m, out), out))
        if new != out:
            out = new
            what.append("empty_tuples")
    if b"@convention" in out:
        out = CONVENTION.sub(_blank, out)
        what.append("convention_attributes")
    if b"await" in out:
        new = TRY_AWAIT.sub(_blank, out)
        if new != out:
            out = new
            what.append("try_await")
    if b"??" in out:
        new = CAST_COALESCE.sub(_blank, out)
        if new != out:
            out = new
            what.append("cast_coalesce")
    new = _join_continuations(out)
    if new != out:
        out = new
        what.append("continuation_operators")
    if b"#if" in out and b"@" in out:
        found = [] if blocks is None else blocks
        new = IF_BLOCK.sub(lambda m: _attribute_block(m, out, found), out)
        if new != out:
            out = new
            what.append("attributes_in_if_blocks")
    assert len(out) == len(src)
    return out, what


class SFile:
    def __init__(self, rel: str, src: bytes, tree):
        self.rel, self.src, self.tree = rel, src, tree
        self.test = bool(TEST_PATH.search(rel))
        self.imports: set[str] = set()
        self.attr_blocks: list = []      # attribute-only `#if` blocks blanked before parsing (_preprocess)


class SwiftPlugin(LanguagePlugin):
    name = "swift"
    mac = None          # how the project builds for the Mac (xcode.apple_build), set per index

    def detect(self, project: Project) -> bool:
        self._files = source_files(project.root, project)
        return bool(self._files)

    def t(self, n) -> str:
        return self.cur.src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    # ------------------------------------------------------------------ index
    def index(self, project: Project, builder: GraphBuilder, frameworks) -> dict:
        t0 = time.time()
        p = parser()
        self.b = builder
        files = getattr(self, "_files", None)
        if files is None:
            files = source_files(project.root, project)
        self.decls: dict[str, Decl] = {}
        self._raw: dict[str, str] = {}
        self._attr_raw: dict[str, dict] = {}     # decl id -> {"Test" | "Suite": attribute source text}
        self._overloads: dict[tuple, Decl] = {}
        self._vmember: dict[tuple, str] = {}
        self._qsup: dict[str, list] = defaultdict(list)  # type fqn -> qualified supertype names written    # (variant type id, member id without @line) -> its node id (#86)
        self.by_name: dict[str, list[Decl]] = defaultdict(list)
        self.types: dict[str, Decl] = {}
        self.members: dict[str, dict[str, list[Decl]]] = defaultdict(lambda: defaultdict(list))
        self.calls: list[tuple] = []
        self.props: dict[str, dict[str, list[Decl]]] = defaultdict(lambda: defaultdict(list))   # type fqn -> name
        self.prop_names: set[str] = set()
        self.props_in: dict[str, list[Decl]] = defaultdict(list)   # file -> property nodes
        self.stored_names: set[str] = set()      # names of plain stored properties anywhere in the project
        self.stored_in: dict[str, set[str]] = defaultdict(set)    # type fqn -> its stored property names
        self.fields: dict[str, dict[str, str]] = defaultdict(dict)  # type fqn -> stored instance property -> field id
        self.field_names: set[str] = set()
        self.mutating_names: set[str] = set()
        self.branch_of: dict[tuple, dict] = {}     # (owner, line, type name) -> enclosing branch of a construction
        self.branch_at: dict[tuple, dict] = {}     # (file, line, type name) -> the same, for the exact layer
        self.values: dict[str, dict[str, str]] = defaultdict(dict)  # type fqn ("" file level) -> case/constant -> id
        self.globals_: dict[str, list] = {}      # file-level constant name -> [(id, file, test, private)]
        self.prop_refs: list[tuple] = []         # (owner, name, receiver | None, line, sf, decl, write)
        self.sigs: dict[str, list] = defaultdict(list)   # function / init node id -> [((label, optional), ...)]
        self.http: list[dict] = []
        self.navs: list[tuple] = []          # (owner, view type name, how, file, line)
        # navigationDestination(for: T.self) { switch d { case .x: V() } }: T -> case (None: any) -> view names (#68)
        self.dest_map: dict[str, dict] = defaultdict(lambda: defaultdict(list))
        self.value_navs: list[tuple] = []    # (owner, type name | None, case, how, file, line)
        self.handler_refs: list[tuple] = []  # (route id, method name, type fqn, file, line)
        self.st = defaultdict(int)
        from ...xcode import apple_build
        try:     # how the project builds for the Mac: AppKit target, Catalyst, both or neither (#74)
            self.mac = apple_build(project.root)["mac"]
        except Exception:  # noqa: BLE001  (a build file cg cannot read never fails the index)
            self.mac = None
        from .packages import SwiftPM
        try:     # SwiftPM modules: what a package target may name (#90)
            self.spm = SwiftPM(project.root, list(files))
        except Exception:  # noqa: BLE001
            self.spm = None
        self._at = None
        if self.spm:
            self.st["swiftpm_targets"] = len(self.spm.targets)
        sfiles, failed = [], []
        errs: dict[str, list] = {}
        for rel in files:
            try:
                src = (project.root / rel).read_bytes()
            except OSError:
                failed.append(rel)
                continue
            blocks: list = []
            psrc, what = _preprocess(src, blocks)
            for w in what:
                self.st[f"files_with_{w}"] += 1
            tree = p.parse(psrc)
            if tree.root_node.has_error:
                self.st["files_with_syntax_errors"] += 1
                errs[rel] = tree_spans(tree.root_node)
            sfiles.append(SFile(rel, src, tree))
            sfiles[-1].attr_blocks = blocks
        self._fd: dict[str, list[Decl]] = defaultdict(list)
        self._avail_regions: dict[str, list] = {}
        for sf in sfiles:            # pass 1: declarations
            self.cur = sf
            for c in sf.tree.root_node.children:
                if c.type == "import_declaration":
                    sf.imports.add(self.t(c).split()[-1])
            if sf.imports & TEST_IMPORTS:     # an Xcode test target folder with any name
                sf.test = True
            self._decls(sf.tree.root_node, sf, None)
        for d in self.decls.values():
            self._fd[d.file].append(d)
        self._xctest_methods()
        self._moya_targets(sfiles)
        self._fluent_models(sfiles)
        for sf in sfiles:            # pass 2: references and framework facts
            self.cur = sf
            fid = self.b.add_node("file", f"swift:{sf.rel}", name=sf.rel, file=sf.rel, line=1, lang="swift",
                                  attrs={"test": True} if sf.test else {})
            self._refs(sf.tree.root_node, sf, fid, None, {})
            self._directives(sf)
            self._available(sf)
        self._resolve_calls()
        self._resolve_props()
        self._at = None
        self._hierarchy()
        self._fluent_migrations(sfiles)
        self._moya_endpoints()
        from .baseurl import collect
        self.bases = collect(project.root, {sf.rel: sf.src.decode("utf-8", "replace") for sf in sfiles
                                            if not sf.test and (b"http" in sf.src or b"InfoDictionary" in sf.src)})
        self._emit_http()
        self._link_navs()
        for rid, mname, tfq, file, line in self.handler_refs:
            t = self.types.get(tfq)
            ms = self._member(t, mname) if t else []
            if ms:
                self.b.add_edge(rid, ms[0].id, "ROUTES_TO", file, line, HEURISTIC)
                self.b.nodes[rid].attrs.setdefault("handler", ms[0].fqn)
            else:
                self.st["routes_unresolved_handler"] += 1
        self.file_report = {"seen": [sf.rel for sf in sfiles] + failed, "parse_failed": failed, "syntax_errors": errs}
        mode = self._exact(project, [sf.rel for sf in sfiles])
        self._apply_available()
        st = dict(self.st)
        st.update({"mode": mode, "files": len(sfiles), "declarations": len(self.decls),
                   "source_files": sum(1 for sf in sfiles if not re.match(r"(.*/)?Package(@swift-[\d.]+)?\.swift$", sf.rel)),
                   "seconds": round(time.time() - t0, 2)})
        return st

    def _exact(self, project: Project, files: list[str]) -> str:
        """Exact layer from the Swift index store (exact.py); the heuristic graph stays when there is none or it
        cannot be read."""
        from . import exact
        t0 = time.time()
        try:
            store, info = exact.find_store(project, files)
        except Exception as e:  # noqa: BLE001 - a toolchain problem must not lose the heuristic graph
            store, info = None, {"status": f"index store lookup failed: {e}"}
        mode = "heuristic"
        if store is not None:
            try:
                if exact.ExactLayer(self).apply(store, info["lib"], project.root, files, self.st):
                    mode = "indexstore"
                else:
                    info["status"] = "the index store has no units for this project's files"
            except Exception as e:  # noqa: BLE001
                info["status"] = f"index store import failed: {type(e).__name__}: {e}"
        info["seconds"] = round(time.time() - t0, 2)
        self.st["index"] = info
        return mode

    # ------------------------------------------------------------------ pass 1
    def _opens_branch(self, n) -> bool:
        """Is `n` the first declaration of a `#if` / `#elseif` / `#else` branch (comments aside)? A conditional
        definition next to an unconditional one of that name is its own node too (Alamofire's `init(_:)` and the
        per-platform `init()`s)."""
        p = n.prev_sibling
        while p is not None:
            if p.type == "directive":
                return not self.t(p).lstrip().startswith("#endif")
            if p.is_named and p.type not in ("comment", "multiline_comment"):
                return False
            p = p.prev_sibling
        return False

    def _other_branch(self, n, prev_line: int) -> bool:
        """Does `n` sit in another `#if` / `#elseif` / `#else` branch than the declaration at `prev_line` before it?
        It does when a `#else` / `#elseif` of the block around that declaration comes between them, or when both are
        in conditional blocks (`#if A ... #endif #if B ... #endif`). Comments, imports and other declarations in
        between do not matter; an unconditional overload after an unrelated `#if DEBUG ... #endif` is no variant."""
        a = n
        while a.parent is not None and not (a.parent.start_point[0] + 1 <= prev_line <= a.parent.end_point[0] + 1):
            a = a.parent                   # the ancestor of `n` that is a sibling of the earlier declaration
        if a.parent is None:
            return False
        depth, exited = 0, False
        for p in a.parent.children:
            if p.start_point[0] + 1 < prev_line or p.type != "directive":
                if p.id == a.id:
                    break
                continue
            t = self.t(p).lstrip()
            if t.startswith("#if"):
                depth += 1
            elif t.startswith("#endif"):
                if depth:
                    depth -= 1
                else:
                    exited = True
            elif t.startswith("#else") and depth == 0:         # `#else` and `#elseif`
                return True
        return exited and depth > 0

    def _branch_variant(self, prev: Decl, sf: SFile, n, cls: Decl | None) -> bool:
        """Is `n`, declared with the same name as `prev`, a separate definition for another `#if` branch? It is when
        the two sit in different branches of the same file (`_other_branch`), when `n` opens a branch
        (`_opens_branch`), or when `n` is a member of a type that is itself a per-branch variant
        (`#if os(macOS) struct T { func f() } #else struct T { func f() } #endif`, #86)."""
        if prev.file != sf.rel:
            return False
        if self._opens_branch(n) or self._other_branch(n, prev.line):
            return True
        return cls is not None and "@" in cls.id and prev.owner != cls.id

    @staticmethod
    def _owner(cls: Decl | None) -> str | None:
        return cls.id if cls is not None and "@" in cls.id else None

    def _prev(self, did: str, cls: Decl | None) -> Decl:
        """The declaration `did` names so far, inside the variant type `cls` when it is one (its own overloads)."""
        own = self._vmember.get((cls.id, did)) if cls is not None else None
        return self.decls[own] if own else self.decls[did]

    def _mods(self, n) -> tuple[list, set]:
        attrs, mods = [], set()
        self._raw = {}
        for c in n.children:
            if c.type == "modifiers":
                for m in c.children:
                    if m.type == "attribute":
                        mm = re.match(r"@`?([\w.]+)`?", self.t(m))
                        if mm:
                            attrs.append(mm.group(1).split(".")[-1])
                            if attrs[-1] in ("Test", "Suite"):
                                self._raw.setdefault(attrs[-1], self.t(m))
                    else:
                        mods.update(self.t(m).split())
        return attrs, mods

    def _name(self, n) -> str | None:
        x = n.child_by_field_name("name")
        return self.t(x).split("<")[0].strip() if x is not None else None

    def _type_name(self, n) -> str | None:
        if n is None:
            return None
        m = re.match(r"\s*(?:some\s+|any\s+)?\[?([A-Za-z_][\w.]*)", self.t(n))
        return m.group(1).split(".")[-1] if m else None

    @staticmethod
    def _orphans(n) -> dict:
        """Declarations tree-sitter put after a type whose body it closed early (a MISSING `}`, e.g. after a line the
        grammar does not know), up to the stray `}` that really closes it (an ERROR at the same level), or the end:
        node id -> the type declaration node they belong to (#73)."""
        out = {}
        kids = n.children
        i = 0
        while i < len(kids):
            c = kids[i]
            body = next((x for x in c.children if x.type in ("class_body", "enum_class_body", "protocol_body")), None) \
                if c.type in TYPE_DECLS else None
            if body is None or not body.children or not body.children[-1].is_missing:
                i += 1
                continue
            j = i + 1
            while j < len(kids) and not (kids[j].type == "ERROR" and
                                         kids[j].children and kids[j].children[0].type == "}"):
                if kids[j].is_named and kids[j].type not in ("comment", "multiline_comment"):
                    out[kids[j].id] = c
                j += 1
            if j < len(kids):
                out[kids[j].id] = c       # the closing `}`: marks where the type ends
            i = j + 1
        return out

    def _adopter(self, t, sf: SFile, cls: Decl | None) -> Decl | None:
        nm = self._name(t)
        if not nm:
            return None
        head = self.t(t).split("{", 1)[0]
        if re.search(r"\bextension\b", head):
            return self.types.get(nm)
        return self.types.get(f"{cls.fqn}.{nm}" if cls and cls.kind == "class" else nm)

    def _decls(self, n, sf: SFile, cls: Decl | None):
        adopt = self._orphans(n) if n.children and getattr(n, "has_error", False) else {}
        for c in n.children:
            if c.id in adopt:
                d = self._adopter(adopt[c.id], sf, cls)
                if d is not None and d.file == sf.rel:
                    end = c.end_point[0] + 1
                    if end > d.end:          # the type reaches its real closing brace
                        d.end = self.b.nodes[d.id].end_line = end
                    if c.type != "ERROR":
                        one = SimpleNamespace(children=[c])
                        self._props(one, d, sf)
                        self._decls(one, sf, d)
                        self.st["declarations_recovered_into_type"] += 1 if c.type in (
                            "function_declaration", "init_declaration", "property_declaration") + TYPE_DECLS else 0
                    continue
            ty = c.type
            if ty in TYPE_DECLS:
                nm = self._name(c)
                if not nm:
                    continue
                head = self.t(c).split("{", 1)[0]
                kw = re.match(r"\s*(?:@[\w.]+(?:\((?:[^()]|\([^()]*\))*\))?\s*|\w+\s+)*?(class|struct|enum|actor|extension|protocol)\b",
                              head)
                kw = kw.group(1) if kw else ("protocol" if ty == "protocol_declaration" else "class")
                attrs, mods = self._mods(c)
                supers = [self._type_name(x) for x in c.children if x.type == "inheritance_specifier"]
                supers = [s for s in supers if s]
                fq = f"{cls.fqn}.{nm}" if cls and kw != "extension" else nm
                for x in c.children:            # `: StatusEditor.AutocompleteService.Client`: the qualified name
                    if x.type == "inheritance_specifier":
                        q = re.match(r"\s*(?:some\s+|any\s+)?([A-Za-z_][\w.]*)", self.t(x))
                        if q and "." in q.group(1) and q.group(1) not in self._qsup[fq]:
                            self._qsup[fq].append(q.group(1))
                body = next((x for x in c.children if x.type in ("class_body", "enum_class_body", "protocol_body")), None)
                if kw == "extension":
                    base = self.types.get(nm)
                    if base is None:
                        base = Decl(f"class:{nm}", "class", nm, nm, sf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                                    None, [], [], {"extension-only"}, test=sf.test)
                        self._add(base, "extension")
                    base.supers += [s for s in supers if s not in base.supers]
                    self.st["extensions"] += 1
                    if body is not None:
                        self._props(body, base, sf)
                        self._decls(body, sf, base)
                    continue
                d = self.types.get(fq)
                if d is not None and "extension-only" in d.modifiers:     # extension seen before the type
                    d.modifiers.discard("extension-only")
                    d.file, d.line, d.end = sf.rel, c.start_point[0] + 1, c.end_point[0] + 1
                    d.supers = supers + [s for s in d.supers if s not in supers]
                    d.attributes = attrs
                    n0 = self.b.nodes[d.id]
                    n0.file, n0.line, n0.end_line = d.file, d.line, d.end
                    n0.attrs["swift_kind"] = kw
                    if d.test and "Suite" in attrs:
                        self._attr_raw[d.id] = self._raw
                        self._swift_testing(n0, d, "Suite")
                else:
                    did = f"class:{fq}"
                    if d is not None and self._branch_variant(d, sf, c, cls):
                        did = f"{did}@{c.start_point[0] + 1}"     # one definition per `#if` branch (#86)
                    d = Decl(did, "class", nm, fq, sf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                             cls.fqn if cls else None, supers, attrs, mods, test=sf.test, owner=self._owner(cls))
                    self._add(d, kw)
                if body is not None:
                    self._props(body, d, sf)
                    self._decls(body, sf, d)
            elif ty in ("function_declaration", "init_declaration", "protocol_function_declaration"):
                nm = "init" if ty == "init_declaration" else self._name(c)
                if not nm:
                    continue
                attrs, mods = self._mods(c)
                kind = "method" if cls else "function"
                fq = f"{cls.fqn}.{nm}" if cls else nm
                params = {}
                sig = []
                kids = list(c.children)
                for i, x in enumerate(kids):
                    if x.type == "parameter":
                        names = [self.t(y) for y in x.children if y.type == "simple_identifier"]
                        tn = self._type_name(next((y for y in x.children if y.is_named and y.type not in
                                                   ("simple_identifier",)), None))
                        if names and tn:
                            params[names[-1]] = tn
                        # optional at a call site: a default value, a closure (trailing-closure syntax), variadic
                        opt = (i + 1 < len(kids) and kids[i + 1].type == "=") or any(
                            y.type in ("function_type", "...") for y in x.children) or "->" in self.t(x)
                        if names:
                            sig.append((names[0], opt))
                did = f"{kind}:{fq}"
                if did in self.decls and "property" in self.decls[did].modifiers:
                    did = f"{did}~func"          # `var label { }` declared first, then `func label(for:)`
                if did in self.decls and _static(self.decls[did]) != bool(STATIC_MODS & mods):
                    # `func weight(forExtraIndex:)` and `static func weight(from:to:)`: two nodes; the one declared
                    # second carries `~static` / `~instance` (like `~2` for a second TypeScript declaration);
                    # overloads of one static-ness still share a node
                    did = f"{did}~{'static' if STATIC_MODS & mods else 'instance'}"
                if did in self.decls:
                    prev = self._prev(did, cls)
                    if not self._branch_variant(prev, sf, c, cls):           # overloads share one node
                        prev.types.update(params)
                        self.sigs[did].append(tuple(sig))
                        self._overloads[(sf.rel, c.start_point[0] + 1, nm)] = prev    # its body's calls are prev's
                        continue
                    did = f"{did}@{c.start_point[0] + 1}"                     # per-platform definition (#if os)
                self.sigs[did].append(tuple(sig))
                d = Decl(did, kind, nm, fq, sf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                         cls.fqn if cls else None, [], attrs, mods, params, sf.test, owner=self._owner(cls))
                self._add(d, "protocol requirement" if ty == "protocol_function_declaration" else kind)
            elif ty == "property_declaration" and cls is not None:
                nm = self._name(c)
                if nm == "body" and any(x.type == "computed_property" for x in c.children):
                    did = f"method:{cls.fqn}.body"
                    if did in self.decls and self._branch_variant(self._prev(did, cls), sf, c, cls):
                        did = f"{did}@{c.start_point[0] + 1}"
                    d = Decl(did, "method", "body", f"{cls.fqn}.body", sf.rel,
                             c.start_point[0] + 1, c.end_point[0] + 1, cls.fqn, [], [], set(), test=sf.test,
                             owner=self._owner(cls))
                    self._add(d, "view body")
                elif nm:
                    self._prop_decl(c, nm, sf, cls)
            elif ty == "enum_entry" and cls is not None:
                for x in c.children:
                    if x.type == "simple_identifier":
                        self._value(cls, "enum_case", self.t(x), sf, x)
            elif ty == "property_declaration" and cls is None and self._top_let(c) and \
                    not re.match(r"Package(@swift-[\d.]+)?\.swift$", os.path.basename(sf.rel)):
                nm = self._name(c)
                if nm:
                    self._value(None, "constant", nm, sf, c)
            elif ty == "protocol_property_declaration" and cls is not None:
                nm = (self._name(c) or "").split()[-1:]     # `var isShown: Bool { get }`: a conformer has it (#86)
                nm = nm[0] if nm else None
                req = self.b.nodes[cls.id].attrs.setdefault("property_requirements", [])
                if nm and nm not in req and len(req) < 64:
                    req.append(nm)
            elif ty not in ("lambda_literal", "statements", "function_body"):
                self._decls(c, sf, cls)

    @staticmethod
    def _top_let(c) -> bool:
        """A file-level `let` (not `var`) binding a single name: a constant."""
        vb = next((x for x in c.children if x.type == "value_binding_pattern"), None)
        return vb is not None and vb.text.strip() == b"let" and sum(x.type == "pattern" for x in c.children) == 1

    def _value(self, cls: Decl | None, kind: str, nm: str, sf: SFile, n):
        """An enum case (`enum_case:<Type>.<case>`) or a constant (`constant:<Type>.<name>`, `constant:<name>`
        for a file-level `let`): a node under its type that USES_VALUE edges point at (#84). Not a call target,
        so no CALLS edge changes."""
        fq = f"{cls.fqn}.{nm}" if cls else nm
        line = n.start_point[0] + 1
        key = fq
        _attrs, mods = self._mods(n)
        private = cls is None and bool(mods & {"private", "fileprivate"})
        if private:
            key = f"{fq}#{sf.rel}"        # a file's own `private let ray`
        prev = self.b.nodes.get(f"{kind}:{key}")
        if prev is not None and (prev.file, prev.line) != (sf.rel, line):
            if cls is None and prev.file != sf.rel:
                key = f"{fq}#{sf.rel}"    # another file's (another module's) `let jsonDecoder`: a symbol of its own
                if f"{kind}:{key}" in self.b.nodes:
                    key = f"{key}@{line}"
            else:
                key = f"{fq}@{line}"      # another `#if` branch's definition: the platform pass links the two
        nid = self.b.add_node(kind, key, name=nm, fqn=fq, file=sf.rel, line=line, end_line=n.end_point[0] + 1,
                              module=cls.fqn if cls else None, lang="swift",
                              attrs={"test": True} if sf.test else {})
        if cls is not None:
            self.b.add_edge(cls.id, nid, "CONTAINS", sf.rel, line, EXACT)
            self.values[cls.fqn].setdefault(nm, nid)
        else:
            self.globals_.setdefault(nm, []).append((nid, sf.rel, sf.test, private))
            self.values[""].setdefault(nm, nid)

    def _props(self, body, d: Decl, sf: SFile):
        for x in body.children:
            if x.type != "property_declaration":
                continue
            nm = self._name(x)
            ta = next((y for y in x.children if y.type == "type_annotation"), None)
            tn = self._type_name(ta.child_by_field_name("name") if ta is not None else None)
            if tn is None:
                m = re.search(r"=\s*([A-Z]\w*)\s*(?:<[^=]*?>)?\s*[.(]", self.t(x))   # Gate() / Gate<Image>()
                tn = m.group(1) if m else None
            if nm and tn:
                d.types[nm] = tn

    def _field(self, cls: Decl, nm: str, sf: SFile, c, attrs: list):
        """A stored instance property (`var count = 0`, `@State private var value`, `@Published var items`) is a
        `field:<Type>.<name>` node (#88): reads and writes of it are READS_PROP / WRITES_PROP edges, for `cg readers` /
        `cg writers Type.prop`. Its property wrapper, if any, is attrs.wrapper."""
        if nm in self.fields.get(cls.fqn, {}):
            return
        kw = next((self.t(x).split()[0] for x in c.children if x.type == "value_binding_pattern" and self.t(x).strip()), "var")
        a = {"property": "stored", "binding": "let" if kw == "let" else "var"}
        wrappers = [x for x in attrs if x not in ("MainActor", "objc", "IBOutlet", "IBInspectable", "available", "nonobjc")]
        if wrappers:
            a["wrapper"] = wrappers[0]
            if wrappers[0] in ("AppStorage", "SceneStorage"):     # the UserDefaults / scene key it persists under
                m = re.search(r'@(?:AppStorage|SceneStorage)\s*\(\s*"([^"]+)"', self.t(c))
                if m:
                    a["key"] = m.group(1)
        if sf.test:
            a["test"] = True
        fid = self.b.add_node("field", f"{cls.fqn}.{nm}", name=nm, fqn=f"{cls.fqn}.{nm}", file=sf.rel,
                              line=c.start_point[0] + 1, end_line=c.end_point[0] + 1,
                              module=getattr(self.b.nodes.get(cls.id), "module", None), lang="swift", attrs=a)
        self.b.add_edge(cls.id, fid, "CONTAINS", sf.rel, c.start_point[0] + 1, EXACT)
        self.fields[cls.fqn][nm] = fid
        self.field_names.add(nm)
        if wrappers:          # `_value = State(initialValue: x)`: the wrapper's storage is the same property
            self.fields[cls.fqn]["_" + nm] = fid
            self.field_names.add("_" + nm)
        self.st["stored_property_nodes"] += 1

    def _prop_decl(self, c, nm: str, sf: SFile, cls: Decl):
        """A computed property, a stored property with `willSet` / `didSet`, or a `lazy var` with an initializer
        becomes a node (`method:<Type>.<name>`, attrs.property, #72): the calls in its body come from it, and reads
        (writes, for observers) of it are CALLS edges to it. A plain stored instance property is a `field:` node with
        READS_PROP / WRITES_PROP edges instead (#88)."""
        comp = next((x for x in c.children if x.type == "computed_property"), None)
        obs = next((x for x in c.children if x.type == "willset_didset_block"), None)
        attrs, mods = self._mods(c)
        lazy = "lazy" in mods and any(x.type == "=" for x in c.children)
        if not (comp or obs or lazy):
            if mods & {"static", "class"}:
                self._value(cls, "constant", nm, sf, c)    # `static let shared = …` (#84)
            elif "extension-only" not in cls.modifiers:
                self._field(cls, nm, sf, c, attrs)
            self.stored_names.add(nm)
            self.stored_in[cls.fqn].add(nm)     # hides a protocol extension's default of that name
            return
        fq = f"{cls.fqn}.{nm}"
        did = f"method:{fq}"
        line = c.start_point[0] + 1
        if did in self.decls:
            prev = self._prev(did, cls)
            if "property" not in prev.modifiers:
                did = f"{did}~property"            # a `func label(for:)` declared first
            elif self._branch_variant(prev, sf, c, cls):
                did = f"{did}@{line}"              # one definition per `#if` branch
            else:
                return
        ranges = []
        for blk in (comp, obs):
            for x in (blk.children if blk is not None else ()):
                if x.type in ACCESSOR_CLAUSES:
                    ranges.append((x.start_point[0] + 1, x.end_point[0] + 1, ACCESSOR_CLAUSES[x.type]))
        how = "computed" if comp is not None else "observed" if obs is not None else "lazy"
        mods = set(mods) | {"property", how}
        d = Decl(did, "method", nm, fq, sf.rel, line, c.end_point[0] + 1, cls.fqn, [], attrs, mods, {}, sf.test, ranges,
                 self._owner(cls))
        self._add(d, {"computed": "computed property", "observed": "property observers", "lazy": "lazy property"}[how],
                  index=False)
        n = self.b.nodes[d.id]
        n.attrs["property"] = how
        if ranges:
            n.attrs["accessors"] = [r[2] for r in ranges]
        self.props[cls.fqn][nm].append(d)
        self.props_in[sf.rel].append(d)
        self.prop_names.add(nm)
        self.st[f"properties_{how}"] += 1

    def _add(self, d: Decl, display_kind: str, index: bool = True):
        attrs = {"swift_kind": display_kind}
        if d.test:
            attrs["test"] = True
        for a in ("static", "override", "async", "mutating", "private"):
            if a in d.modifiers:
                attrs[a] = True
        if "mutating" in d.modifiers and d.kind == "method":
            self.mutating_names.add(d.name)            # `basket.add(x)` on a stored struct value writes it (#88)
        if d.attributes:
            attrs["attributes"] = d.attributes[:12]
        mod = d.cls or None
        self.b.add_node(d.kind, d.id.split(":", 1)[1], name=d.name, fqn=d.fqn, file=d.file, line=d.line,
                        end_line=d.end, module=mod, lang="swift", attrs=attrs)
        n = self.b.nodes[d.id]
        if self._raw and ("Test" in d.attributes or "Suite" in d.attributes):
            self._attr_raw[d.id] = self._raw
        if d.kind == "class" and "main" in d.attributes:
            n.entry_kind = "main"
            self.st["entries_main"] += 1
        if d.test and d.kind in ("function", "method") and "Test" in d.attributes:
            self._swift_testing(n, d, "Test")
        elif d.test and d.kind == "class" and "Suite" in d.attributes:
            self._swift_testing(n, d, "Suite")
        self.decls[d.id] = d
        if d.owner:
            self._vmember.setdefault((d.owner, d.id.split("@", 1)[0]), d.id)
        if index:            # a property node is not a call target (`_prop_targets` resolves reads of it)
            self.by_name[d.name].append(d)
        if d.kind == "class":
            self.types.setdefault(d.fqn, d)
        if d.cls:
            if index:
                self.members[d.cls][d.name].append(d)
            self.b.add_edge(d.owner or f"class:{d.cls}", d.id, "CONTAINS", d.file, d.line, EXACT)

    def _swift_testing(self, n, d: Decl, attr: str):
        """Swift Testing: an `@Test` function / method is one test case (a parameterized `@Test(arguments:)` too), an
        `@Suite` type a suite; the display name, tags and traits come from the attribute's arguments."""
        raw = self._attr_raw.get(d.id, {}).get(attr, "")
        if attr == "Test":
            n.entry_kind = "test"
            n.attrs["framework"] = "swift-testing"
            if d.cls:
                n.attrs["suite"] = d.cls
            if re.search(r"\barguments\s*:", raw):
                n.attrs["parameterized"] = True
            self.st["swift_testing_tests"] += 1
        else:
            n.attrs["suite"] = True
            self.st["swift_testing_suites"] += 1
        m = re.match(r'@(?:Testing\.)?\w+\s*\(\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            n.attrs["display_name"] = m.group(1)
        tags = [t for grp in re.findall(r"\.tags\(([^)]*)\)", raw) for t in re.findall(r"\.?(\w+)", grp)]
        if tags:
            n.attrs["tags"] = tags[:12]
        traits = sorted(set(re.findall(r"\.(disabled|enabled|bug|timeLimit|serialized)\b", raw)))
        if traits:
            n.attrs["traits"] = traits

    def _xctest_methods(self):
        """XCTest: a `test*` instance method without parameters on a class whose superclass chain reaches XCTestCase
        (directly, through a project base class, or through an external `*TestCase` base)."""
        memo: dict[str, bool] = {}

        def is_case(fq: str, depth=0) -> bool:
            if fq in memo:
                return memo[fq]
            memo[fq] = False
            d = self.types.get(fq) or self.types.get(fq.rsplit(".", 1)[-1])
            ok = False
            if d is not None and depth < 12:
                for s in d.supers:
                    if s == "XCTestCase" or s.endswith("TestCase") or (s in self.types and is_case(s, depth + 1)):
                        ok = True
                        break
            memo[fq] = ok
            return ok

        for d in self.decls.values():
            if not (d.test and d.kind == "method" and d.name.startswith("test") and d.cls) or "static" in d.modifiers:
                continue
            n = self.b.nodes.get(d.id)
            if n is None or n.entry_kind == "test" or any(self.sigs.get(d.id, [()])[0]):
                continue
            if is_case(d.cls):
                n.entry_kind = "test"
                n.attrs["framework"] = "xctest"
                self.st["xctest_tests"] += 1

    # ------------------------------------------------------------------ pass 2
    def _decl_at(self, sf: SFile, n, kinds, name=None) -> Decl | None:
        line = n.start_point[0] + 1
        nm = name or self._name(n)
        for d in self._fd.get(sf.rel, ()):
            if d.line == line and d.kind in kinds and d.name == nm:
                return d
        ov = self._overloads.get((sf.rel, line, nm))
        if ov is not None and ov.kind in kinds:
            return ov
        if name and kinds == ("class",):
            d = self.types.get(name)
            return d
        return None

    def _refs(self, n, sf: SFile, owner: str, decl: Decl | None, ctx: dict):
        adopt = self._orphans(n) if n.children and getattr(n, "has_error", False) else {}
        for c in n.children:
            if c.id in adopt and c.type != "ERROR":
                d = self._adopter(adopt[c.id], sf, decl if decl is not None and decl.kind == "class" else None)
                if d is not None:
                    self._refs(SimpleNamespace(children=[c]), sf, d.id, d, ctx)
                    continue
            ty = c.type
            if self.values and ty in VALUE_SITES:
                self._value_refs(c, sf, owner, decl)
            if ty in ("function_declaration", "init_declaration"):
                d = self._decl_at(sf, c, ("function", "method"), "init" if ty == "init_declaration" else None)
                if d is not None:
                    self._fn_http(c, sf, d)
                    self._refs(c, sf, d.id, d, {"routers": {}})
                    continue
            elif ty in TYPE_DECLS:
                nm = self._name(c)
                d = self.types.get(nm) if nm else None
                inner = next((x for x in self._fd.get(sf.rel, ()) if x.kind == "class" and x.name == nm
                              and x.line == c.start_point[0] + 1), None)
                d = inner or d
                self._refs(c, sf, d.id if d else owner, d or decl, ctx)
                continue
            elif ty == "property_declaration" and decl is not None and decl.kind == "class" and self._name(c) == "body" \
                    and f"method:{decl.fqn}.body" in self.decls:
                b = self.decls[f"method:{decl.fqn}.body"]
                self._refs(c, sf, b.id, b, ctx)
                continue
            elif ty == "property_declaration" and decl is not None and decl.kind == "class" and self.prop_names:
                pd = self._decl_at(sf, c, ("method",))
                if pd is not None and "property" in pd.modifiers:
                    self._refs(c, sf, pd.id, pd, ctx)
                    continue
            elif ty == "navigation_expression" and (self.prop_names or self.field_names):
                self._prop_nav(c, sf, owner, decl)
            elif ty == "simple_identifier" and (self.prop_names or self.field_names):
                self._prop_bare(c, sf, owner, decl)
            elif ty == "call_expression":
                if self._call(c, sf, owner, decl, ctx):
                    continue
            elif ty == "property_declaration" and decl is not None:
                self._router_binding(c, ctx)
            self._refs(c, sf, owner, decl, ctx)

    def _value_refs(self, c, sf: SFile, owner: str, decl: Decl | None):
        """USES_VALUE edges to enum cases and constants where the type is certain (#84): `Type.case`,
        `Type.constant`, a file-level constant by name, and `.case` where the contextual type is known: the
        subject of a `switch`, the other side of `==` / `!=`, a `let x: T = .case`, a parameter default."""
        ty = c.type
        line = c.start_point[0] + 1
        cls = self._encl_type(decl)
        self._at = (sf, line)
        if ty == "call_expression":
            f = c.children[0] if c.children else None
            if f is not None and f.type == "navigation_expression":
                self._value_refs(f, sf, owner, decl)      # `E.c(1)`: the callee is not visited again
            return
        if ty == "navigation_expression":
            target = c.child_by_field_name("target")
            sfx = c.child_by_field_name("suffix")
            nmn = sfx.child_by_field_name("suffix") if sfx is not None else None
            if target is None or nmn is None or target.type not in ("simple_identifier", "navigation_expression"):
                return
            kind, tn = self._recv_type(self.t(target), decl, cls)
            if kind == "meta":
                self._value_edge(owner, tn, self.t(nmn), sf, line, "member")
            return
        if ty == "simple_identifier":
            nm = self.t(c)
            p = c.parent
            if nm in self.values.get("", {}) and p is not None and p.type not in NOT_A_READ \
                    and p.type not in ("navigation_suffix", "pattern", "value_argument_label") \
                    and not (p.type == "navigation_expression" and p.child_by_field_name("target") != c) \
                    and not (decl is not None and (self._bound(c, nm) or self._shadowed(decl, nm, sf, line))) \
                    and (decl is None or self._local(decl, nm) is None) and not self._is_callee(c) \
                    and not (cls is not None and nm in self.stored_in.get(cls.fqn, ())):
                self._global_edge(owner, nm, sf, line)
            return
        if ty == "switch_statement":
            subj = next((x for x in c.children if x.is_named and x.type not in ("switch_entry", "comment")), None)
            tn = self._ctx_type(subj, decl, cls)
            if tn is None:
                return
            for e in c.children:
                if e.type != "switch_entry":
                    continue
                for sp in e.children:
                    if sp.type == "switch_pattern":
                        self._implicit(sp, tn, owner, sf)
            return
        if ty == "equality_expression":
            kids = [x for x in c.children if x.is_named]
            if len(kids) == 2:
                for a, b in ((kids[0], kids[1]), (kids[1], kids[0])):
                    if b.type in ("prefix_expression", "call_expression") and self.t(b).lstrip().startswith("."):
                        tn = self._ctx_type(a, decl, cls)
                        if tn is not None:
                            self._implicit(b, tn, owner, sf)
            return
        if ty == "property_declaration":
            ta = next((x for x in c.children if x.type == "type_annotation"), None)
            val = next((x for x in c.children if x.type == "prefix_expression"), None)
            if ta is not None and val is not None:
                tn = self._type_name(ta.child_by_field_name("name"))
                t = self._type(tn)
                if t is not None:
                    self._implicit(val, t.fqn, owner, sf)
            return
        if ty == "parameter":
            nx = c.next_sibling
            if nx is not None and nx.type == "=" and nx.next_sibling is not None \
                    and nx.next_sibling.type == "prefix_expression":
                tn = self._type_name(next((y for y in c.children if y.is_named and y.type != "simple_identifier"),
                                          None))
                t = self._type(tn)
                if t is not None:
                    self._implicit(nx.next_sibling, t.fqn, owner, sf)

    def _ctx_type(self, n, decl: Decl | None, cls: Decl | None) -> str | None:
        """The project type of an expression used as a contextual type for `.case`: `self` inside the type, a
        parameter, a local or a property of known type."""
        if n is None or n.type not in ("simple_identifier", "navigation_expression", "self_expression"):
            return None
        kind, tn = self._recv_type(self.t(n), decl, cls)
        return tn if kind == "type" and tn in self.values else None

    def _implicit(self, n, tn: str, owner: str, sf: SFile):
        """`.case` / `.case(let x)` / `.case?` written in `n` (a prefix expression or a switch pattern)."""
        txt = self.t(n).lstrip()
        m = re.match(r"^(?:case\s+)?\.\s*([A-Za-z_]\w*)", txt)
        if m:
            self._value_edge(owner, tn, m.group(1), sf, n.start_point[0] + 1, "contextual type")

    def _global_edge(self, owner: str, nm: str, sf: SFile, line: int):
        """A file-level constant read by name: the one the reading file can see (its own `private let`, else a
        single visible one: same module or a dependency, a test file's only from tests); none when ambiguous."""
        cs = self.globals_.get(nm, ())
        own = [c for c in cs if c[1] == sf.rel]
        if not own:
            own = [c for c in cs if not c[3] and (sf.test or not c[2])
                   and (not self.spm or self.spm.sees(sf.rel, c[1]))]
        if len(own) == 1 and own[0][0] != owner:
            self.b.add_edge(owner, own[0][0], "USES_VALUE", sf.rel, line, EXACT, how="file constant")
            self.st["value_refs"] += 1

    def _value_edge(self, owner: str, tn: str, nm: str, sf: SFile, line: int, how: str):
        vid = self.values.get(tn, {}).get(nm)
        if vid is not None and vid != owner:
            self.b.add_edge(owner, vid, "USES_VALUE", sf.rel, line, EXACT, how=how)
            self.st["value_refs"] += 1

    @staticmethod
    def _is_callee(n) -> bool:
        p = n.parent
        return p is not None and p.type == "call_expression" and p.children and p.children[0] == n

    def _is_write(self, n):
        """True for an assignment target; "mutating" for the receiver of a mutating standard-library method
        (`items.append(x)`, `flag.toggle()`) or of a `mutating func` declared in the project (`basket.add(x)`),
        "inout" for `&x`; False for a read."""
        p = n.parent
        if p is None:
            return False
        if p.type == "directly_assignable_expression":
            return True
        if p.type == "prefix_expression" and self.t(p).lstrip().startswith("&"):
            return "inout"
        if p.type == "navigation_expression" and p.child_by_field_name("target") == n and self._is_callee(p):
            sfx = p.child_by_field_name("suffix")
            m = sfx.child_by_field_name("suffix") if sfx is not None else None
            if m is not None and (self.t(m) in MUTATING_METHODS or self.t(m) in self.mutating_names):
                return "mutating"
        return False

    def _prop_nav(self, c, sf: SFile, owner: str, decl: Decl | None):
        """`cart.label`, `self.summary`, `Cart.shared`: a read (or write) of a property node, resolved later."""
        sfx = c.child_by_field_name("suffix")
        nmn = sfx.child_by_field_name("suffix") if sfx is not None else None
        nm = self.t(nmn) if nmn is not None else None
        proj = bool(nm) and nm.startswith("$") and len(nm) > 1          # `vm.$query`: a Binding / Publisher
        if proj:
            nm = nm[1:]
        if not nm or (nm not in self.prop_names and nm not in self.field_names) or self._is_callee(c):
            return
        target = c.child_by_field_name("target")
        if target is None:
            return
        if target.type == "key_path_expression":
            root = self.t(target).lstrip("\\").strip()               # `\Basket.items`; `\.items` has no named root
            if root and nm in self.field_names and re.fullmatch(r"[A-Z]\w*(\.\w+)*", root):
                self.prop_refs.append((owner, nm, "\\" + root, c.start_point[0] + 1, sf, decl, "keypath"))
            return
        recv = re.sub(r"^\s*[!\-~&]+", "", self.t(target))
        self.prop_refs.append((owner, nm, recv, c.start_point[0] + 1, sf, decl, "binding" if proj else self._is_write(c)))

    def _prop_bare(self, c, sf: SFile, owner: str, decl: Decl | None):
        """`summary` inside a member of the type that declares it (an implicit `self.summary`)."""
        nm = self.t(c)
        proj = nm.startswith("$") and len(nm) > 1 and not nm[1:].isdigit()    # `$query`: the wrapper's projection
        if proj:
            nm = nm[1:]
        p = c.parent
        if (nm not in self.prop_names and nm not in self.field_names) or p is None or p.type in NOT_A_READ or self._is_callee(c):
            return
        if p.type == "value_argument" and p.child_by_field_name("value") != c:
            return
        if p.type == "prefix_expression" and self.t(p).lstrip().startswith("."):
            return
        if p.type == "navigation_expression" and p.child_by_field_name("target") != c:
            return
        if c.prev_sibling is not None and c.prev_sibling.type == ".":
            return                                  # `guard case .success = self`: an implicit member
        line = c.start_point[0] + 1
        if decl is None or decl.kind == "class" or self._bound(c, nm) or self._shadowed(decl, nm, sf, line):
            return
        self.prop_refs.append((owner, nm, None, line, sf, decl, "binding" if proj else self._is_write(c)))

    def _bound(self, c, nm: str) -> bool:
        """A parameter of an enclosing function, initializer, subscript or closure is named `nm`."""
        a = c.parent
        while a is not None and a.type not in TYPE_DECLS:
            if a.type in ("function_declaration", "init_declaration", "subscript_declaration", "lambda_literal"):
                for x in a.children:
                    if x.type == "parameter" or x.type == "lambda_function_type":
                        for y in ([x] if x.type == "parameter" else
                                  [z for q in x.children if q.type == "lambda_function_type_parameters"
                                   for z in q.children if z.type == "lambda_parameter"]):
                            n = y.child_by_field_name("name")
                            if n is not None and self.t(n) == nm:
                                return True
            a = a.parent
        return False

    def _shadowed(self, decl: Decl, nm: str, sf: SFile, line: int) -> bool:
        """A local, closure parameter, `case let` binding or loop variable of that name in the member above `line`."""
        if nm in decl.types:
            return True
        txt = "\n".join(sf.src.decode("utf-8", "replace").split("\n")[decl.line - 1:line])
        n = re.escape(nm)
        return bool(re.search(rf"\b(?:let|var)\s+[^=\n{{}}]*?\b{n}\b|\b{n}\s*(?:,\s*\w+\s*)*\)?\s+in\b|"
                              rf"\bfor\s+(?:case\s+)?(?:let\s+)?\(?[\w\s,]*\b{n}\b", txt))

    def _callee(self, c):
        """(receiver text, name, value_arguments node, trailing lambda, line) of a call_expression. The line is the
        call's own line: that of the receiver's last operand when the expression starts on an earlier line
        (`let ok = a` / `  + Scorer.score(x)` parses as `(a + Scorer).score(x)`)."""
        line = c.start_point[0] + 1
        if not c.children:
            return None, None, None, None, line
        f = c.children[0]
        while f.child_by_field_name("rhs") is not None:
            # `a + weight(forExtraIndex: 2)` parses as `(a + weight)(forExtraIndex: 2)`: the callee is the right operand
            f = f.child_by_field_name("rhs")
            line = f.start_point[0] + 1
        suf = next((x for x in c.children if x.type == "call_suffix"), None)
        args = next((x for x in suf.children if x.type == "value_arguments"), None) if suf is not None else None
        lam = next((x for x in suf.children if x.type in ("lambda_literal", "annotated_lambda")), None) if suf is not None else None
        if f.type == "simple_identifier":
            return None, self.t(f), args, lam, line
        if f.type == "navigation_expression":
            target = f.child_by_field_name("target") or f.children[0]
            sfx = next((x for x in f.children if x.type == "navigation_suffix"), None)
            nm = self.t(sfx).lstrip(".").strip() if sfx is not None else None
            if target is None or target.type == "navigation_suffix":
                return "", nm, args, lam, line
            err = next((x for x in f.children if x.type == "ERROR"), None)
            if sfx is not None and (err is not None or target.type in ("<", ">", "<=", ">=")):
                # `let ok = a` / `  < Scorer.score(x)`: a continuation line starting with `<` / `>` does not parse;
                # the receiver is the text between the operator and the member name
                recv = self.cur.src[f.start_byte:sfx.start_byte].decode("utf-8", "replace")
                if err is not None:
                    line = err.start_point[0] + 1
            else:
                recv = self.t(target)
                rhs = target
                while rhs.child_by_field_name("rhs") is not None:      # binary expression: its right operand
                    rhs = rhs.child_by_field_name("rhs")
                if rhs is not target:
                    line = rhs.start_point[0] + 1
            m = re.match(r"\s*([!~+\-<>=&|*/%^?]+)\s*", recv)
            if m and not recv.lstrip().startswith(".."):
                # a prefix operator (`!Preview.matches(a, b)`, `-Offset.value()`) applies to the call's result, not
                # to the receiver; a leading binary operator is the end of a broken continuation line
                recv = recv[m.end():]
            return recv, nm, args, lam, line
        return None, None, args, lam, line

    def _args(self, args) -> list[tuple[str | None, object]]:
        out = []
        for a in (args.children if args is not None else []):
            if a.type == "value_argument":
                lab = next((x for x in a.children if x.type == "value_argument_label"), None)
                val = a.child_by_field_name("value") or next((x for x in reversed(a.children) if x.is_named), None)
                out.append((self.t(lab).strip() if lab is not None else None, val))
        return out

    def _call(self, c, sf: SFile, owner: str, decl: Decl | None, ctx: dict) -> bool:
        recv, name, args, lam, line = self._callee(c)
        if not name or not re.match(r"^\w+$", name):
            return False
        al = self._args(args)
        # ---- SwiftUI / UIKit navigation
        if name == "NavigationLink":
            for lab, v in al:
                if lab == "destination" and v is not None:
                    self._nav_target(v, owner, sf, line, "NavigationLink")
        if name == "NavigationLink" or (name in ("navigate", "push", "append") and recv is not None
                                        and NAV_RECV.search(recv)):
            for lab, v in al[:1] if name != "NavigationLink" else al:
                if v is not None and (name != "NavigationLink" or lab == "value") and \
                        (name != "navigate" or lab == "to"):
                    m = re.match(r"\s*([A-Z]\w*)?\s*\.\s*([a-z]\w*)\b", self.t(v))
                    if m and m.group(2) not in ("init", "self"):
                        self.value_navs.append((owner, m.group(1), m.group(2),
                                                "NavigationLink(value:)" if name == "NavigationLink" else f"{name}(...)",
                                                sf.rel, line))
        if name == "navigationDestination" and lam is not None:
            ty = next((re.match(r"\s*([A-Z][\w.]*)\s*\.\s*self\s*$", self.t(v)) for lab, v in al
                       if lab == "for" and v is not None), None)
            if ty:
                self._destinations(lam, ty.group(1).split(".")[-1], owner, sf, line)
        if name in PRESENT and lam is not None:
            self._lambda_views(lam, owner, sf, line, f".{name}")
        if name in ("pushViewController", "present", "show") and al and al[0][1] is not None:
            self._nav_target(al[0][1], owner, sf, line, name)
        if name in ("WindowGroup", "TabView") and lam is not None and recv is None:
            self._lambda_views(lam, owner, sf, line, name, page_only=name == "WindowGroup")
        # ---- Alamofire
        if name == "request" and recv is not None and re.match(r"^(AF|Alamofire|session|\w*[Ss]ession)$", recv) and al:
            url = self._url_text(al[0][1])
            meth = next((self.t(v).lstrip(".").upper() for lab, v in al if lab == "method" and v is not None), "GET")
            if url is not None:
                self.http.append({"src": owner, "method": meth, "url": url, "client": "alamofire", "file": sf.rel,
                                  "line": line})
        # ---- Moya: provider.request(.case) -> the TargetType case's endpoint
        self._moya_call(name, recv, al, owner, sf, line)
        # ---- Fluent: Todo.query(on:) / .find / todo.save(on:)
        self._fluent_call(name, recv, owner, decl, sf, line, c)
        # ---- BGTaskScheduler
        if name == "register" and "BGTaskScheduler" in (recv or "") and lam is not None:
            hid = self.b.add_node("function", f"<bgtask>@{sf.rel}:{line}", name="background task handler", file=sf.rel,
                                  line=line, lang="swift", entry_kind="queue_job", attrs={"lambda": True})
            self.st["background_tasks"] += 1
            self._refs(lam, sf, hid, decl, ctx)
            return True
        # ---- Vapor routing
        routers = ctx.get("routers")
        if routers is not None and recv is not None:
            base = self._router(recv, routers)
            if base is not None:
                if name in VERBS or name == "on":
                    self._vapor_route(name, al, lam, base, sf, owner, decl, c, ctx)
                    return lam is not None
                if name in ("group", "grouped") and lam is not None:
                    pfx, guards = self._group_args(al, base)
                    pname = self._lambda_param(lam)
                    inner = dict(routers)
                    if pname:
                        inner[pname] = (pfx, guards)
                    self._refs(lam, sf, owner, decl, {**ctx, "routers": inner})
                    return True
        if name[:1].isupper() and recv is None:
            br = self._branch(c)
            if br:
                self.branch_of[(owner, line, name)] = br
                self.branch_at[(sf.rel, line, name)] = br
        self.calls.append((owner, name, recv, line, sf, decl, tuple(lab or "_" for lab, _v in al), lam is not None))
        return False

    def _branch(self, c) -> dict:
        """The innermost `switch` case / `if` / `guard` / ternary branch around a construction (#88): attrs.branch
        (`case .settings`, `if isEditing`, `else of if isEditing`) and attrs.branch_line, so the parent that rebuilds
        or swaps a view under a condition is visible on its INSTANTIATES edge."""
        x, prev = c.parent, c
        while x is not None and x.type not in BRANCH_STOP:
            t = x.type
            if t == "switch_entry":
                pats = [self.t(p) for p in x.children if p.type == "switch_pattern"]
                lab = ("case " + ", ".join(pats)) if pats else "default"
                return {"branch": re.sub(r"\s+", " ", lab)[:80], "branch_line": x.start_point[0] + 1}
            if t in ("if_statement", "guard_statement"):
                ch = x.children
                els = next((k for k in ch if k.type == "else"), None)
                cond = []
                for k in ch[1:]:
                    if k.type in ("{", "else", "statements"):
                        break
                    cond.append(self.t(k))
                kw = "guard" if t == "guard_statement" else "if"
                lab = f"{kw} {' '.join(cond)}"
                if els is not None and prev.start_byte > els.start_byte:
                    lab = "else of " + lab
                return {"branch": re.sub(r"\s+", " ", lab)[:80], "branch_line": x.start_point[0] + 1}
            if t == "ternary_expression" and x.children:
                colon = next((k for k in x.children if k.type == ":"), None)
                lab = f"{self.t(x.children[0])} ?"
                if colon is not None and prev.start_byte > colon.start_byte:
                    lab = "else of " + lab
                if prev is not x.children[0]:
                    return {"branch": re.sub(r"\s+", " ", lab)[:80], "branch_line": x.start_point[0] + 1}
            prev, x = x, x.parent
        return {}

    # ---- Vapor helpers
    def _router(self, recv: str, routers: dict):
        """(prefix, guards) of a router expression: app / routes / req.application, a variable bound to a group, or
        a chained `x.grouped(...)`."""
        recv = recv.strip()
        if recv in routers:
            return routers[recv]
        if re.match(r"^(app|application|routes|router|req\.application|self\.app)$", recv):
            return ("", [])
        m = re.match(r"^(\w+)\s*\.\s*grouped\s*\((.*)\)\s*$", recv, re.S)
        if m:
            base = self._router(m.group(1), routers)
            if base is not None:
                return self._group_text(m.group(2), base)
        return None

    def _group_text(self, inner: str, base):
        pfx, guards = base
        segs = re.findall(r'"([^"]*)"', inner)
        mws = [re.sub(r"\(\s*\)$", "", x.strip()) for x in re.split(r",(?![^()]*\))", inner) if x.strip() and '"' not in x]
        return join_path(pfx, "/".join(vapor_seg(s) for s in segs)), guards + [m for m in mws if m]

    def _group_args(self, al, base):
        return self._group_text(", ".join(self.t(v) for _, v in al if v is not None), base)

    def _router_binding(self, c, ctx: dict):
        """let protected = app.grouped("v1").grouped(User.authenticator()) -> routers["protected"]."""
        routers = ctx.get("routers")
        if routers is None:
            return
        nm = self._name(c)
        txt = self.t(c)
        m = re.match(r"^\s*(?:let|var)\s+\w+\s*(?::[^=]+)?=\s*(.+)$", txt, re.S)
        if not nm or not m:
            return
        expr = m.group(1).strip()
        parts = re.match(r"^([\w.]+)((?:\s*\.\s*grouped\s*\((?:[^()]|\([^()]*\))*\))+)\s*$", expr, re.S)
        if not parts:
            return
        base = self._router(parts.group(1), routers)
        if base is None:
            return
        for g in re.findall(r"grouped\s*\(((?:[^()]|\([^()]*\))*)\)", parts.group(2)):
            base = self._group_text(g, base)
        routers[nm] = base

    def _lambda_param(self, lam) -> str | None:
        m = re.match(r"\{\s*\(?\s*(\w+)", self.t(lam))
        return m.group(1) if m and re.search(r"\bin\b", self.t(lam).split("\n")[0]) else None

    def _vapor_route(self, verb, al, lam, base, sf, owner, decl, c, ctx):
        pfx, guards = base
        segs, handler, method = [], None, verb.upper()
        for lab, v in al:
            if v is None:
                continue
            if lab is None and v.type == "line_string_literal":
                segs.append(vapor_seg(template(self.t(v))))
            elif lab == "use":
                handler = self.t(v)
            elif lab is None and verb == "on":
                method = self.t(v).lstrip(".").upper()
        uri = "/" + join_path(pfx, "/".join(segs)).strip("/")
        line = c.start_point[0] + 1
        key = f"{method} {uri}"
        attrs = {"uri": uri, "method": method, "framework": "vapor"}
        if guards:
            attrs["middleware"] = list(dict.fromkeys(guards))
        rid = self.b.add_node("route", key, name=key, file=sf.rel, line=line, lang="swift", entry_kind="http_route",
                              attrs=attrs)
        self.b.nodes[rid].entry_kind = self.b.nodes[rid].entry_kind or "http_route"
        self.st["routes"] += 1
        self.st["routes_vapor"] += 1
        self.b.add_edge(owner, rid, "REFERENCES_FN", sf.rel, line, EXACT, how="router registration")
        if lam is not None:
            hk = f"<{key}>@{sf.rel}:{line}"
            hid = self.b.add_node("function", hk, name=f"{key} handler", fqn=hk, file=sf.rel, line=line,
                                  end_line=c.end_point[0] + 1, lang="swift",
                                  attrs={"lambda": True, "swift_kind": "route handler", **({"test": True} if sf.test else {})})
            self.b.add_edge(rid, hid, "ROUTES_TO", sf.rel, line, EXACT)
            self._refs(lam, sf, hid, decl, {**ctx, "routers": None})
        elif handler:
            h = re.sub(r"^self\.", "", handler).split("(")[0]
            tfq = decl.cls if decl is not None and decl.cls else (decl.fqn if decl is not None and decl.kind == "class" else None)
            if tfq:
                self.handler_refs.append((rid, h, tfq, sf.rel, line))

    # ---- navigation helpers
    def _nav_target(self, v, owner, sf, line, how):
        m = re.match(r"\s*(?:\w+\s*\.\s*)?([A-Z]\w*)\s*\(", self.t(v))
        if m:
            self.navs.append((owner, m.group(1), how, sf.rel, line))

    def _view_names(self, stm) -> list[str]:
        out = []
        for x in (stm.children if stm is not None else []):
            node = x
            while node.type in ("call_expression", "navigation_expression") and node.children and \
                    node.children[0].type in ("call_expression", "navigation_expression"):
                node = node.children[0]
            m = re.match(r"\s*([A-Z]\w*)\s*\(", self.t(node))
            if m and m.group(1) not in CONTAINER_VIEWS:
                out.append(m.group(1))
        return out

    def _destinations(self, lam, tname: str, owner, sf, line):
        """`navigationDestination(for: Route.self) { r in switch r { case .detail(let id): DetailView(id: id) } }`:
        the views per case of Route, for `NavigationLink(value: Route.detail(...))` / `router.navigate(to: .detail)`
        (#68). The modifier's holder navigates to every one of them."""
        stm = next((x for x in lam.children if x.type == "statements"), None)
        for sw in (stm.children if stm is not None else []):
            if sw.type != "switch_statement":
                continue
            for e in sw.children:
                if e.type != "switch_entry":
                    continue
                cases = []
                for sp in e.children:
                    if sp.type == "switch_pattern":
                        m = re.match(r"\s*(?:[A-Z]\w*)?\s*\.\s*(\w+)", self.t(sp))
                        if m:
                            cases.append(m.group(1))
                if not cases and not any(x.type == "default_keyword" or self.t(x) == "default" for x in e.children):
                    continue
                views = self._view_names(next((x for x in e.children if x.type == "statements"), None))
                for v in views:
                    for cs in cases or [None]:
                        self.dest_map[tname][cs].append((v, sf.rel, line))
                    self.navs.append((owner, v, ".navigationDestination(for:)", sf.rel, line))
                    self.st["navigation_destinations"] += 1
        for v in self._view_names(stm):              # no switch: one view for every value
            self.dest_map[tname][None].append((v, sf.rel, line))

    def _lambda_views(self, lam, owner, sf, line, how, page_only=False):
        stm = next((x for x in lam.children if x.type == "statements"), None)
        for x in (stm.children if stm is not None else []):
            node = x
            while node.type in ("call_expression", "navigation_expression") and node.children and \
                    node.children[0].type in ("call_expression", "navigation_expression"):
                node = node.children[0]          # View(...).tabItem { } -> View(...)
            m = re.match(r"\s*([A-Z]\w*)\s*\(", self.t(node))
            if m and m.group(1) not in CONTAINER_VIEWS:
                self.navs.append((owner, m.group(1), how, sf.rel, line, page_only))

    # ---- URLSession
    def _url_text(self, v) -> str | None:
        if v is None:
            return None
        txt = self.t(v)
        if v.type == "line_string_literal":
            return template(txt)
        m = re.search(r'URL\s*\(\s*string\s*:\s*("(?:[^"\\]|\\.)*")', txt)
        if m:
            return template(m.group(1))
        return None

    COMP_INIT = re.compile(r"(\w+)\s*=\s*URLComponents\s*\(\s*(?:string\s*:\s*(\"(?:[^\"\\]|\\.)*\"))?")

    def _components_url(self, txt: str) -> str | None:
        """`var c = URLComponents(string: "https://api.x.com")` / `URLComponents()` with `c.scheme` / `c.host` /
        `c.path = "/v1/users/\\(id)"` set after it (#68); query items are not part of the route."""
        m = self.COMP_INIT.search(txt)
        if not m:
            return None
        v = re.escape(m.group(1))

        def field(f):
            fm = re.search(rf"\b{v}\s*\.\s*{f}\s*=\s*(\"(?:[^\"\\]|\\.)*\")", txt)
            return template(fm.group(1)) if fm else None
        base = template(m.group(2)) if m.group(2) else None
        path = field("path")
        if base is None:
            host = field("host")
            if host is None and path is None:
                return None
            base = (f"{field('scheme') or 'https'}://{host}" if host else "")
        if path is None:
            return base or None
        return base.rstrip("/") + "/" + path.lstrip("/")

    def _fn_http(self, fn, sf: SFile, d: Decl):
        txt = self.t(fn)
        if not URLSESSION.search(txt) or "URLSession" not in txt and "session" not in txt:
            return
        url = None
        for m in re.finditer(r'URL\s*\(\s*string\s*:\s*("(?:[^"\\]|\\.)*")', txt):
            url = template(m.group(1))
        if url is None:
            url = self._components_url(txt)
        if url is None:
            base = re.search(r"(\w+)\s*\.\s*appending(?:PathComponent|Path)?\s*\(\s*(?:path\s*:\s*)?(\"(?:[^\"\\]|\\.)*\")", txt)
            if base:
                url = "{" + base.group(1) + "}/" + template(base.group(2)).lstrip("/")
        if url is None:
            self.st["urlsession_url_unknown"] += 1
            return
        mm = re.search(r'httpMethod\s*=\s*"(\w+)"', txt)
        self.http.append({"src": d.id, "method": (mm.group(1) if mm else "GET").upper(), "url": url, "client": "urlsession",
                          "file": sf.rel, "line": d.line})

    # ---- @available(macOS, unavailable): the declaration (and its members) does not exist on that platform;
    # @available(*, unavailable): on none. Version forms (`@available(iOS 17, *)`, `@available(iOS, introduced: 15)`,
    # `if #available(iOS 17, *)`, `guard #available`, `if #unavailable`) keep the code on every target and record the
    # minimum OS versions: node attrs.available on the declaration (members inherit the type's), edge attrs.available
    # on the references in the guarded branch. `deprecated` is kept as attrs.deprecated.
    AVAILABLE = re.compile(r"@available\s*\(\s*(iOS|macOS|OSX)\s*,\s*unavailable\b")
    AVAIL_ATTR = re.compile(r"@available\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
    AVAIL_VER = re.compile(r"\b(iOS|iPadOS|macOS|OSX|watchOS|tvOS|visionOS|macCatalyst)(?:ApplicationExtension)?\s*"
                           r"(?:,\s*introduced\s*:\s*)?(\d+(?:\.\d+)*)")

    @staticmethod
    def _vmax(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            if k not in out or tuple(int(x) for x in v.split(".")) > tuple(int(x) for x in out[k].split(".")):
                out[k] = v
        return out

    def _available(self, sf: SFile):
        if b"available" not in sf.src:
            return
        from ...platforms import KNOWN, Cond, _plat_atom, mark
        lines = sf.src.decode("utf-8", "replace").split("\n")
        decl_av = []
        for d in sorted(self._fd.get(sf.rel, ()), key=lambda d: d.line - d.end):     # outer declarations first
            head = "\n".join(lines[d.line - 1:d.end]).split("{", 1)[0]
            for m in self.AVAILABLE.finditer(head):
                plat = OS_PLATFORM[m.group(1)]
                if plat == "macos" and self.mac == "catalyst":
                    continue                 # `@available(macOS, unavailable)` does not apply to a Catalyst build
                atom = self._apple(plat) if plat == "ios" else _plat_atom(plat)   # iOS-unavailable: Catalyst too
                mark(self.b, sf.rel, d.line, d.end, Cond("tree", ("not", atom), m.group(0) + ")"))
                self.st["platform_unavailable"] += 1
            av, dep = {}, None
            for m in self.AVAIL_ATTR.finditer(head):
                args = m.group(1)
                if re.match(r"\s*\*\s*,\s*unavailable\b", args):
                    mark(self.b, sf.rel, d.line, d.end, Cond("tree", ("all", [("not", _plat_atom(p)) for p in KNOWN]),
                                                             "@available(*, unavailable)"))
                    self.st["platform_unavailable_everywhere"] += 1
                    continue
                if re.search(r"\bdeprecated\b", args):
                    mm = re.search(r'message\s*:\s*"((?:[^"\\]|\\.)*)"', args)
                    dep = mm.group(1)[:160] if mm else True
                if "unavailable" not in args and "obsoleted" not in args:
                    for v in self.AVAIL_VER.finditer(args):
                        av = self._vmax(av, {"macOS" if v.group(1) == "OSX" else v.group(1): v.group(2)})
            for a, z, pav in decl_av:                      # members inherit the enclosing type's availability
                if a <= d.line and d.end <= z and (a, z) != (d.line, d.end):
                    av = self._vmax(pav, av)
            if av:
                decl_av.append((d.line, d.end, av))
            n = self.b.nodes.get(d.id)
            if n is not None and (av or dep):
                n.attrs = dict(n.attrs or {})
                if av:
                    n.attrs["available"] = av
                    self.st["available_declarations"] += 1
                if dep:
                    n.attrs["deprecated"] = dep
        if b"#available" not in sf.src and b"#unavailable" not in sf.src:
            return
        regions = self._avail_regions.setdefault(sf.rel, [])

        def walk(n):
            for c in n.children:
                if c.type == "availability_condition":
                    self._avail_branch(c, regions)
                walk(c)
        walk(sf.tree.root_node)

    def _avail_branch(self, c, regions: list):
        txt = self.t(c)
        av = {}
        for v in self.AVAIL_VER.finditer(txt):
            av = self._vmax(av, {"macOS" if v.group(1) == "OSX" else v.group(1): v.group(2)})
        if not av:
            return
        neg = "#unavailable" in txt.replace(" ", "")
        p = c.parent
        if p is None:
            return
        kids = p.children
        i = next((k for k, x in enumerate(kids) if x.id == c.id), None)
        if i is None:
            return
        if p.type == "if_statement":
            opens = [k for k in range(i + 1, len(kids)) if kids[k].type == "{"]
            closes = [k for k in range(i + 1, len(kids)) if kids[k].type == "}"]
            if not opens or not closes:
                return
            if not neg:
                a, z = kids[opens[0]].start_point[0] + 1, kids[closes[0]].start_point[0] + 1
            else:                              # `if #unavailable(iOS 17) { old } else { new }`: the else branch
                if len(opens) < 2:
                    return
                a, z = kids[opens[1]].start_point[0] + 1, p.end_point[0] + 1
        elif p.type == "guard_statement" and not neg:
            stmts = p.parent
            a, z = p.end_point[0] + 2, (stmts.end_point[0] + 1 if stmts is not None else p.end_point[0] + 1)
        else:
            return
        if z >= a:
            regions.append((a, z, av))
            self.st["available_branches"] += 1

    def _apply_available(self):
        """References inside `if #available(...)` / after `guard #available(...)`: edge attrs.available."""
        if not any(self._avail_regions.values()):
            return
        for e in self.b.edges.values():
            regs = self._avail_regions.get(e.file)
            if not regs or e.line is None:
                continue
            av = {}
            for a, z, v in regs:
                if a <= e.line <= z:
                    av = self._vmax(av, v)
            if av:
                e.attrs = {**(e.attrs or {}), "available": av}
                self.st["available_references"] += 1

    # ---- #if os(...)
    def _directives(self, sf: SFile):
        from ...platforms import Cond, _plat_atom, mark
        dirs = []
        def walk(n):
            for c in n.children:
                if c.type == "directive":
                    dirs.append(c)
                else:
                    walk(c)
        walk(sf.tree.root_node)
        stack = []        # [(start line, cond text, previous conds)]
        for d in dirs:
            txt = self.t(d).strip()
            line = d.start_point[0] + 1
            kw = re.match(r"#(if|elseif|else|endif)\b\s*(.*)", txt)
            if not kw:
                continue
            k, expr = kw.group(1), kw.group(2)
            if k in ("elseif", "else", "endif") and stack:
                start, cur, prev, ctext = stack.pop()
                if cur is not None:
                    mark(self.b, sf.rel, start, line - 1, Cond("tree", cur, ctext))
                    self.st["platform_blocks"] += 1
                prev = prev + ([cur] if cur is not None else [])
                if k == "endif":
                    continue
                new = self._os_expr(expr) if k == "elseif" else None
                if k == "else":
                    new = ("all", [("not", p) for p in prev]) if prev else None
                elif new is not None and prev:
                    new = ("all", [new] + [("not", p) for p in prev])
                stack.append((line + 1, new, prev, txt))
            elif k == "if":
                stack.append((line + 1, self._os_expr(expr), [], txt))
        for first, last, iftxt in sf.attr_blocks:
            # `#if os(macOS)` / `@Test` / `#endif` / `func f()`: f is declared under that condition
            kw = re.match(r"#if\b\s*(.*)", iftxt or "")
            cond = self._os_expr(kw.group(1)) if kw else None
            if cond is None:
                continue
            for d in self._fd.get(sf.rel, ()):
                if first < d.line < last:
                    mark(self.b, sf.rel, d.line, d.end, Cond("tree", cond, iftxt))
                    self.st["platform_blocks"] += 1

    def _os_expr(self, expr: str):
        """Platform condition tree of a `#if` expression: `os()`, `canImport()`, `targetEnvironment()` joined by
        `||`, `&&`, `!` and parentheses (`(os(iOS) && canImport(CoreTelephony)) || os(tvOS)`, #86). An `||` with a
        term that names no platform is unknown; such a term in an `&&` is left out (`os(iOS) && DEBUG`: iOS), except
        under a `!` where leaving it out would claim too much."""
        expr = re.sub(r"//.*|/\*.*?\*/", "", expr).strip()
        toks = re.findall(r"\|\||&&|!|\(|\)|[^\s()!&|]+(?:\s*\([^()]*\))?", expr)
        pos = [0]

        def peek():
            return toks[pos[0]] if pos[0] < len(toks) else None

        def take():
            pos[0] += 1
            return toks[pos[0] - 1]

        def p_or(strict):
            parts = [p_and(strict)]
            while peek() == "||":
                take()
                parts.append(p_and(strict))
            if len(parts) == 1:
                return parts[0]
            return ("any", parts) if all(parts) else None

        def p_and(strict):
            parts = [p_not(strict)]
            while peek() == "&&":
                take()
                parts.append(p_not(strict))
            if len(parts) == 1:
                return parts[0]
            known = [x for x in parts if x]
            if not known or (strict and len(known) < len(parts)):
                return None
            return ("all", known) if len(known) > 1 else known[0]

        def p_not(strict):
            t = peek()
            if t == "!":
                take()
                if peek() not in ("(", "!") and peek() is not None:
                    return self._os_atom(take(), True)
                x = p_not(True)
                return ("not", x) if x else None
            if t == "(":
                take()
                x = p_or(strict)
                if peek() == ")":
                    take()
                return x
            if t is None:
                return None
            return self._os_atom(take(), False)

        try:
            r = p_or(False)
        except RecursionError:
            return None
        return r if pos[0] == len(toks) else None

    def _os_atom(self, expr: str, neg: bool):
        from ...platforms import _plat_atom
        m = re.match(r"(os|canImport|targetEnvironment)\s*\(\s*([\w.]+)\s*\)$", expr)
        if not m:
            return None
        fn, arg = m.groups()
        if fn == "os":
            a = self._apple(OS_PLATFORM.get(arg))
        elif fn == "canImport":
            if arg in APPLE_ONLY or arg == "FoundationNetworking":
                apple = ("any", [_plat_atom(p) for p in ("macos", "ios", "tvos", "watchos", "visionos")])
                return apple if (arg in APPLE_ONLY) != neg else ("not", apple)
            plat = IMPORT_PLATFORM.get(arg)
            if isinstance(plat, tuple):        # UIKit: the iOS family, and the Mac when the app builds for Catalyst
                a = ("any", [self._apple(p) for p in plat] + ([self._apple("catalyst")] if self.mac in ("catalyst", "both") else []))
            elif plat == "macos" and self.mac == "catalyst":
                a = ("atom", "unknown_on", "macos")      # AppKit in a Catalyst-only app: may or may not import
            else:
                a = _plat_atom(plat) if plat else None
        else:                       # Mac Catalyst: the iOS app built for macOS; simulator: not a platform
            a = self._apple("catalyst") if arg == "macCatalyst" else None
        if a is None:
            return None
        return ("not", a) if neg else a

    def _apple(self, plat: str | None):
        """Atom of an Apple platform name for this project (#74): `os(macOS)` is the AppKit target (never true in a
        Catalyst-only app), `os(iOS)` is also true on the Mac when the app builds for Catalyst, and
        `targetEnvironment(macCatalyst)` is the Mac only then; with both a native and a Catalyst Mac build those are
        unknown on macOS. tvOS, watchOS and visionOS are their own targets."""
        from ...platforms import _plat_atom
        if plat is None:
            return None
        unk = ("atom", "unknown_on", "macos")
        if plat == "macos":
            return {"catalyst": ("atom", "never", None), "both": unk}.get(self.mac, _plat_atom("macos"))
        if plat == "ios":
            return {"catalyst": ("any", [_plat_atom("ios"), _plat_atom("macos")]),
                    "both": ("any", [_plat_atom("ios"), unk])}.get(self.mac, _plat_atom("ios"))
        if plat == "catalyst":
            return {"catalyst": _plat_atom("macos"), "both": unk}.get(self.mac, ("atom", "never", None))
        return _plat_atom(plat)

    # ------------------------------------------------------------------ resolution
    def _member(self, cls: Decl | None, name: str, depth: int = 0) -> list[Decl]:
        if cls is None:
            return []
        hit = self.members.get(cls.fqn, {}).get(name)
        if hit:
            return hit
        if depth > 6:
            return []
        if "extension-only" not in cls.modifiers:
            # `extension Models.Notification.NotificationType { ... }` (module-qualified) extends the nested type
            for e in self._qualified_ext().get(cls.fqn, []):
                hit = self.members.get(e.fqn, {}).get(name)
                if hit:
                    return hit
        for s in cls.supers:
            sc = self.types.get(s)
            if sc is not None and sc is not cls:
                r = self._member(sc, name, depth + 1)
                if r:
                    return r
        return []

    def _qualified_ext(self) -> dict:
        """Extensions written with a qualified name (`extension Models.Notification.NotificationType`) by the fqn of
        the project type they extend (`Notification.NotificationType`)."""
        if getattr(self, "_qext", None) is None:
            self._qext = defaultdict(list)
            real = [d for d in self.types.values() if "extension-only" not in d.modifiers]
            for e in self.types.values():
                if "extension-only" in e.modifiers and "." in e.fqn:
                    for t in real:
                        if e.fqn.endswith("." + t.fqn):
                            self._qext[t.fqn].append(e)
        return self._qext

    def _resolve_calls(self):
        for owner, name, recv, line, sf, decl, labels, trailing in self.calls:
            self._how = self._recv = None
            self._at = (sf, line)
            targets = [t for t in self._targets(name, recv, decl, labels, trailing) if self._sees(t, True)]
            how = {"binding": self._how} if self._how else {}
            # the receiver's type when the member was found on an ancestor: `impact Sub.m` narrows by it (#62)
            if self._recv is not None and any(t.kind != "class" and t.cls and t.cls != self._recv.fqn for t in targets):
                how["recv"] = [self._recv.id]
            if self._how == "candidate":
                how["candidates"] = len(targets)
            if not targets:
                self.st["calls_unresolved"] += 1
                continue
            done = False
            for t in targets[:MAX_CANDIDATES if self._how == "candidate" else 3]:
                if t.kind == "class":
                    hit = self._first_variants([m for m in self.members.get(t.fqn, {}).get("init", [])
                                                if self._fits(m.id, labels, trailing) and self._sees(m)])
                    if "extension-only" in t.modifiers and not hit:
                        # a type the project only extends (String, Data, URL, JSONDecoder, ...): the call uses one of
                        # the SDK's initializers, not a project one
                        self.st["calls_sdk_initializer"] += 1
                        continue
                    self.b.add_edge(owner, t.id, "INSTANTIATES", sf.rel, line, HEURISTIC,
                                    **self.branch_of.get((owner, line, name), {}))
                    for m in hit:
                        self.b.add_edge(owner, m.id, "CALLS", sf.rel, line, HEURISTIC)
                else:
                    self.b.add_edge(owner, t.id, "CALLS", sf.rel, line, HEURISTIC, **how, **self._accessor_attr(decl, line))
                    if how:
                        self.st["calls_by_name_only" if self._how == "name" else "call_candidate_edges"] += 1
                done = True
            self.st["calls_resolved" if done else "calls_unresolved"] += 1

    def _prop_member(self, cls: Decl | None, name: str, depth: int = 0) -> list[Decl]:
        """Property nodes named `name` of a type, its extensions and its supertypes (protocol extensions too)."""
        if cls is None or name in self.stored_in.get(cls.fqn, ()):
            return []
        hit = self.props.get(cls.fqn, {}).get(name)
        if hit:
            return hit
        if depth > 6:
            return []
        for e in self._qualified_ext().get(cls.fqn, []) if "extension-only" not in cls.modifiers else ():
            if name in self.stored_in.get(e.fqn, ()):
                return []
            hit = self.props.get(e.fqn, {}).get(name)
            if hit:
                return hit
        for s in cls.supers:
            sc = self.types.get(s)
            if sc is not None and sc is not cls:
                r = self._prop_member(sc, name, depth + 1)
                if r:
                    return r
        return []

    def _prop_targets(self, name: str, recv: str | None, decl: Decl | None) -> list[Decl]:
        """Property nodes a read reaches, with the receiver rules of method calls (#70, #83): a known receiver type
        binds exactly or not at all; an unknown one only by a name that no stored / SDK property shares."""
        cls = self._encl_type(decl)
        if recv is None or recv in ("self", "Self", "super"):
            own = self._prop_member(cls, name)
            want = True if recv == "Self" else False if recv in ("self", "super") else \
                bool(decl is not None and decl.kind != "class" and _static(decl))
            return [d for d in own if _static(d) == want] or (own if recv is None else [])
        kind, tname = self._recv_type(recv, decl, cls)
        if kind in ("type", "meta"):
            return [d for d in self._prop_member(self._type(tname), name) if _static(d) == (kind == "meta")]
        if kind == "sdk":
            tc = self.types.get(tname) if tname else None
            if tc is not None and "extension-only" in tc.modifiers:
                own = self._prop_member(tc, name)
                if own:
                    return own
            if tname is None and (name in self.stored_names or name in COMMON_PROPS):
                return []                    # `conn.data.database`: some SDK value's own (stored) property
            cands = [d for d in self._all_props(name) if d.cls in self.types
                     and "extension-only" in self.types[d.cls].modifiers and not _static(d)
                     and (not self._certain or d.cls not in CONCRETE_SDK or d.cls == tname)]
            return cands if len(cands) == 1 else []
        if name in self.stored_names or name in COMMON_PROPS:
            return []
        cands = self._first_variants([d for d in self._all_props(name) if not _static(d)])
        if not cands or len(cands) > MAX_CANDIDATES:
            return []
        self._how = "name" if len(cands) == 1 else "candidate"
        return cands

    def _all_props(self, name: str) -> list[Decl]:
        return [d for by in self.props.values() for d in by.get(name, ()) if self._sees(d)]

    def _resolve_props(self):
        """CALLS edges for reads of computed / lazy properties and writes of computed / observed ones (`property`:
        `read` | `write`); a read of a stored property with observers runs no code of it."""
        for owner, name, recv, line, sf, decl, write in self.prop_refs:
            if name not in self.prop_names or write == "keypath":
                self._field_ref(owner, name, recv, line, sf, decl, write)
                continue
            self._how = None
            self._at = (sf, line)
            targets = [t for t in self._prop_targets(name, recv, decl) if self._sees(t, True)]
            how = {"binding": self._how} if self._how else {}
            if self._how == "candidate":
                how["candidates"] = len(targets)
            done = False
            # an in-place mutation (`items.append(x)`, `&x`, `$x`) fires an observer's didSet and, on a lazy var,
            # still runs its initializer first; only a plain assignment skips the lazy initializer
            for t in targets[:MAX_CANDIDATES]:
                if t.id == owner or ("observed" in t.modifiers and not write) or ("lazy" in t.modifiers and write is True):
                    continue
                self.b.add_edge(owner, t.id, "CALLS", sf.rel, line, HEURISTIC, property="write" if write else "read",
                                **how, **self._accessor_attr(decl, line))
                done = True
            self.st["property_refs_resolved" if done else "property_refs_unresolved"] += 1
            if not done and name in self.field_names:
                self._field_ref(owner, name, recv, line, sf, decl, write)

    def _field_member(self, cls: Decl | None, name: str, depth: int = 0) -> str | None:
        """The field node of a stored property `name` of a type or its superclasses."""
        if cls is None or depth > 6:
            return None
        fid = self.fields.get(cls.fqn, {}).get(name)
        if fid:
            return fid
        for s in cls.supers:
            sc = self.types.get(s)
            if sc is not None and sc is not cls:
                r = self._field_member(sc, name, depth + 1)
                if r:
                    return r
        return None

    def _field_ref(self, owner, name, recv, line, sf, decl, write):
        """READS_PROP / WRITES_PROP to a stored property (#88): `self.x` / a bare `x` inside its type, or `v.x`
        where the type of `v` is known. An unknown receiver binds nothing (stored names are far too common)."""
        cls = self._encl_type(decl)
        fid = None
        if write == "keypath":
            fid = self._field_member(self._type(recv[1:].split(".")[-1]), name)
        elif recv is None or recv in ("self", "super"):
            fid = self._field_member(cls, name)
        elif recv not in ("Self",):
            self._at = (sf, line)
            kind, tname = self._recv_type(recv, decl, cls)
            if kind == "type":
                fid = self._field_member(self._type(tname), name)
        if fid is None or fid == owner:
            self.st["stored_property_refs_unresolved"] += 1
            return
        via = write if isinstance(write, str) else None
        write = bool(write) and via != "keypath"     # a key path names the property; whether it writes is unknown
        self.b.add_edge(owner, fid, "WRITES_PROP" if write else "READS_PROP", sf.rel, line, RESOLVED,
                        **({"receiver": "self"} if recv in (None, "self", "super") else {"receiver": recv[:40]}),
                        **({"via": via} if via else {}),
                        **({"storage": "wrapper"} if name.startswith("_") else {}),
                        **self._accessor_attr(decl, line))
        self.st["stored_property_writes" if write else "stored_property_reads"] += 1

    @staticmethod
    def _accessor_attr(decl: Decl | None, line: int) -> dict:
        """{"accessor": "didSet"} for a reference inside an explicit accessor of a property node."""
        for a, b, name in (decl.ranges if decl is not None else ()):
            if a <= line <= b:
                return {"accessor": name}
        return {}

    @staticmethod
    def _first_variants(ds: list) -> list:
        """Of the per-`#if` definitions of one overload (`init`, `init@42`, `init@48` that fit the call), the first:
        the platform pass links its sibling variants (attrs.platform_variant_of)."""
        best: dict = {}
        for d in ds:
            k = d.id.split("@")[0]
            if k not in best or d.line < best[k].line:
                best[k] = d
        return list(best.values())

    def _fits(self, did: str, labels: tuple, trailing: bool) -> bool:
        """Some signature of `did` accepts the call's argument labels (unknown signature: yes). Parameters with a
        default value, closures and variadics may be left out; a trailing closure fills a closure parameter."""
        sigs = self.sigs.get(did)
        if not sigs:
            return True
        for sig in sigs:
            i = 0
            ok = True
            for k, (lab, opt) in enumerate(sig):
                if i < len(labels) and labels[i] == lab:
                    i += 1
                elif trailing and i == len(labels) and all(o for _l, o in sig[k + 1:]):
                    trailing = False          # the trailing closure is this parameter (a closure typealias)
                elif not opt:
                    ok = False
                    break
            if ok and i == len(labels):
                return True
        return False

    def _encl_type(self, decl: Decl | None) -> Decl | None:
        if decl is None:
            return None
        if decl.kind == "class":
            return decl
        return self.types.get(decl.cls) if decl.cls else None

    @staticmethod
    def _selector(name: str, labels: tuple, trailing: bool) -> str:
        return f"{name}({''.join(l + ':' for l in labels)}{'_:' if trailing and labels else ''})" if labels or not trailing \
            else f"{name}(_:)"

    @staticmethod
    def _segments(recv: str) -> list[str]:
        """`a.b(x).c[0]!` -> ['a', 'b(x)', 'c[0]!'] (top-level dots; of a binary expression only the right operand)."""
        depth, quote, cut, parts = 0, False, 0, []
        txt = recv.strip()
        for i, ch in enumerate(txt):
            if ch == '"':
                quote = not quote
            if quote:
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0:
                if ch == "." and not (i > 0 and txt[i - 1].isdigit()):
                    parts.append(txt[cut:i])
                    cut = i + 1
                elif ch in "+-*/%<>=&|?" and i > 0 and txt[i - 1] in " \t\n" and re.match(r"[+\-*/%<>=&|?]+\s", txt[i:]):
                    parts, cut = [], i + len(re.match(r"[+\-*/%<>=&|?]+\s*", txt[i:]).group(0))
        parts.append(txt[cut:])
        return [p.strip() for p in parts if p.strip()]

    def _type(self, name: str | None) -> Decl | None:
        """A project type by fqn or, for nested types (`StatusEditor.TextEditingService` recorded as
        `TextEditingService`), by its unique simple name."""
        if not name:
            return None
        t = self.types.get(name)
        if t is not None:
            return t
        cs = {d.fqn: d for d in self.by_name.get(name.split(".")[-1], []) if d.kind == "class"}
        return next(iter(cs.values())) if len(cs) == 1 else None

    def _sdk_or_unknown(self, tn: str) -> tuple[str, str | None]:
        """A type name cg could not resolve to one project type: an SDK type unless the project declares a type of
        that simple name (several nested ones, `Configuration`): then unknown."""
        if any(d.kind == "class" and "extension-only" not in d.modifiers for d in self.by_name.get(tn.split(".")[-1], [])):
            return "unknown", None
        return "sdk", tn

    def _local(self, decl: Decl | None, ident: str) -> str | None:
        """The type of a function's local variable at the call being resolved: the nearest declaration of the name
        above the call (`let g = SlotGate<String>()`, `let p = Path()`, `var inside = false`, `let n: Int = ...`,
        `var puzzle = Puzzle(archivableCubes: puzzle)` over the parameter); None when that declaration's type is not
        readable or there is none."""
        sf, line = getattr(self, "_at", (None, 0))
        if decl is None or sf is None or decl.kind == "class" or sf.rel != decl.file:
            return None
        cache = self.__dict__.setdefault("_loc_cache", {})
        key = (decl.id, decl.file, decl.line)
        if key not in cache:
            txt = "\n".join(sf.src.decode("utf-8", "replace").split("\n")[decl.line - 1:decl.end])
            out: dict[str, list] = defaultdict(list)
            found = []
            for m in LOCAL_DECL.finditer(txt):
                nm, ann, init, lit = m.group(1), m.group(2), m.group(3), m.group(4)
                tn = ann.split(".")[-1] if ann else init
                if tn is None and lit:
                    tn = next(t for t, rx in LITERAL_TYPES if re.match(rx, lit))
                found.append((m.start(), nm, tn))
            for m in CLOSURE_PARAM.finditer(txt):         # `Path { p in p.close() }`: p is a Path
                found.append((m.start(), m.group(2), CLOSURE_PARAM_TYPES[m.group(1)]))
            for pos, nm, tn in sorted(found, key=lambda x: x[0]):
                out[nm].append((decl.line + txt.count("\n", 0, pos), tn))
            cache[key] = out
        best = None
        for ln, tn in cache[key].get(ident, ()):
            if ln <= line:
                best = tn
        return best

    def _recv_type(self, recv: str, decl: Decl | None, cls: Decl | None) -> tuple[str, str | None]:
        """What a receiver expression is: ("type", T) a value of project type T, ("meta", T) the project type T itself
        (static members), ("sdk", T | None) a value of an SDK type (T when known; modifier chains on SDK views,
        `Font.body`, `UIApplication.shared`), ("unknown", None). `a?.b` / `a!.b` read as `a.b`."""
        segs = self._segments(recv)
        self._certain = False
        if not segs:
            return "unknown", None
        state: tuple[str, str | None] = ("unknown", None)
        for i, seg in enumerate(segs):
            seg = seg.rstrip("!?")
            m = re.match(r"^([A-Za-z_]\w*)\s*(.*)$", seg, re.S)
            if not m:
                return "unknown", None
            ident, rest = m.group(1), m.group(2).strip()
            called = rest.startswith(("(", "{"))
            sub = rest.startswith("[")
            if i == 0:
                if ident in ("self", "Self", "super") and not rest:
                    if cls is None:
                        return "unknown", None
                    state = ("type", cls.fqn) if "extension-only" not in cls.modifiers else ("sdk", cls.fqn)
                    continue
                if ident[:1].isupper():
                    t = self._type(ident)
                    if t is not None and "extension-only" not in t.modifiers:
                        state = ("type", t.fqn) if called else ("meta", t.fqn)
                    else:                            # an SDK type, its initializer or a value built from it
                        state = ("sdk", ident) if t is not None else self._sdk_or_unknown(ident)
                        if called and len(segs) == 1 and state[0] == "sdk":
                            self._certain = True     # `UIGraphicsPDFRenderer(bounds: r).pdfData { }`: that type
                    if sub:
                        state = ("unknown", None)
                    continue
                if called:                            # a free function's result
                    return "unknown", None
                ltn = self._local(decl, ident)
                tn = ltn or (decl.types.get(ident) if decl is not None else None) or \
                    (cls.types.get(ident) if cls is not None else None)
                if tn is None or sub:
                    return "unknown", None
                t = self._type(tn)
                state = ("type", t.fqn) if t is not None and "extension-only" not in t.modifiers else \
                    ("sdk", tn) if t is not None else self._sdk_or_unknown(tn)
                if ltn and state[0] == "sdk":
                    if len(segs) > 1:          # a property chain off a local SDK value may reach project types
                        return "unknown", None
                    self._certain = True       # `var inside = false`, `let p = Path()`: that type, nothing else
                continue
            kind, tname = state
            if kind == "sdk":
                if sub:
                    return "unknown", None
                # `Font.body`, `UIApplication.shared`: a static property of an SDK type is a value of that type;
                # anything further down a chain (`Text("x").font(...)`) is some SDK value
                state = ("sdk", tname if not called and i == 1 and segs[0][:1].isupper() else None)
                continue
            if kind in ("type", "meta") and not called and not sub:
                tc = self.types.get(tname)
                tn = tc.types.get(ident) if tc is not None else None
                t = self._type(tn)
                if t is not None and "extension-only" not in t.modifiers:
                    state = ("type", t.fqn)
                    continue
                if tn:
                    state = ("sdk", tn) if t is not None else self._sdk_or_unknown(tn)
                    if state[0] == "unknown":
                        return state
                    continue
            return "unknown", None
        return state

    def _fitting(self, ds: list, labels: tuple, trailing: bool) -> list:
        return [d for d in ds if self._fits(d.id, labels, trailing) and self._sees(d, True)]

    def _dispatch_visible(self, d: Decl, caller: str, depth: int = 0) -> bool:
        """A member of a type outside the caller's modules that overrides or implements a member of a supertype the
        caller does see (an `EventMonitor` conformer in the tests): reached at run time through dynamic dispatch."""
        t = self.types.get(d.cls) if d.cls else None
        if t is None or depth > 4:
            return False
        for _s, st in self._supers(t):
            if st is None or st is t or "extension-only" in st.modifiers:
                continue
            if self.spm.sees(caller, st.file) and (d.name in self.members.get(st.fqn, {})
                                                   or d.name in self.props.get(st.fqn, {})
                                                   or d.name in (self.b.nodes[st.id].attrs.get("property_requirements") or ())):
                return True
            if self._dispatch_visible(Decl(d.id, d.kind, d.name, d.fqn, d.file, d.line, d.end, st.fqn), caller, depth + 1):
                return True
        return False

    def _super_type(self, name: str) -> Decl | None:
        """The project type a supertype name means: the type of that (qualified, `Module.`-prefixed) name, else the
        one nested type of that short name."""
        q = name
        while q:
            if q in self.types:
                return self.types[q]
            q = q.split(".", 1)[1] if "." in q else None      # `Module.Type`: drop the module
        if getattr(self, "_nested", None) is None:
            self._nested = defaultdict(list)
            for d in self.types.values():
                if "." in d.fqn and "extension-only" not in d.modifiers:
                    self._nested[d.fqn.rsplit(".", 1)[1]].append(d)
        hit = self._nested.get(name.rsplit(".", 1)[-1]) or []
        return hit[0] if len(hit) == 1 else None

    def _supers(self, t: Decl) -> list[tuple[str, Decl | None]]:
        """(name, project type or None) per supertype of `t`; a qualified name written in the inheritance clause
        (`final class Spy: StatusEditor.AutocompleteService.Client, StatusEditor.PostingService.Client`) is resolved
        as written (#90)."""
        quals = self._qsup.get(t.fqn) or []
        short = {q.rsplit(".", 1)[1] for q in quals}
        names = list(quals) + [x for x in dict.fromkeys(t.supers) if x not in short]
        return [(x, self._super_type(x)) for x in names]

    def _sees(self, d: Decl, count: bool = False) -> bool:
        """May the call / read being resolved name `d`? Code in a SwiftPM target sees its own module and the targets
        it depends on, never an app, extension, preview or test target's declarations (#90). A type the project only
        extends is the SDK's, visible everywhere (its members are not)."""
        if not self.spm or self._at is None:
            return True
        if d.kind == "class" and "extension-only" in d.modifiers:
            return True
        caller = self._at[0].rel
        if self.spm.sees(caller, d.file) or self._dispatch_visible(d, caller):
            return True
        if count:
            self.st["candidates_outside_module"] += 1
        return False

    def _targets(self, name: str, recv: str | None, decl: Decl | None, labels: tuple = (), trailing: bool = False) -> list[Decl]:
        """Project declarations a call reaches: the full selector (name, argument labels, arity) has to fit;
        static members only through the type, instance members only through a value (#70)."""
        cls = self._encl_type(decl)
        if recv is None or recv in ("self", "Self", "super"):
            if cls is not None:
                own = [d for d in self._member(cls, name) if d.kind != "class"]
                if own:
                    self._recv = cls
                    fit = self._fitting(own, labels, trailing)
                    want = True if recv == "Self" else False if recv in ("self", "super") else \
                        bool(decl is not None and decl.kind != "class" and _static(decl))
                    return [d for d in fit if _static(d) == want] or (fit if recv is None else
                                                                      [d for d in fit if d.name == "init"])
            if name[:1].isupper():
                t = self.types.get(name)
                return [t] if t is not None else []
            cands = self._fitting([d for d in self.by_name.get(name, []) if d.kind == "function"], labels, trailing)
            if len(cands) > 1 and len({d.id.split("@")[0] for d in cands}) == 1:
                # one function defined per `#if os(...)` branch: the call reaches the base definition, and the
                # platform pass links its sibling variants (attrs.platform_variant_of)
                return [min(cands, key=lambda d: ("@" in d.id, d.line))]
            return cands if len(cands) == 1 else []
        kind, tname = self._recv_type(recv, decl, cls)
        if kind in ("type", "meta"):
            tc = self._type(tname)
            self._recv = tc
            ms = [d for d in self._member(tc, name) if d.kind != "class"] if tc is not None else []
            ms = [d for d in ms if _static(d) == (kind == "meta")] or \
                ([] if kind == "type" else [d for d in ms if d.name == "init"])
            return self._fitting(ms, labels, trailing)
        sel = self._selector(name, labels, trailing)
        sdk_sel = sel in SDK_SELECTORS or (name in STDLIB_METHODS and all(lab in STDLIB_LABELS for lab in labels))
        if kind == "sdk" and re.fullmatch(r"[A-Z]\w*", recv.strip()):
            # `Styleguide.registerFonts()`: a module-qualified free function (the module is a target folder,
            # `Sources/Styleguide/`)
            fs = self._fitting([d for d in self.by_name.get(name, []) if d.kind == "function"
                                and f"/{recv.strip()}/" in "/" + d.file], labels, trailing)
            if fs:
                return self._first_variants(fs)
        if kind == "sdk":
            # an SDK value: only members the project declares in an extension of an SDK type (of that type when
            # known, else of any: `Group { ... }.withEnvironments()` reaches `extension View`); never a selector
            # the SDK itself has (`.sorted(by:)`, `.accessibilityIdentifier(_:)`)
            if sdk_sel:
                self.st["calls_sdk_selector"] += 1
                return []
            tc = self.types.get(tname) if tname else None
            if tc is not None and "extension-only" in tc.modifiers:
                own = self._fitting([d for d in self._member(tc, name) if d.kind != "class"], labels, trailing)
                if own:
                    return own
            cands = [d for d in self.by_name.get(name, []) if d.kind == "method" and d.cls in self.types
                     and "extension-only" in self.types[d.cls].modifiers and "static" not in d.modifiers
                     and (not self._certain or d.cls not in CONCRETE_SDK or d.cls == tname)]
            cands = self._fitting(cands, labels, trailing)
            # short unlabelled selectors (`.run()`, `.get(_:)`) are too often the SDK type's own member
            return cands if len(cands) == 1 and (any(lab != "_" for lab in labels) or len(name) > 3) else []
        # unknown receiver: the selector decides
        if sdk_sel:                    # `xs.first(where:)`, `continuation.resume(returning:)`: the SDK's
            self.st["calls_sdk_selector"] += 1
            return []
        labelled = any(lab != "_" for lab in labels)
        cands = [d for d in self.by_name.get(name, []) if d.kind == "method" and "static" not in d.modifiers
                 and "class" not in d.modifiers]
        cands = self._first_variants(self._fitting(cands, labels, trailing))
        if not cands or not (labelled or len(name) > 3):
            return []
        if len(cands) > MAX_CANDIDATES:
            # `update()` on twenty app types: no useful candidate set
            self.st["calls_too_ambiguous"] += 1
            return []
        # bound by the selector alone (receiver type unknown): one fitting method is a `name` binding, several are
        # `candidate` edges to each (#83 item 6); both are left out of divergence, `cg tests` / impact flag candidates
        self._how = "name" if len(cands) == 1 else "candidate"
        return cands

    def _hierarchy(self):
        for d in list(self.decls.values()):
            if d.kind != "class":
                continue
            sups = self._supers(d)
            ext = [s for s, sc in sups if sc is None or "extension-only" in sc.modifiers]
            if ext and self.b.nodes[d.id].attrs.get("swift_kind") == "class":
                # an SDK superclass (`NSPopover`, `UIImageView`): members and initializers it gives are not in the graph
                self.b.nodes[d.id].attrs["external_supers"] = ext[:4]
            for s, sc in sups:
                if sc is None:
                    if s in LIFECYCLE_BASES or s == "App":
                        self._entry_class(d, "ui_page" if s == "UIViewController" else "main", f"{s} conformance")
                    continue
                kind = "IMPLEMENTS" if self.b.nodes[sc.id].attrs.get("swift_kind") == "protocol" else "EXTENDS"
                self.b.add_edge(d.id, sc.id, kind, d.file, d.line, HEURISTIC)
                for nm, ms in self.members.get(d.fqn, {}).items():
                    for base in self.members.get(sc.fqn, {}).get(nm, []):
                        for m in ms:
                            if m is not base:
                                ek = "IMPLEMENTED_BY" if kind == "IMPLEMENTS" else "OVERRIDDEN_BY"
                                self.b.add_edge(base.id, m.id, ek, m.file, m.line, HEURISTIC)

    def _entry_class(self, d: Decl, kind: str, why: str):
        n = self.b.nodes[d.id]
        if n.entry_kind == kind == "main":          # @main App: counted once
            self.st["entries_main"] -= 1
        n.entry_kind = n.entry_kind or kind
        n.attrs["entry_reason"] = why
        for nm, ms in self.members.get(d.fqn, {}).items():
            if LIFECYCLE.match(nm):
                for m in ms:
                    self.b.add_edge(d.id, m.id, "REFERENCES_FN", m.file, m.line, EXACT, how="framework lifecycle")
        self.st[f"entries_{kind}"] += 1

    # ------------------------------------------------------------------ Moya / Fluent (whole-file facts)
    @staticmethod
    def _block(txt: str, start: int) -> str:
        """The brace-balanced block whose `{` is at or after `start`."""
        i = txt.find("{", start)
        if i < 0:
            return ""
        depth = 0
        for j in range(i, len(txt)):
            if txt[j] == "{":
                depth += 1
            elif txt[j] == "}":
                depth -= 1
                if depth == 0:
                    return txt[i:j + 1]
        return txt[i:]

    def _prop_block(self, body: str, name: str) -> str | None:
        m = re.search(r"\bvar\s+" + name + r"\s*:\s*[\w.]+\s*\{", body)
        return self._block(body, m.start()) if m else None

    def _per_case(self, block: str | None, value_rx: str) -> tuple[dict, str | None]:
        """`switch self { case .a, .b(let x): return "..." }` -> ({case: value}, default value)."""
        if not block:
            return {}, None
        out, default = {}, None
        parts = re.split(r"\n\s*(case\s+[^\n:]*?:|default\s*:)", block)
        if len(parts) == 1:
            m = re.search(value_rx, block)
            return {}, (m.group(1) if m else None)
        for head, body in zip(parts[1::2], parts[2::2]):
            m = re.search(value_rx, body)
            if not m:
                continue
            if head.startswith("default"):
                default = m.group(1)
                continue
            for c in re.findall(r"\.(\w+)", head.split("case", 1)[1]):
                out.setdefault(c, m.group(1))
        return out, default

    def _moya_targets(self, sfiles):
        """Moya `TargetType` enums: baseURL + per-case path / method -> endpoint templates per case."""
        self.moya: dict[str, dict] = {}            # enum name -> {"base", "cases": {case: (METHOD, path)}, decl}
        texts = {sf.rel: sf.src.decode("utf-8", "replace") for sf in sfiles if b"TargetType" in sf.src}
        names = set()
        for txt in texts.values():
            names.update(re.findall(r"\b(?:enum|extension|struct)\s+(\w+)\s*:[^{]*\bTargetType\b", txt))
        for name in sorted(names):
            body = ""
            for txt in texts.values():
                for m in re.finditer(r"\b(?:enum|extension)\s+" + name + r"\b[^{]*\{", txt):
                    body += self._block(txt, m.start()) + "\n"
            d = self.types.get(name)
            if d is not None:
                sf = next((s for s in sfiles if s.rel == d.file), None)
                if sf is not None:
                    lines = sf.src.decode("utf-8", "replace").split("\n")[d.line - 1:d.end]
                    body += "\n".join(lines)
            cases = re.findall(r"^\s*case\s+(\w+(?:\s*\([^)]*\))?(?:\s*,\s*\w+(?:\s*\([^)]*\))?)*)\s*$", body, re.M)
            case_names = []
            for c in cases:
                case_names += [re.match(r"\w+", x.strip()).group(0) for x in re.split(r",(?![^(]*\))", c) if x.strip()]
            bb = self._prop_block(body, "baseURL") or ""
            mb = re.search(r'URL\s*\(\s*string\s*:\s*("(?:[^"\\]|\\.)*")', bb)
            base = template(mb.group(1)) if mb else "{baseURL}"
            paths, pdef = self._per_case(self._prop_block(body, "path"), r'("(?:[^"\\]|\\.)*")')
            meths, mdef = self._per_case(self._prop_block(body, "method"), r"\.(get|post|put|delete|patch|head|options)\b")
            out = {}
            for c in dict.fromkeys(case_names or list(paths)):
                p = paths.get(c) or pdef
                if p is None:
                    continue
                out[c] = ((meths.get(c) or mdef or "get").upper(), template(p))
            if out:
                self.moya[name] = {"base": base, "cases": out, "decl": d}
                self.st["moya_targets"] += 1
        # provider variables: `let provider = MoyaProvider<GitHub>()` / `var api: MoyaProvider<GitHub>`
        self.moya_vars: dict[str, str] = {}
        for txt in (sf.src.decode("utf-8", "replace") for sf in sfiles if b"Provider<" in sf.src):
            for m in re.finditer(r"\b(\w+)\s*(?::\s*\w*Provider\s*<\s*(\w+)\s*>|=\s*\w*Provider\s*<\s*(\w+)\s*>)", txt):
                self.moya_vars.setdefault(m.group(1), m.group(2) or m.group(3))

    def _moya_call(self, name, recv, al, owner, sf, line) -> bool:
        if not self.moya or name not in ("request", "requestPublisher", "requestWithProgress") or not al:
            return False
        arg = self.t(al[0][1]) if al[0][1] is not None else ""
        m = re.match(r"\s*(?:(\w+))?\.(\w+)", arg)
        if not m:
            return False
        case, tname = m.group(2), m.group(1)
        if tname is None:
            rv = re.sub(r"\.(rx|reactive)$", "", recv or "").split(".")[-1]
            tname = self.moya_vars.get(rv)
        targets = [tname] if tname in self.moya else [t for t, v in self.moya.items() if case in v["cases"]]
        if len(targets) != 1 or case not in self.moya[targets[0]]["cases"]:
            return False
        t = self.moya[targets[0]]
        meth, path = t["cases"][case]
        self.http.append({"src": owner, "method": meth, "url": join_path(t["base"], path), "client": "moya",
                          "file": sf.rel, "line": line, "target": f"{targets[0]}.{case}"})
        self.st["moya_calls"] += 1
        return True

    def _moya_endpoints(self):
        """Every Moya case is an endpoint of its TargetType, called or not (the enum -> http edge)."""
        for name, t in self.moya.items():
            d = t["decl"]
            if d is None:
                continue
            for case, (meth, path) in t["cases"].items():
                self.http.append({"src": d.id, "method": meth, "url": join_path(t["base"], path), "client": "moya",
                                  "file": d.file, "line": d.line, "target": f"{name}.{case}", "declared": True})

    def _fluent_models(self, sfiles):
        """Fluent `Model` classes (`static let schema = "todos"`) -> table nodes (MAPS_TO_TABLE)."""
        self.fluent: dict[str, str] = {}
        if not any(b"Fluent" in sf.src for sf in sfiles):
            return
        bysrc = {sf.rel: sf.src.decode("utf-8", "replace").split("\n") for sf in sfiles if b"schema" in sf.src}
        for d in list(self.types.values()):
            if "Model" not in d.supers or d.file not in bysrc:
                continue
            body = "\n".join(bysrc[d.file][d.line - 1:d.end])
            m = re.search(r'static\s+(?:let|var)\s+schema\s*(?::\s*String)?\s*(?:=\s*|\{\s*(?:return\s+)?)"([^"]+)"', body)
            if not m:
                continue
            self.fluent[d.name] = m.group(1)
            tid = self.b.add_node("table", m.group(1), lang="sql", attrs={"via": "fluent"})
            self.b.add_edge(d.id, tid, "MAPS_TO_TABLE", d.file, d.line, EXACT, via="Fluent schema")
            self.st["fluent_models"] += 1

    def _local_types(self, decl, sf) -> dict:
        """`let todo = Todo(...)`, `guard let todo = try await Todo.find(...)`, `let t: Todo = ...` in a function."""
        key = (decl.id, decl.line) if decl is not None else None
        if key is None:
            return {}
        cache = self.__dict__.setdefault("_lt_cache", {})
        if key not in cache:
            lines = sf.src.decode("utf-8", "replace").split("\n")[decl.line - 1:decl.end]
            out = {}
            for m in re.finditer(r"\b(?:let|var)\s+(\w+)\s*(?::\s*(\w+))?\s*=\s*(?:try\s*[?!]?\s+)?(?:await\s+)?([A-Z]\w*)\s*[.(]",
                                 "\n".join(lines)):
                out.setdefault(m.group(1), m.group(2) or m.group(3))
            cache[key] = out
        return cache[key]

    def _fluent_call(self, name, recv, owner, decl, sf, line, c):
        if not self.fluent or recv is None:
            return
        rv = re.sub(r"[?!]|\(.*\)$", "", recv).split(".")[-1].strip()
        if rv in self.fluent and name in ("query", "find"):                    # Todo.query(on:) / Todo.find(id, on:)
            whole = c
            while whole.parent is not None and whole.parent.type in ("navigation_expression", "call_expression",
                                                                     "call_suffix", "await_expression", "try_expression"):
                whole = whole.parent
            chain = self.t(whole)
            kind = "WRITES_TABLE" if re.search(r"\.(delete|update|set|create)\s*\(", chain) else "READS_TABLE"
            tid = f"table:{self.fluent[rv]}"
            self.b.add_edge(owner, tid, kind, sf.rel, line, RESOLVED, via=f"{rv}.{name}")
            self.st["fluent_queries"] += 1
            return
        if name in ("save", "create", "update", "delete", "forceDelete", "restore"):  # todo.save(on: req.db)
            ty = (decl.types.get(rv) if decl is not None else None) or self._local_types(decl, sf).get(rv)
            if ty in self.fluent:
                self.b.add_edge(owner, f"table:{self.fluent[ty]}", "WRITES_TABLE", sf.rel, line, RESOLVED,
                                via=f"{ty}.{name}")
                self.st["fluent_writes"] += 1
            return

    def _fluent_migrations(self, sfiles):
        """`database.schema("todos")....create()` / `.delete()` / `.update()` in a Migration -> WRITES_TABLE
        (via migration) from the prepare / revert method."""
        for sf in sfiles:
            if b".schema(" not in sf.src:
                continue
            for d in self._fd.get(sf.rel, ()):
                if d.kind != "method" or d.name not in ("prepare", "revert"):
                    continue
                body = "\n".join(sf.src.decode("utf-8", "replace").split("\n")[d.line - 1:d.end])
                for m in re.finditer(r'\.schema\s*\(\s*"([^"]+)"\s*\)', body):
                    tail = body[m.end():m.end() + 600]
                    op = re.search(r"\.(create|update|delete)\s*\(\s*\)", tail)
                    tid = self.b.add_node("table", m.group(1), lang="sql", attrs={"via": "fluent"})
                    self.b.add_edge(d.id, tid, "WRITES_TABLE", d.file, d.line, EXACT,
                                    via=f"migration {op.group(1) if op else 'schema'}")
                    self.st["fluent_migrations"] += 1

    def _emit_http(self):
        bases = getattr(self, "bases", {}) or {}
        base_origins = {split_url(b["value"])[0] for bs in bases.values() for b in bs}
        for r in self.http:
            origin, path = split_url(r["url"])
            okind = "api" if origin is None else ("unknown" if origin.startswith("{") else "other")
            extra = {}
            if okind == "unknown" and bases.get(origin[1:-1]):
                bs = bases[origin[1:-1]]
                if len(bs) == 1:            # one configured value: the backend's origin and path prefix
                    bo, bp = split_url(bs[0]["value"])
                    origin, path, okind = bo, (bp.rstrip("/") + path if bp not in ("", "/") else path), "api"
                    extra = {"base": {"value": bs[0]["value"], "source": bs[0]["source"]}}
                    self.st["http_base_resolved"] += 1
                else:                       # one per configuration / environment
                    okind = "env"
                    extra = {"base_candidates": bs[:6]}
                    self.st["http_base_per_environment"] += 1
            elif okind == "other" and origin in base_origins:
                okind = "api"               # an absolute URL on a configured base's origin
                self.st["http_base_origin_matched"] += 1
            key = f"{r['method']} {path}" if okind in ("api", "unknown", "env") else f"{r['method']} {origin}{path}"
            nid = self.b.add_node("http", key, key, fqn=key, lang="swift",
                                  attrs={"method": r["method"], "path": path, "client": r["client"], "origin": origin,
                                         "origin_kind": okind, **extra})
            self.b.add_edge(r["src"], nid, "HTTP_CALLS", r["file"], r["line"], HEURISTIC if okind != "api" else EXACT,
                            client=r["client"], url=r["url"], origin=origin,
                            **({"target": r["target"]} if "target" in r else {}),
                            **({"how": "moya target"} if r.get("declared") else {}))
            self.st[f"http_{r['client']}"] += 1

    def _page(self, view: Decl, how: str) -> str:
        key = f"swift:{view.name}"
        pid = self.b.add_node("page", key, name=view.name, fqn=key, file=view.file, line=view.line, lang="swift",
                              entry_kind="ui_page", attrs={"route": view.name, "via": how, "view": view.fqn})
        n = self.b.nodes[pid]
        n.entry_kind = n.entry_kind or "ui_page"
        m = self.members.get(view.fqn, {})
        body = m.get("body") or m.get("viewDidLoad") or m.get("loadView")
        self.b.add_edge(pid, body[0].id if body else view.id, "ROUTES_TO", view.file, view.line, EXACT)
        return pid

    def _is_controller(self, v: Decl, depth: int = 0) -> bool:
        if any(s.endswith("ViewController") for s in v.supers):
            return True
        return depth < 4 and any(self._is_controller(self.types[s], depth + 1) for s in v.supers if s in self.types)

    def _link_navs(self):
        # NavigationLink(value: T.x(...)) / navigate(to: .x): the views navigationDestination(for: T.self) shows for
        # case x (an untyped `.x` only when exactly one destination type has that case)
        for owner, tname, case, how, file, line in self.value_navs:
            if tname is None:
                def declares(t):        # the enum declares the case (shown by a `default:` branch)
                    ty = self.types.get(t)
                    return ty is not None and case in self.values.get(ty.fqn, {})
                hits = [t for t, m in self.dest_map.items() if case in m] or \
                    [t for t, m in self.dest_map.items() if None in m and declares(t)]
                tname = hits[0] if len(hits) == 1 else None
            m = self.dest_map.get(tname) if tname else None
            views = (m.get(case) or m.get(None) or []) if m else []
            for v, _f, _l in views:
                self.navs.append((owner, v, how, file, line))
            if views:
                self.st["navigations_by_value"] += 1
        pages = {}
        for nav in self.navs:
            owner, tname, how, file, line = nav[:5]
            page_only = nav[5] if len(nav) > 5 else False
            v = self.types.get(tname)
            if v is None or not ("body" in self.members.get(v.fqn, {}) or self._is_controller(v)):
                continue
            if v.fqn not in pages:
                pages[v.fqn] = self._page(v, how)
                self.st["swiftui_pages"] += 1
            if not page_only:
                self.b.add_edge(owner, pages[v.fqn], "NAVIGATES_TO", file, line, EXACT, how=how)
                self.st["navigations"] += 1
