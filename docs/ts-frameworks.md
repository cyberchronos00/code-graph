# TypeScript / JavaScript frameworks

What NestJS, Next.js, Astro, Express-style routers and React Router / Remix add on top of the TypeScript plugin. They share
one program with the Nuxt layer, so aliases and types go through the checker. Plain JavaScript
(CommonJS or ESM, with or without `jsconfig.json`) is indexed with `allowJs`. Samples:
`examples/bookstore-nest`, `bookstore-next`, `bookstore-express`. Without a root tsconfig, the
program is the per-package `tsconfig.json` next to a `package.json` one or two levels down, plus
a `tsconfig.json` in `src/`, `app/`, `lib/` or `web/` without one. The extractor runs under Node.js 20+ or Bun ([Installing and updating cg](install.md#javascript-runtime)).

Several layers can run on one project. The shared post-pass (env, ORM tables, in-repo links)
runs once, after the last of them. Extractor facts are described values; the Python plugins
resolve them.

## NestJS

Detect: `@nestjs/core` or `@nestjs/common`, or `nest-cli.json`.

| feature | graph |
|---|---|
| modules | `@Module` → BINDS / REFERENCES, `attrs.nest_module`. `RouterModule.register` prefixes |
| routes | `@Controller(prefix \| {path})` + `@Get` / `@Post` / `@Put` / `@Patch` / `@Delete` / `@All` / `@Head` / `@Options` → `route:<METHOD> <uri>` (entry `http_route`) with ROUTES_TO the handler. `setGlobalPrefix(p, {exclude})`. `enableVersioning` URI (`/v1/...`, `defaultVersion`) and `@Version`. Header / media-type versioning stays in `attrs.version`, not the path |
| DTOs | `@Body() dto: CreateXDto` / `@Query() q: XQueryDto` → `attrs.body_dto` / `body_fields`, `query_dto` / `query_fields` (`?` marks optional) |
| enhancers | `@UseGuards` / interceptors / pipes / filters, `APP_GUARD`, `useGlobalGuards` → `attrs.guards` and USES_MIDDLEWARE |
| DI | constructor and `@Inject()`. Providers: class, `useClass`, `useExisting`, `useFactory` (the class it constructs), `useValue`, `ClientsModule.register`. INJECTS, CALLS `via: nest-di`, BOUND_TO from an abstract token to the provider. Library tokens (`ConfigService`, `JwtService`) are external |
| GraphQL | `@Resolver` + `@Query` / `@Mutation` / `@Subscription` / `@ResolveField` → `route:GRAPHQL Query.<name>` |
| other entries | `@Cron` / `@Interval` / `@Timeout` → `schedule:`; BullMQ `@Processor` + `WorkerHost.process`, Bull `@Process(name)` → `job:<queue>`; `@OnEvent` (string, array, or `{name}`, including a project decorator of that name) → `listener:` with `event:<name>`; `@MessagePattern` / `@EventPattern`, `@SubscribeMessage` (gateway namespace), `@GrpcMethod` → `message:<transport>:<pattern>`; nest-commander `@Command({name})` → `command:nest:<name>` (operator-only) |
| producers | `queue.add` on `@InjectQueue`, `EventEmitter2.emit`, `ClientProxy.send` / `emit` → DISPATCHES. An outgoing `message:` node when the handler lives in another service |
| data | TypeORM / MikroORM / sequelize / Mongoose → MAPS_TO_TABLE. Prisma, Drizzle, Kysely, knex → READS_TABLE / WRITES_TABLE |
| config | `process.env.X`, `ConfigService.get`, `registerAs` → `config:ns.key` |

Tokens: class types, `@Inject('TOKEN')` / `@Inject(SYMBOL)`, `@Dependencies(A, B)` in JS.
Providers are found in module arrays, spread arrays, custom-provider files and dynamic
`forRoot()`. Library providers (`ConfigService`, `JwtService`, `@InjectX()`, tokens
imported from packages) count as external. The GraphQL route's protocol twin is
[Protocol links](protocols.md).

## Next.js

Detect: `next` or `next.config.*`.

| feature | graph |
|---|---|
| app router | `page.tsx` → `page:` (`attrs.uri`). Layouts / loading / error are `ui_global` with USES_LAYOUT. `route.ts` exports `GET` / `POST`, including `withX(handler)` and re-exports |
| segments | `[id]` → `{id}`, `[...slug]` → `{slug*}`, `[[...slug]]` → `{slug*?}`, route groups `(x)` and parallel slots `@x` dropped, intercepting `(.)x` / `(..)x` resolved to the intercepted path (best effort), `_private` skipped |
| pages router | `pages/**` minus `_app`, `_document`, `_error`. `pages/api/**`: method from `req.method === 'X'` or `switch (req.method)` (also `const { method } = req`), else `ANY`. `getServerSideProps` / `getStaticProps` / `generateMetadata` are reached from the page |
| server actions | `'use server'` → `route:ACTION <file>#<fn>`. Client components CALLS them |
| middleware | `middleware.ts` / `proxy.ts` `config.matcher` → USES_MIDDLEWARE (`resolved`). A regex or no matcher links every route (`heuristic`) |
| clients | `fetch`, axios (`post` / `put` / `patch` take the body from the second argument; `get` / `delete` / `head` / `options` and `request({ method, url, data, params })` take `data` as the body and `params` as the query, module or instance), `ky` (+ `ky.create({prefixUrl})`), `ofetch` / `$fetch` (+ `.create`), `useSWR(key)`, OpenAPI `this.request({path, method})` and `__request(OpenAPI, {method, url})`. Same-repo calls are MATCHES_ROUTE (`attrs.in_repo`) |
| links | `<Link href>` from `next/link` → NAVIGATES_TO (`via: link`), beside `router.push` / `replace` |
| config | static `basePath`; literal `rewrites` as `uri_variants`. `NEXT_PUBLIC_*` (and `VITE_`, `NUXT_PUBLIC_`, `REACT_APP_`, `EXPO_PUBLIC_`, `PUBLIC_`) is `attrs.public` |

## React Router and Remix

Detect: `react-router`, `react-router-dom`, `@react-router/*` or `@remix-run/*` in `package.json`, or
`react-router.config.*`, or `app/routes.ts`. Framework mode when `@react-router/dev` or `@remix-run/dev`
is present. A literal `appDirectory` in `react-router.config.*` is the app directory (default `app`).
Sample: `examples/bookstore-react-router`.

| feature | graph |
|---|---|
| code routers | `createBrowserRouter` / `createHashRouter` / `createMemoryRouter` / `useRoutes`, and `<Routes>` / `<Route>` (also `createRoutesFromElements`). Nested `children`, `index`, `path` joined with the parent. `page:react-router:<path>` (`attrs.route` in colon form, `uri`, `framework: react-router`, `router: code`, entry `ui_page`) RENDERS `element` / `Component`. `lazy: () => import(...)` resolves the module |
| file routes | `app/routes.ts` helpers `route` / `index` / `layout` / `prefix`, and `flatRoutes()` file conventions (`books.$id`, `_index`, `$` splat, `_` pathless layouts, `($param)` optional segments, `[.]` escapes, `route.tsx` folders). `page:<file>` like Next.js, `attrs.router: framework`, page line 1 |
| layouts | `root.tsx` and layout routes → `layout:` (`ui_global`). Pages USES_LAYOUT. Framework id `layout:<file>`. Code layout id `layout:react-router:<path>` |
| loaders | Framework mode: `loader` → `route:GET <uri>`, `action` → `route:POST <uri>`, ROUTES_TO the function (entry `http_route`). The same action also gets `route:PUT` / `route:PATCH` / `route:DELETE` when a form or fetcher uses that method. The page CALLS them (`via: loader` / `action`). `clientLoader` / `clientAction` stay CALLS and are not routes. A code-router loader / action is a client CALLS only |
| navigation | `<Link to>`, `<NavLink to>`, `<Navigate to>`, `navigate()` from `useNavigate()` (including an import or local alias), and `redirect()` → NAVIGATES_TO (`via` `link` / `navigate` / `redirect`). A relative `to` resolves against the route module that owns the call, including `..`. `navigate(-1)` is a history delta and is ignored. A template literal with a param becomes `{param}`. A miss stays in `nav_unresolved` (coverage). Next.js `<Link href>` uses the same JSX handling |
| forms | `<Form>`, `fetcher.Form`, `fetcher.load` / `fetcher.submit`, `useSubmit()` → `http:<METHOD> <uri>` + HTTP_CALLS. The default method is GET. An omitted action is the current route URL. A relative action resolves the same way as `to`. `<Form method="delete">` (and put / patch) matches the action route. Framework mode matches these to the loader / action route (MATCHES_ROUTE, `attrs.in_repo`). A Next.js app that only depends on `react-router` and has no routes is not reported as React Router |

## Express, Koa, Fastify, Hono, Elysia

Detect: `express`, `koa`, `@koa/router`, `fastify`, `hono`, `elysia`. Router values are followed
across `import` / `require`, `module.exports` and factories. A parameter typed
`FastifyInstance` / `Router` / `Hono`, or an untyped parameter that receives a literal path,
counts. A plugin RPC registration `router.post("github.webhooks", handler)` (a dotted literal with no
leading slash, not an HTTP path) is `POST /github.webhooks` at `heuristic` with `attrs.plugin_rpc`,
including when `router` is an untyped parameter. `router.post("/orders")` and `router.post("/acme.events")`
stay HTTP routes and do not gain `plugin_rpc`. `elysia` is its own framework label and uses this router layer.

| feature | graph |
|---|---|
| routes | `.get` / `.post` / …, `.route(path).get`, `fastify.route`, `hono.on`. `:id` → `{id}`, `:p+` / `*name` → `{p*}`, `*` → `{p*?}` |
| mounting | `.use`, `lazyUse`, koa `.routes()`, `fastify.register({prefix})`, `hono.route`, `basePath`, `routes(app)` |
| handlers | functions, `controller.method`, `exports.x`, wrappers (`asyncHandler`), lazy `require()` registries |
| middleware | route middleware, `preHandler` / `onRequest`, fastify `addHook`, `.use` registered before the route in the same file, middleware passed with a mount → `attrs.middleware` + USES_MIDDLEWARE |
| schemas | Fastify `schema.body.properties` / `querystring.properties` → `attrs.body_fields` / `query_fields` |
| confidence | `exact` literal path and direct handler; `resolved` through a wrapper or alias; `heuristic` when the router is never mounted (`attrs.unmounted`) or the path is not a literal |

`cg link --backend api.db --frontend web.db` matches any TS frontend to a Nest, Next or Express
backend. A traced `baseURL` is exact. `process.env` and generated clients align to the end of
the route (`heuristic`); the most specific alignment wins. Method and path only: routes carry
`body_fields`, calls carry `body_keys`, and nothing compares them yet.

### Elysia

Detect: an `elysia` dependency. `cg coverage` reports `frameworks: elysia` (preset `express`).
Sample: `examples/bookstore-payments`.

| feature | graph |
|---|---|
| instances | `new Elysia({ prefix, name })`, an exported const, or a function that returns an instance |
| chains | `.model`, `.decorate`, `.state`, `.derive`, `.resolve`, `.macro`, `.onError`, `.listen`, `.use(openapi())` stay on the same instance |
| mounting | `.use(sub)` across files, including nested `new Elysia({ prefix }).use(new Elysia({ prefix }))`, a factory `function f(prefix) { return new Elysia({ prefix }) }`, the same sub-app mounted twice, and `.group('/x', app => app.get(...))`. A circular `.use()` stops. `.use(import('./x'))` is `heuristic`. A plugin with no routes is not a router |
| hooks | `.onRequest`, `.onBeforeHandle`, `.guard({ beforeHandle })`, route `{ beforeHandle }` → `attrs.middleware`. `local` affects the instance and descendants mounted after the hook. `scoped` also affects the direct parent. `global` affects ancestors and plugins mounted on them afterwards. `onRequest` is copied onto every ancestor, so it affects every route in that app, including routes registered earlier. Other hooks affect routes registered after them. A hook that sets status 401 or 403, returns `error(401)` / `status(401)`, or throws an auth error is `[auth]` with those status codes, whatever its name. A hook that only sets headers is not auth. The label is the hook function, or the plugin `name` |
| schemas | `body` / `query` / `params: t.Object({...})` → `attrs.request.keys`, `body_fields`, `query_fields`. `{ body: 'name' }` and `t.Ref('name')` resolve `.model({...})` on that instance or a plugin it `.use()`s, including `.model(importedMap)`. A model-map value that is an identifier (`{ CreateRefundBody: refundBodySchema }`) is followed to its `const` initializer in the same module or through one import or re-export, for `body`, `query`, `params` and `t.Ref`. One hop only: a const that points to another const stays unknown. A name that does not resolve is an unknown schema, not an empty one |
| paths | `:id` → `{id}`, `:id?` → `{id?}`, `*` → `{wildcard*?}`. Extra slashes in a prefix or a path are collapsed |

```ts
export const payments = new Elysia({ prefix: '/payments' })
  .use(signedRequests)
  .post('/', ({ body }) => createPayment(body), { body: t.Object({ order_id: t.Number(), amount: t.Number() }) })
```

Eden Treaty clients and `.ws()` message names are not read. Hook order across files is not known, so a hook and a route in different files both count. A hook that skips some paths itself (an early return on `request.url`) is still listed on every route it affects.

## Astro

An Astro project is detected from an `astro` dependency or an `astro.config.mjs` / `.js` / `.ts` /
`.mts` / `.cjs` file. Frontmatter between the `---` fences and `<script>` blocks are indexed as
TypeScript at their real lines in the `.astro` file, and the template is read too. `.astro` files
are parsed as TSX, so a `<T>x` cast or a `<T>() =>` arrow in a `.astro` file is a syntax error.
Literal `srcDir`, `base`, `trailingSlash`, `redirects` and `i18n` are read from `astro.config.*`,
and routes carry `base`.

| feature | graph |
|---|---|
| pages | `src/pages/**.astro` (or `<srcDir>/pages`) → page (`ui_page`). `index` is `/`, `[slug]` is `{slug}`, `[...rest]` is `{rest*}`. A segment starting with `_` is skipped |
| endpoints | files under the pages directory exporting `GET` / `POST` / … (`ALL` → `ANY`) become routes |
| srcDir / base / i18n | literal `srcDir`, `base`, `trailingSlash` and `i18n` from `astro.config.*`. Routes carry `base`. A locale segment sets `attrs.locale`; otherwise the default locale when `prefixDefaultLocale` is false |
| redirects | config entries become redirect pages (an external destination is only `attrs.redirect`), and `Astro.redirect` / `rewrite` / `context.redirect` are NAVIGATES_TO |
| middleware | USES_MIDDLEWARE from every page and route, in `sequence` order |
| actions | `POST /_actions/<name>` routes (nested: `shelf.clear`), plus CALLS from `actions.x()` calls and `<form action={actions.x}>` in any `.astro` file |
| components | `<Card />` → RENDERS. Imports from `.ts` and other `.astro` files resolve |
| template | `{…}` expressions, attribute values and `define:vars` are code at their real lines; `is:raw` children are text |
| islands | `client:load` / `idle` / `visible` / `media` / `only` and `server:defer` → RENDERS `attrs.client` / `client_value` / `server`; React/Preact `.tsx`, Vue `.vue` |
| links | `<a href>` → NAVIGATES_TO, also inside expressions |
| props / params | `attrs.props`, `attrs.params` |
| functions | functions in a `.astro` file are nodes; the page CALLS `getStaticPaths` |
| getStaticPaths | `attrs.get_static_paths` when the frontmatter exports it; the function is not evaluated |
| is:inline | `<script is:inline>` sets `attrs.inline_scripts` to the count |
| markdown pages | `.md` / `.mdx` / `.html` under `pages/` → page. Frontmatter (`---` YAML or `+++` TOML) `layout` → RENDERS. Root-relative Markdown links → NAVIGATES_TO. MDX imports, `<X />` and calls of imported functions |
| collections | `content.config.ts` / legacy `content/config.ts` → `table` nodes with `attrs.collection`, loader, base and entries. `getCollection` / `getEntry` / `getEntries` (imported from `astro:content`) → READS_TABLE from the calling function or page; `render` → READS_TABLE when the file reads one collection. `reference()` → HAS_RELATION |

## Stored fields

`field:<file>#<Class>.<name>` (`property: stored`) for a non-static class property whose
initializer is not a function, and for a constructor parameter property. An arrow-function
property is a method. A static readonly literal is a constant.

The checker’s property access is `READS_PROP` / `WRITES_PROP` (`exact`). Writes: assignment,
`++` / `delete`, `this.cache[k] =` (`via: item`), `this.items.push` (`via: mutating`). In
`.js`, `this.x =` without a declaration is `declared: this`.

Pinia `state` keys and Vue `data()` keys use the same edges (`hook: pinia` / `data`), from
`this.x`, `useStore().x`. React `useState` / `useReducer` and Vue `ref` / `reactive` in a
component or composable are `property: state`; `setCount(...)` is `via: setter`. Passing the
setter on is not a write. A `RENDERS` or `INSTANTIATES` edge inside `if` / `switch` / `&&`
carries `branch` and `branch_line`.

`cg readers` / `cg writers` take `Class.field`. Public numbers:
[TypeScript / JavaScript frameworks](validation-log.md#typescript--javascript-frameworks).

## Limitations

- Paths built in a loop or from config become `{param}` or are missed. Custom decorators that
  wrap `@Get` through `applyDecorators` are not routes (a blind spot in
  [Completeness](completeness.md)).
- Nest tokens are global. Request-scoped providers are treated as singletons. `@OnJob` and other
  `SetMetadata` systems are not entry points.
- Next `pageExtensions`, MDX-only pages, i18n `locales` and `generateStaticParams` are not
  applied. Regex middleware matchers are heuristic.
- Remix v1 nested-folder routes and `remix.config.js` `routes()` are not read. Route-module
  `meta`, `links`, `headers`, `shouldRevalidate` and `ErrorBoundary` are not nodes. Generated
  `+types` and Vite options other than a literal `appDirectory` are not read. A component that
  forwards `to` through props is not a link. TanStack Router and Expo Router are separate.
- Express middleware order is known inside one file. Dynamic `require(path)` is not followed.
- Untyped JavaScript resolves when the checker can see the object literal or the CommonJS
  export.
- Astro `.svelte` islands are not resolved. MDX expressions resolve by imported name (`resolved`),
  not by the type checker, and only relative or root-relative imports. Content reads need a literal
  collection name (a `reference()` value passed to `getEntry` is not followed); relative Markdown
  links, Markdoc, custom loaders' entries and MDX inside collection entries are not read. Computed
  config values, `injectRoute` from integrations, and i18n `domains` / `fallback` are not read.
