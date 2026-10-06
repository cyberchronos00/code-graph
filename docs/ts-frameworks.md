# TypeScript / JavaScript frameworks

What NestJS, Next.js and Express-style routers add on top of the TypeScript plugin. They share
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
| clients | `fetch`, axios, `ky` (+ `ky.create({prefixUrl})`), `ofetch` / `$fetch` (+ `.create`), `useSWR(key)`, OpenAPI `this.request({path, method})` and `__request(OpenAPI, {method, url})`. Same-repo calls are MATCHES_ROUTE (`attrs.in_repo`) |
| config | static `basePath`; literal `rewrites` as `uri_variants`. `NEXT_PUBLIC_*` (and `VITE_`, `NUXT_PUBLIC_`, `REACT_APP_`, `EXPO_PUBLIC_`, `PUBLIC_`) is `attrs.public` |

## Express, Koa, Fastify, Hono

Detect: `express`, `koa`, `@koa/router`, `fastify`, `hono`. Router values are followed
across `import` / `require`, `module.exports` and factories. A parameter typed
`FastifyInstance` / `Router` / `Hono`, or an untyped parameter that receives a literal path,
counts.

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

## Astro

An Astro project is detected from an `astro` dependency or an `astro.config.mjs` / `.js` / `.ts` /
`.mts` / `.cjs` file. Frontmatter between the `---` fences and `<script>` blocks are indexed as
TypeScript at their real lines in the `.astro` file.

| feature | graph |
|---|---|
| pages | `src/pages/**.astro` → page (`ui_page`). `index` is `/`, `[slug]` is `{slug}`, `[...rest]` is `{rest*}`. A segment starting with `_` is skipped |
| endpoints | files under `src/pages/` exporting `GET` / `POST` / … (`ALL` → `ANY`) become routes |
| components | `<Card />` → RENDERS. Imports from `.ts` and other `.astro` files resolve |
| getStaticPaths | `attrs.get_static_paths` when the frontmatter exports it; the function is not evaluated |
| is:inline | `<script is:inline>` sets `attrs.inline_scripts` to the count |

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
- Express middleware order is known inside one file. Dynamic `require(path)` is not followed.
- Untyped JavaScript resolves when the checker can see the object literal or the CommonJS
  export.
- Astro template expressions are not code yet (only component tags), and functions declared in
  a `.astro` file are not nodes of their own: their calls count for the file. `.md` / `.mdx` pages,
  content collections, islands, middleware, actions and a custom `srcDir` are not read.
