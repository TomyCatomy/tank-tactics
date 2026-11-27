# Neon CLI config

These files configure the [Neon](https://neon.com) project that hosts the game's Postgres
database. Only the Neon command-line tool (`npm i -g neon`) uses them, on a developer's
machine. The game server never reads them and the site doesn't serve them. The server only
needs `DATABASE_URL` (see `ServerConfig`).

- `neon.ts`: which Neon services the project uses (Postgres only, no Neon Auth) and the
  policy for Neon branches (new non-default branches expire after 7 days).
- `package.json`, `package-lock.json`: the `@neon/config` package that `neon.ts` imports.
  Run `npm ci` in this folder before using the CLI.

The repo is linked to a Neon project by `.neon` at the repo root (git-ignored; create it with
`neon init` or `neon link`). Run the CLI from the repo root and point it at this folder:

```bash
neon config plan --config neon/neon.ts   # preview
neon deploy --config neon/neon.ts        # apply, and pull DATABASE_URL etc. into .env.local
```
