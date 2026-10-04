# Port gaps: `cg parity`

`cg parity` compares two graphs of the same app on two platforms, for example an iOS app in Swift / SwiftUI and its
Android port in Kotlin / Compose. It lists the types, functions, enum cases and constants of the source graph that
have no counterpart in the target ([#85](https://github.com/cyberchronos00/code-graph/issues/85)).

```text
$ cg index immuni-app-ios --db out/ios.db && cg index immuni-app-android --db out/android.db
$ cg parity --db out/ios.db --against out/android.db
parity: out/ios.db -> out/android.db
1378 source symbols: 193 matched (exact 157, normalized 36), 1180 missing, 5 unknown (syntax errors), 0 platform-only

== MISSING in target: 1180
  App/UI  (481)
    AppSetupVC  [class] App/UI/AppSetup/AppSetupVC.swift:19
    ...
```

Run it both ways: `--db A --against B` lists what B lacks, and `--db B --against A` lists what A lacks.

## What is compared

- **Types:** class, struct, enum, protocol, interface, object and actor.
- **Members of matched types:** methods, properties, enum cases, constants, and Kotlin nested sealed-class objects
  (`data object DeleteClick : OrderAction()`).
- **Top-level code:** top-level functions and constants.

Some symbols are left out and counted in `--json` `summary`:

- test code and test support (`TestHelpers/`, `Mocks/`, `Fixtures/` folders and `Mock*` / `Fake*` / `Stub*` names);
- Swift `Has*` service-locator protocols;
- extension and Kotlin companion object nodes (their members stay with the type);
- overrides (`viewDidLoad`, `onCreate`, ...);
- boilerplate members (`body`, `init`, `hash`, `description`).

## How a counterpart is found

Each source symbol gets the first match in this order. Every match in `--json` carries its `confidence`.

| confidence | rule |
|---|---|
| `explicit` | A comment right above either declaration names the counterpart: `/// Port of: FooView`, `// iOS: Foo.bar`, `// Android: FooScreen`. Renames can also come from `--map renames.json` (`{"source name": "target name"}`). |
| `exact` | The same name in the same category (type, function or constant). Members need the same name inside the matched type. |
| `normalized` | See the list below. |
| `fuzzy` | The same words with one of them shortened or pluralised (`ItemList` / `ItemListing`, `PendingLogins` / `PendingLogin`). One word more or less (`LoginTOTPState` / `LoginState`) is not a match. `--no-fuzzy` turns this off. |
| `moved` | A member missing on the counterpart type whose name has three words or more and exists on exactly one other target type (`VaultRepository.updateCipherCollections` → `CipherManager.updateCipherCollections`). |

A `normalized` match is a match after these rewrites:

- Case and `_` are folded.
- `Default…`, `…Impl`, `…Json` and `…RequestModel` are dropped, along with any `--strip-prefix` words (an app prefix one side adds, e.g. `--strip-prefix Vault` for `VaultAddEditState` / `AddEditState`).
- `VM` and `Processor` are read as ViewModel.
- UI suffixes (View, Screen, Sheet, Page, Fragment, Activity, ViewController, VC, Controller) are stripped only when both names have one. A model type `Otp` does not meet `OtpFragment`.
- The same words can come in another order (`AddEditFolderView` / `FolderAddEditScreen`).
- A SwiftUI type with a `body` also matches a `@Composable` function of the same stem.
- For members, `get` prefixes and UI event verbs (`deletePressed`, `deleteTapped`, `DeleteClick`) are dropped.

A nested type (`State.FormField`) is looked for inside its owner's counterpart. Anywhere else it matches by exact
name only, so a generic `Keys` or `FieldType` does not meet an unrelated type.

## Matching by structure (`--structure`)

Off by default: without it the output is the name matching above, unchanged. With `--structure`, the symbols still
missing after the name rules are paired by what they use ([#93](https://github.com/cyberchronos00/code-graph/issues/93),
`codegraph/parity_structure.py`). Each symbol's declaration span and outgoing edges give it a feature set:

| feature | from |
|---|---|
| `l10n:<key>` | localization keys: `Localizations.creatingAccount`, `L10n.actionCancel`, `R.string.creating_account`, `BitwardenString.x`, `CommonStrings.x` (case and `_` folded, so the two platforms' keys meet) |
| `str:<text>` | string literals of 5 to 80 characters: URL paths, header names, analytics events, accessibility ids |
| `http:<path>` | HTTP endpoints it calls, path parameters folded |
| `sym:<target>` | symbols it calls, instantiates or navigates to that are already paired (renamed to the target's name) |
| `call:<name>` | the names of the members it calls (two words or more): `getDevices` on an `AuthService` and on an `AuthRepository` |
| `w:<stem>` | the stemmed content words of its own name (role, UI and event words left out) |

Two symbols pair when they share at least one use and either two uses or two name words, the IDF-weighted cosine of
their features is at least 0.3, each is the other's best candidate and the runner-up scores below 0.8 of the best.
The category must agree (a SwiftUI view type also meets a `@Composable` function), types and top-level symbols must
agree on being UI code, and architecture roles must not clash (a `…Coordinator` does not meet a `…State`; `Processor`
meets `ViewModel`, `Request` meets `Api`). Compose previews and preview providers are left out. Members of a type
paired this way are then compared like those of a name match.

Rename rules are learned from the non-exact type pairs: a word tail (`Coordinator` → `Navigator`) or head rewrite seen
at least twice, and in two thirds of the pairs with that source tail, is applied to the remaining missing types and
top-level symbols (confidence `learned`). The rules are listed in the text output and in `--json` `learned_rules`;
`--no-learn` turns them off.

Inferred matches have confidence `structure` (with `score` and `evidence`, the shared features with the most weight)
or `learned` (with the rule), are listed in their own `== INFERRED` section and counted in `summary.inferred`, so they
never read as name matches. `--write-map FILE` writes them as a `--map` file to review, edit and commit; fed back with
`--map` they become `explicit` matches.

## Buckets

- **missing:** no counterpart found. These are grouped by folder in the text output. A Gradle `src/main/kotlin/`
  path is grouped by package.
- **unknown:** the symbol is in a file that parsed with syntax errors ([completeness.md](completeness.md)), so it
  stays out of "missing".
- **platform_only:** the symbol is tagged only for platforms the target graph does not build
  ([platforms.md](platforms.md)), other than the source app's own platform. A platform that tags at least half of the
  compared source symbols is the app itself (Element X iOS tags nearly every symbol `ios` from its Xcode targets), so
  code tagged with it is compared like untagged code; a `watchos`-only widget is still platform-only (#107).

## How far to trust "missing"

Matching is by name, so a counterpart with a different name and a different shape is reported missing. This was
measured on Bitwarden (bitwarden/ios and bitwarden/android). The two apps share a product but differ in architecture:

- iOS has a coordinator, processor, state, action and effect per screen;
- Android has a navigation file, view model, state, action and event per screen.

Hand-checked random samples of 20 "missing" results each way:

| direction | symbols | missing | false "missing" in the sample |
|---|---|---|---|
| iOS → Android | 3735 | 3048 | 6 / 20 (30%) |
| Android → iOS | 8177 | 7661 | 2 / 20 (10%) |

The false ones fall into three groups:

- **Behaviour under a different name:** `CompleteRegistrationProcessor.checkPasswordAndCompleteRegistration` ↔
  `CompleteRegistrationViewModel.handleCallToAction`, and `LoginWithDeviceProcessor.sendLoginWithDeviceRequest` ↔
  `createAuthRequest`.
- **Moved and renamed:** `AuthService.getPendingLoginRequest` ↔ `AuthRequestManager.getAuthRequestById`.
- **SwiftUI subviews as Compose functions:** `GeneratorView.generatedValueView`, `AttachmentsView.noAttachmentsView`.

The real gaps in the samples were platform-only code and code the other side models differently:

- platform-only: watchOS, CryptoKit and `Bundle` helpers, Android navigation helpers, Compose saved-state keys,
  Digital Asset Links;
- modelled differently: result types the other side returns as `Bool`, per-screen dialog states, or features one app
  doesn't have.

Read the list as "no counterpart with a related name". Use `--map` and `--strip-prefix` for systematic renames.

### With `--structure` (October 2026)

| pair | inferred matches | right in a hand-checked sample of 20 | sampled "missing" now matched |
|---|---|---|---|
| Bitwarden iOS → Android | 101 (of 3,048 missing) | 13 right, 2 wrong (15 of the 20 sampled are still inferred after tightening) | 0 / 20 |
| Bitwarden Android → iOS | 124 (of 7,661) | 7 right, 4 wrong (11 of 20 still inferred) | 0 / 20 (1 wrong match) |
| Element X Android → iOS | 91 (of 7,910) | about 16 / 20 | 0 / 20 |
| Element X iOS → Android | 83 (78 structure, 5 learned; after #107) | not sampled yet | — |

The inferred matches are mostly right where the two apps share localization keys or accessibility ids (SwiftUI
subviews ↔ composables, display-label helpers, request ↔ API types). The wrong ones share generic keys
(`l10n:yes`, `l10n:phone`) or one string. They are a small part of "missing": a random sample of 20 missing symbols
per direction had no match before or after, so the false "missing" rate is unchanged. In those samples the false
ones are counterparts with different names that use nothing the other side uses in a way the graph sees
(`ViewItemState` ↔ `VaultItemState`, `IdentityTokenResponseModel` ↔ `GetTokenResponseJson`,
`LoginFormState` ↔ `LoginScreenViewStateBindings`). Element X iOS → Android had put 3,551 of 4,184 source symbols in
`platform_only`, because the iOS symbols are tagged `ios` and the Android graph does not build `ios` (fixed in #107:
0 platform-only, 3,760 missing).

`--json` returns `matched`, `missing`, `unknown` and `platform_only` lists (symbol, kind, file:line, folder group,
target and confidence for a match, and `owner_matched` for a member of a matched type), plus `summary`.
