# Port gaps: `cg parity`

`cg parity` compares two graphs of the same app on two platforms (an iOS app in Swift / SwiftUI
and its Android port in Kotlin / Compose). It lists types, functions, enum cases and constants
of the source graph that have no counterpart in the target.

```text
$ cg index immuni-app-ios --db out/ios.db && cg index immuni-app-android --db out/android.db
$ cg parity --db out/ios.db --against out/android.db
parity: out/ios.db -> out/android.db
1378 source symbols: 193 matched (exact 157, normalized 36), 1180 missing, 5 unknown
```

Run both directions. `--db A --against B` lists what B lacks.

## What is compared

- Types: class, struct, enum, protocol, interface, object, actor.
- Members of matched types: methods, properties, enum cases, constants, Kotlin nested sealed
  objects (`data object DeleteClick`).
- Top-level functions and constants.

Left out, and counted in `--json` `summary`: test and mock folders (`TestHelpers/`, `Mock*` /
`Fake*` / `Stub*`); Swift `Has*` service-locator protocols; extension and companion-object
nodes (members stay on the type); overrides (`viewDidLoad`, `onCreate`); boilerplate (`body`, `init`, `hash`, `description`).

## How a counterpart is found

First match wins. `--json` records `confidence`.

| confidence | rule |
|---|---|
| `explicit` | a comment above either declaration (`/// Port of: FooView`, `// Android: FooScreen`), or `--map renames.json` (`{"source": "target"}`) |
| `exact` | same name, same category. Members need the same name inside the matched type |
| `normalized` | case and `_` folded; `Default` / `Impl` / `Json` / `RequestModel` and `--strip-prefix` words dropped; `VM` and `Processor` read as ViewModel; UI suffixes stripped only when both names have one; word order may differ; a SwiftUI type with `body` matches a `@Composable` of the same stem; member `get` prefixes and event verbs (`deletePressed`, `DeleteClick`) dropped |
| `fuzzy` | one word shortened or pluralised (`ItemList` / `ItemListing`). One extra word is not a match. `--no-fuzzy` turns this off |
| `moved` | a member of three or more words, missing on the counterpart type, present on exactly one other target type |

A nested type is looked for inside its owner's counterpart. Anywhere else it matches by exact
name only, so a generic `Keys` does not meet an unrelated type.

## Structure (`--structure`)

Off by default. With it, symbols still missing after the name rules are paired by what they use
(`codegraph/parity_structure.py`).

| feature | from |
|---|---|
| `l10n:<key>` | `Localizations`, `L10n`, `R.string`, `CommonStrings` (case and `_` folded) |
| `str:<text>` | string literals of 5–80 characters |
| `http:<path>` | HTTP paths, parameters folded |
| `sym:<target>` | calls already paired, renamed to the target's name |
| `call:<name>` | member names it calls (two words or more) |
| `w:<stem>` | stemmed words of its own name |

Two symbols pair when they share a use and either two uses or two name words, the cosine of
their features is at least 0.3, each is the other's best candidate, and the runner-up is below
0.8 of the best. Category and UI-ness must agree. A `Coordinator` does not meet a `State`;
`Processor` meets `ViewModel`. Compose previews are left out.

A word rewrite seen at least twice, and in two thirds of the pairs with that tail (
`Coordinator` → `Navigator`), is applied to the rest (`learned`). `--no-learn` turns that
off. Inferred matches are an `== INFERRED` section (`summary.inferred`), never name matches.
`--write-map FILE` writes a `--map` to review; fed back, those rows become `explicit`.

## Buckets

| bucket | meaning |
|---|---|
| missing | no counterpart. Grouped by folder (a Gradle `src/main/kotlin/` path by package) |
| unknown | the file parsed with syntax errors ([completeness.md](completeness.md)), so it is not "missing" |
| platform_only | tagged only for platforms the target graph does not build ([platforms.md](platforms.md)), other than the source app's own platform. A tag on at least half the source symbols is the app itself, so that tag is compared like untagged code. A `watchos`-only widget stays platform-only |

## How far to trust "missing"

A counterpart with a different name and a different shape is reported missing. Hand-checked
samples of 20 "missing" rows on Bitwarden (bitwarden/ios, bitwarden/android):

| direction | symbols | missing | false "missing" in the sample |
|---|---|---|---|
| iOS → Android | 3735 | 3048 | 6 / 20 |
| Android → iOS | 8177 | 7661 | 2 / 20 |

The false rows fall into three groups:

- Behaviour under another name:
  `CompleteRegistrationProcessor.checkPasswordAndCompleteRegistration` ↔
  `CompleteRegistrationViewModel.handleCallToAction`.
- Moved and renamed: `AuthService.getPendingLoginRequest` ↔
  `AuthRequestManager.getAuthRequestById`.
- A SwiftUI subview written as a Compose function: `GeneratorView.generatedValueView`.

The real gaps in those samples were platform-only code (watchOS, CryptoKit, Compose saved-state
keys) and shapes the other side models differently (a result type returned as `Bool`, a
per-screen dialog state). Read the list as "no counterpart with a related name". Use `--map` and
`--strip-prefix` for systematic renames.

With `--structure` (October 2026): Bitwarden inferred 101 and 124 matches; a sample of 20 was
mostly right where the apps share a localization key, and a separate sample of 20 still-missing
symbols had no match either way. Element X Android → iOS inferred 91 (about 16/20 right).
Element X iOS → Android inferred 83; before the platform-tag fix, 3,551 of 4,184 iOS symbols
were `platform_only` because they are tagged `ios`.

`--json` returns `matched`, `missing`, `unknown`, `platform_only`, `learned_rules` and
`summary`.
