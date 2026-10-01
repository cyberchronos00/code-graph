# TypeScript / JavaScript server and full-stack frameworks

Framework layers on top of the TypeScript plugin (`plugins/ts`) for **NestJS**, **Next.js** and **Express-style
routers** (Express, Koa + `@koa/router`, Fastify, Hono). They read the same single TypeScript program as the Nuxt layer,
so imports, aliases, re-exports and types resolve through the real checker. Plain-JavaScript projects (CommonJS or ESM, with
or without `jsconfig.json`) are indexed with `allowJs`.

The samples `examples/bookstore-nest`, `examples/bookstore-next` and `examples/bookstore-express` show every feature
below. `tests/test_ts_frameworks.py` asserts the exact edges on them.

## How it fits together

```
extractor/extract.mjs ─┬─ language facts (nodes, CALLS, RENDERS, HTTP calls, ...)
                       └─ extractor/fw.mjs: framework-neutral facts
                            classes + decorators (described args), constructor params, router-style calls,
                            ORM / query-builder member calls, instances (declarations + initialisers),
                            module exports / directives / req.method checks, env reads, {provide: ...} objects
plugins/tsweb/   shared helpers: path templates, add_route, env / config / ORM tables, in-repo client -> route links
plugins/nest/    NestPlugin      (detect: @nestjs/core or @nestjs/common dependency, or nest-cli.json)
plugins/nextjs/  NextPlugin      (detect: next dependency or next.config.*)
plugins/express/ ExpressPlugin   (detect: express / koa / @koa/router / fastify / hono / ... dependency)
```

Arguments are captured as *described values* (`{s: "/v1"}`, `{ref, node, key}`, `{call, args, mod}`, `{obj}`, `{fn}`,
...), and the Python layers resolve them. That keeps the framework rules in Python and the extractor generic. Several
TS framework layers can run on one project (Nest on Express, Next with an Express custom server). The shared post-pass
(env, ORM tables, in-repo links) runs once, after the last of them.

## NestJS

| feature | what is modelled |
|---|---|
| modules | `@Module({imports, controllers, providers, exports})`: BINDS (providers) / REFERENCES edges, `attrs.nest_module`; `RouterModule.register([{path, module, children}])` path prefixes |
| routes | `@Controller(prefix \| {path})` + `@Get/@Post/@Put/@Patch/@Delete/@All/@Head/@Options(path)` → `route:<METHOD> <uri>` (entry `http_route`) with ROUTES_TO the handler method; `app.setGlobalPrefix(p, {exclude})`; `enableVersioning` URI (`/v1/...`, `defaultVersion`) and `@Version`; with header / media-type versioning the version goes to `attrs.version` instead of the path |
| enhancers | `@UseGuards/@UseInterceptors/@UsePipes/@UseFilters` on class and method, global `APP_GUARD`/`APP_INTERCEPTOR`/... providers and `app.useGlobalGuards(...)`: `attrs.guards/interceptors/pipes/filters`, USES_MIDDLEWARE to `canActivate`/`intercept`/`transform`/`catch` |
| DTOs | `@Body() dto: CreateXDto` / `@Query() q: XQueryDto`: `attrs.body_dto/body_fields`, `query_dto/query_fields` (class properties, `?` for optional) |
| DI | constructor and `@Inject()` property injection. Tokens: class types, `@Inject('TOKEN')`/`@Inject(SYMBOL)`, `@Dependencies(A, B)` (JS). Providers: classes, `{provide, useClass \| useExisting \| useFactory (the class it constructs) \| useValue}` found anywhere (module arrays, spread arrays, custom-provider files, dynamic modules' `forRoot()`), `ClientsModule.register([{name}])`. Result: INJECTS consumer → provider, CALLS through `this.<member>.<method>()` (`via: nest-di`), BOUND_TO from an abstract class token's methods to the provider's. Library providers (`ConfigService`, `JwtService`, `@InjectX()` decorators, tokens imported from packages) count as external |
| GraphQL | `@Resolver` + `@Query/@Mutation/@Subscription/@ResolveField` → `route:GRAPHQL Query.<name>` |
| other entry points | `@Cron/@Interval/@Timeout` → `schedule:` (`scheduled`); BullMQ `@Processor` + `WorkerHost.process`, Bull `@Process(name)` → `job:<queue>` (`queue_job`); `@OnEvent(name)` → `listener:` (`listener`) with `event:<name>`; `@MessagePattern/@EventPattern`, `@SubscribeMessage` (WebSocket gateways with namespace), `@GrpcMethod` → `message:<transport>:<pattern>` (`message_handler`); nest-commander `@Command({name})` → `command:nest:<name>` (`cli_command`, operator-only) |
| producers | `queue.add(name)` on `@InjectQueue(q)`, `EventEmitter2.emit(name)`, `ClientProxy.send/emit(pattern)` → DISPATCHES to the job / event / message node (an outgoing `message:` node when the handler lives in another service) |
| data | TypeORM / MikroORM `@Entity(name)` (snake_case default), sequelize-typescript `@Table`, Mongoose `@Schema` (lower-case plural collection) → MAPS_TO_TABLE + columns + HAS_RELATION; `@InjectRepository(E)` / `Repository<E>` / `@InjectModel(E.name)` members, Prisma (`schema.prisma` with `@@map`/`@map`), Drizzle tables, Kysely `selectFrom/insertInto/...('t')`, knex `db('t')` → READS_TABLE / WRITES_TABLE |
| config | `process.env.X`, `ConfigService.get('X')` (UPPER_SNAKE keys = env), `registerAs('ns', () => ({...}))` → `config:ns.key` nodes + READS_ENV |

## Next.js

| feature | what is modelled |
|---|---|
| app router | `app/**/page.(tsx\|jsx\|ts\|js\|mdx)` → `page:` nodes (`ui_page`, name in Next notation, `attrs.uri` as a template); layouts / templates / loading / error / not-found / default (`ui_global`) with USES_LAYOUT up the tree; `route.ts` handlers: exported `GET/POST/...` functions, `export const GET = withX(handler)` wrappers (`attrs.wrapped_by`), `export { h as GET }` and `export * from` re-exports |
| segments | `[id]` → `{id}`, `[...slug]` → `{slug*}` (one or more), `[[...slug]]` → `{slug*?}` (zero or more), route groups `(x)` dropped, parallel slots `@x` dropped, intercepting `(.)x` / `(..)x` resolved to the intercepted path (best effort), `_private` folders skipped |
| pages router | `pages/**` pages (minus `_app`, `_document`, `_error`), `pages/api/**` handlers: method from `req.method === 'X'` / `switch (req.method)` (also destructured `const { method } = req`), else `ANY`; `getServerSideProps` / `getStaticProps` / `generateMetadata` reached from the page |
| server actions | functions exported from a `'use server'` module or with an inline `'use server'` → `route:ACTION <file>#<fn>` (`http_route`); client components call them directly (CALLS) |
| middleware | `middleware.ts` / `proxy.ts` `config.matcher` (strings, arrays, `{source}` objects; `:path*` patterns) → USES_MIDDLEWARE from matching routes and pages (`resolved`). A regex matcher, or no matcher at all, links every route and page (`heuristic`) |
| next.config | static `basePath` (prefixed to every route and page) and literal `rewrites` (`attrs.rewrites`, `uri_variants` used when matching client calls) |
| clients | `fetch`, `axios`, `ky` (+ `ky.create({prefixUrl})`), `ofetch`/`$fetch` (+ `.create`), `useSWR(key)`, OpenAPI-generated clients (`this.request({path, method})`, `__request(OpenAPI, {method, url})`); same-repo calls are linked to route handlers (MATCHES_ROUTE, `attrs.in_repo`) |
| env | `process.env.X`; `NEXT_PUBLIC_*` (and `VITE_`, `NUXT_PUBLIC_`, `REACT_APP_`, `EXPO_PUBLIC_`, `PUBLIC_`) keys get `attrs.public` |

## Express, Koa, Fastify, Hono

Router instances are declarations whose initialiser is a known factory (`express()`, `express.Router()`, `Router()`,
`new Router({prefix})`, `Fastify()`, `new Koa()`, `new Hono()`, `.basePath(p)`). They are followed across files through
`import`/`require`, `module.exports`, factory functions that return them, and `const api = Router().use(..)` chains.
Function parameters typed `FastifyInstance` / `Router` / `Hono`, or untyped parameters that receive route calls with a
literal path, also count.

| feature | what is modelled |
|---|---|
| routes | `x.get/post/put/patch/delete/del/all/head/options(path, ...mw, handler)`, `x.route(path).get(h).post(h)`, `fastify.route({method, url, handler})`, `hono.on(methods, path, h)`; `:id` → `{id}`, `:id?` → `{id?}`, `:p+` / `*name` → `{p*}`, `*` / `:p*` / `{*p}` → `{p*?}` |
| mounting | `x.use(prefix?, ...mw, sub)` and Ghost-style `lazyUse`, `sub.routes()` (koa-router), `fastify.register(plugin, {prefix})` (the plugin's first parameter is the child), `hono.route(prefix, sub)`, `basePath`, `router.prefix(p)`; a function called with a router argument (`routes(app)`) binds its parameter. Prefix chains across files are joined into full paths |
| handlers | direct functions, `controller.method` references (including `exports.x = fn` CommonJS controllers and handler objects such as `{browse: {query(frame) {}}}`), wrappers (`asyncHandler(fn)`, `http(api.posts.browse)`), members looked up through lazy `require()` registries (`get posts() { return wrap(require('./posts')) }`) |
| middleware | route-level middleware, `preHandler`/`onRequest`/... route options, fastify `addHook`, `x.use(mw)` router-level middleware (only the ones registered before the route or mount in the same file) and middleware passed with a mount: `attrs.middleware` + USES_MIDDLEWARE where the function resolves |
| schemas | Fastify `schema.body.properties` / `querystring.properties` → `attrs.body_fields` / `query_fields` |
| confidence | `exact`: literal path, known factory, direct handler. `resolved`: through wrappers, handler objects, registries, aliases or params. `heuristic`: the router is never mounted from an app (`attrs.unmounted`, prefix unknown), the receiver is recognised by name only, or the path is not a literal |

## Cross-repo linking

`cg link --backend api.db --frontend web.db` reuses the matcher in `codegraph/link.py` for any TypeScript frontend (Nuxt,
Next, Vue, React) against a Nest, Next or Express backend. Calls whose base URL is traced (an axios instance with a literal
`baseURL`) match exactly. Calls whose base is configuration (`process.env.X`, `ky.create({prefixUrl})`, generated clients)
have an unknown origin, so they are aligned with the end of the route URIs (`heuristic`). The most specific alignment wins
(literal over parameter). Catch-all segments (`{rest*}`, `{rest*?}`) match the remaining path.

**Payloads are not compared.** The matcher compares method and path only. Routes carry `body_fields` / `query_fields`
(Nest DTOs, Fastify schemas), and client calls carry `body_keys` / `query_keys`, so a payload check can be added on top.
Today nothing compares them.

## Validation on public projects

Shallow clones, indexed with `CODEGRAPH_NO_CACHE=1` on an 8-core box. Index time is wall time for `cg index`.
| project (revision) | files | index time | nodes / edges | found | spot-check | resolution / links | parse failures | gaps |
|---|---|---|---|---|---|---|---|---|
| immich `server/` (a81ddfa), NestJS + Kysely | 458 TS | 7.4 s | 4,759 / 19,780 | 295 HTTP routes (= the 295 route decorators in 47 controllers, global prefix `api`, `RouteKey` enum paths), 13 nest-commander commands, 86 `@OnEvent` listeners, 60 tables | 47 album / asset / auth routes: all correct; `GET /api/albums/{id}` → controller → service → repository → `table:album` | DI: 200 of 201 project tokens resolved (99.5%), 49 library tokens; the miss is a `string[]` param | 0 | immich's own `@OnJob` job decorators are not entry points |
| nest `sample/` 01–37 (7fb52e7), federation sub-apps indexed one by one | 41 apps | 66 s total (1.4–2.6 s each) | 1,043 / 1,708 | 62 HTTP routes (incl. `@Sse`), 31 GraphQL operations, 5 message handlers, 1 queue job, 3 schedules, 1 listener | route count equals the route-decorator count in all 27 samples with routes | DI: 44 resolved + 13 library, 0 unresolved | 0 | the 31/32 roots hold several apps and are not a project themselves |
| Ghost `ghost/core` (412652f), Express, CommonJS + TS | 1,740 JS/TS | 12.4 s | 9,429 / 19,391 | 335 routes: 242 Admin API, 20 Content API, 39 Members API, plus frontend and service routers | 20 random routes: 20/20 paths correct, 19/20 handlers resolved (`http(api.posts.browse)` → `controller.browse.query` through the lazy `require()` registry) | 11 routes without a project handler (library middleware factories), 5 routers never mounted from an app | 0 | middleware attribution on `lazyUse` apps is coarse |
| dub `apps/web` (7e01013), Next.js app router + Prisma | 3,428 TS | 24.8 s | 15,726 / 50,246 | 198 pages, 57 layouts, 653 route handlers, 134 server actions, 86 Prisma models | 20 random route handlers and pages: all correct (route groups, `[...slug]`, re-exported handlers) | in-repo client → handler links: 299 of 363 endpoints (the rest are external URLs or dynamic) | 0 | 7 handlers built by libraries (`NextAuth(...)`, workflow `serve(...)`) have no project function |
| vercel/next.js `examples/` (fbd032ff) | 224 apps | 384 s total (≤ 2.3 s each) | 6,186 / 10,537 | 221 detected as Next: 413 pages, 236 layouts, 22 route handlers, 125 `pages/api` routes, 19 server actions | 22 random pages / routes: all correct | in-repo links: 47 of 69 endpoints | 1 file (with-custom-babel-config: non-standard syntax) | 5 apps with MD/MDX-only pages or Electron renderers have no pages; 3 monorepo roots (with-zones, with-yarn-workspaces, with-stencil) must be indexed per app |
| realworld express + Prisma (30b68e1) → realworld vue3 client (741c215) | 25 + 43 | 2.3 s + 1.9 s, link 0.05 s | 142 / 277 + 196 / 530 | 20 routes under `/api` (`Router().use(..)` chains) | 20/20 correct | `link`: 19 of 20 client endpoints matched (generated client, origin unknown → `heuristic`); the miss is a dynamic URL | 0 | — |

Spot-checks were done by hand against the source (decorators, router files, file-system routes). The "found" counts
come from the graph (`SELECT count(*) FROM nodes WHERE kind='route'` etc.).

## Limitations

- **Static only.** Routes registered in loops, from config files, or with computed paths become `{param}` / `{regex}`
  templates or are missed. Decorator arguments must be literals, consts, enums or simple expressions.
- **Nest:** tokens are global (provider scoping per module, `exports` visibility and token collisions across modules are not
  modelled). Request-scoped / transient providers are treated like singletons. Custom decorators that wrap `@Get` etc. through
  `applyDecorators` are not recognised as routes. Project-specific job / event systems built on `SetMetadata` are not entry
  points. Header / media-type versioning is recorded in `attrs.version`, not in the path.
- **Next:** `pageExtensions`, MDX-only pages and i18n `locales` in next.config are not applied. Regex middleware matchers
  are not evaluated (every route and page gets a heuristic USES_MIDDLEWARE). `generateStaticParams` is not
  evaluated. Rewrites to other origins are recorded but not followed.
- **Express-style:** middleware order is only known within one file. Error handlers are not separated from middleware.
  Dynamic `require(path)` and routers passed around through containers are not followed. Express 5 / path-to-regexp v8
  syntax is supported for the common forms (`{:id}`, `*name`, `{*name}`).
- **JavaScript without types:** calls through untyped objects resolve only when the checker can infer the shape (CommonJS
  exports, object literals). Factory-built services (`module.exports = createService(deps)`) often stay unresolved.
- **Linking:** payload/DTO compatibility is not checked (see above), and calls with an unknown base URL are always
  `heuristic`.
