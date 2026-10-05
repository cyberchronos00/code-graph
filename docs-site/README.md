# code-graph docs site

Nuxt 4 + Nuxt UI + Nuxt Content site for the markdown in the repository `docs/` directory (`docs/*.md`, `docs/mcp/`, `docs/media/`). The collection reads `../docs` at build time. Do not copy that tree into `docs-site/content`.

Design tokens: Nuxt UI primary lime, secondary violet, neutral zinc, Geist / Noto Sans JP / Geist Mono, and a light/dark color-mode toggle.

## Local

From `docs-site/`:

```bash
pnpm install
pnpm dev
```

Open the URL Nuxt prints (usually `http://localhost:3000`).

Production build (Cloudflare Workers module preset, pages prerendered):

```bash
pnpm run build
pnpm preview   # optional local preview of the built output
```

## Deploy to `*.workers.dev`

This repo does not create the Cloudflare project. After the Worker exists, from `docs-site/`:

```bash
pnpm exec wrangler login
pnpm run deploy
```

`wrangler.toml` names the Worker `code-graph-docs`. The public URL is `https://code-graph-docs.<account-subdomain>.workers.dev`.

### Dashboard clicks (owner)

1. Open [Cloudflare dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **Create**.
2. Choose **Workers** → **Import a repository** (or **Connect to Git**).
3. Select the `cyberchronos00/code-graph` repository and the branch you deploy from.
4. Set **Root directory** to `docs-site`.
5. Build settings:
   - Package manager: **pnpm**
   - Build command: `pnpm install && pnpm run build`
   - Deploy command: `pnpm exec wrangler deploy`
   - Node.js version: **22** or newer (Wrangler / Nitro)
6. Leave the Worker name as `code-graph-docs` so it matches `wrangler.toml` (`workers_dev = true`).
7. Save and deploy. The `*.workers.dev` hostname appears on the Worker overview.
8. Optional: **Settings → Domains & Routes → Add** a custom domain.

`wrangler.toml` points `main` at `.output/server/index.mjs` and static assets at `.output/public` (the Nitro `cloudflare_module` output). `pnpm run deploy` runs `wrangler deploy` from `docs-site/` so that file is the one Wrangler reads. Nitro also writes `.output/server/wrangler.json` (paths relative to that directory) and may warn that the root `main` / `assets` fields are not copied into that generated file. Use one or the other, not both in the same command.

Nuxt Content logs that the server SQL adapter switches to a D1 binding named `DB`. Prerendered pages and the docs search dump are static assets, so the site renders on `*.workers.dev` without creating that database. Create a D1 database and bind it as `DB` only if you need the live `/__nuxt_content/*/query` route.

Media files under `docs/media/` stay in git and are not copied into the site bundle. Relative links to them resolve to GitHub.
