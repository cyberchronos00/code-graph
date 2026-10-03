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

## Buckets

- **missing:** no counterpart found. These are grouped by folder in the text output. A Gradle `src/main/kotlin/`
  path is grouped by package.
- **unknown:** the symbol is in a file that parsed with syntax errors ([completeness.md](completeness.md)), so it
  stays out of "missing".
- **platform_only:** the symbol is tagged only for platforms the target graph does not build
  ([platforms.md](platforms.md)).

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

`--json` returns `matched`, `missing`, `unknown` and `platform_only` lists (symbol, kind, file:line, folder group,
target and confidence for a match, and `owner_matched` for a member of a matched type), plus `summary`.
