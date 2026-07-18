---
name: gitnexus-ensure
description: Internal — used by the gitnexus-* skills; not meant for direct invocation. Ensure the GitNexus CLI is installed and healthy under ~/.omc/dependencies/gitnexus (approved-source-only clone + build).
---

# omc gitnexus-ensure (internal)

GitNexus is omc's managed code-knowledge-graph dependency. It is never a PATH
binary — it runs as `node <CLI>` where:

```
CLI = ~/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js
```

(`~/.omc` is `$OMC_HOME` when that env var is set.)

## Step 1 — healthy already?

`node <CLI> --version` succeeds → report the version and end. Done.

(Updating an already-healthy install is `omc update`'s job — deterministic,
forces `main`. This skill only installs/repairs.)

## Step 2 — install (approved source ONLY)

The ONLY source ever cloned or accepted is:

```
https://github.com/chris-husse/GitNexus.git
```

- Destination `~/.omc/dependencies/gitnexus` already contains a git clone →
  check `git -C <dest> remote get-url origin`. Anything other than the
  approved URL → **REFUSE and stop** ("origin is X, not the approved GitNexus
  source") — never re-point, never build an unapproved tree. Approved →
  `git -C <dest> fetch origin --prune && git -C <dest> checkout main` and pull.
- No clone → `git clone https://github.com/chris-husse/GitNexus.git <dest>`.

## Step 3 — build (two-step; order matters)

1. `gitnexus-shared/` is a plain sibling package, NOT an npm workspace — the
   main build compiles it with ITS OWN `node_modules/.bin/tsc`, so install its
   deps FIRST: `cd <dest>/gitnexus-shared && npm install --no-audit --no-fund`.
2. `cd <dest>/gitnexus && npm ci && npm run build`.

## Step 4 — verify

`node <CLI> --version` must now succeed; report the version. If it doesn't,
surface the build output and stop — never claim success on a broken build.
