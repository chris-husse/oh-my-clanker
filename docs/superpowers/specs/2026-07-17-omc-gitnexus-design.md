# explain / document / index — the GitNexus layer

Approved 2026-07-17 (approach A: pure-skill). Simplified port of the chicken's
GitNexus integration: skills own install and drive the CLI directly; no Python,
no MCP setup, no per-editor config mutation.

## Dependency

GitNexus installs to `~/.omc/dependencies/gitnexus`, cloned ONLY from
`https://github.com/chris-husse/GitNexus.git` (a pre-existing clone with any
other origin is refused — the chicken's approved-source guarantee). Build:
`npm install` in `gitnexus-shared/` FIRST (plain sibling, not a workspace —
without its own tsc the build dies), then `npm ci && npm run build` in
`gitnexus/`. The CLI is never on PATH: run
`node ~/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js …`.

## Layout decisions

- The index stays at GitNexus-native `.gitnexus/` in the primary worktree root
  (hooks/status/registry expect it) — gitignored. The spec'd
  `.omc/docs/gitnexus/index` path is dropped.
- LLM docs: `gitnexus wiki` writes `.gitnexus/wiki/`; the document skill syncs
  that to `.omc/docs/gitnexus/docs/` (the user-visible location) — gitignored.
- Primary-worktree model: index/document always operate on the primary root
  (`git worktree list`, first entry), warning when invoked from a linked
  worktree; explain reads the primary root's graph/docs from anywhere. Cadence:
  run `/omc:index` + `/omc:document` in the main checkout as main moves so
  `/omc:explain` in worktrees stays current.

## Skills

| Skill | Layer | Does |
|---|---|---|
| `gitnexus-ensure` | internal | CLI healthy (`node <cli> --version`) → done; missing → approved-source clone + two-step npm build + verify. |
| `gitnexus-index` | internal | ensure → primary root → `gitnexus analyze` with index-only flags (`--skip-agents-md`; suppress hook/skill installs where flags exist). Incremental (analyze updates stale indexes). |
| `gitnexus-document` | internal | ensure → index present (else run gitnexus-index) → `gitnexus wiki --provider <omc default provider> [--model <cfg>]` → sync `.gitnexus/wiki/` → `.omc/docs/gitnexus/docs/`. gitnexus's wiki providers include `claude`/`codex`/`opencode` natively (it drives the local agent CLI — same auth omc already requires), so the mapping is IDENTITY; never fall through to gitnexus's `openai` default. |
| `gitnexus-explain` | internal | ensure → index present (else "run /omc:index first") → COMPOSE `query` → `context` → `impact` → `cypher` (no CLI explain command exists) + read `.omc/docs/gitnexus/docs/` when present → cited findings. |
| `index` | user-facing | delegate to gitnexus-index. |
| `document` | user-facing | delegate to gitnexus-document. |
| `explain <question>` | user-facing | project's `.omc/skills/explain-context/SKILL.md` if present (project-specific context: doc locations, conventions) → gitnexus-explain → one synthesized answer with file/symbol citations. |

## Dogfooding (this repo)

`.omc/skills/` gains: `verify` (Docker smoke suite), `review` (diff review of
`origin/main..HEAD`, Critical/Important/Minor verdict, pass unless
Critical/Important), `explain-context` (README, docs/superpowers/specs+plans,
docker/PLUGIN-NOTES.md, .superpowers/sdd/progress.md). `build` already exists.

## E2E cost control: pre-baked dependency

The clone+build is baked into the E2E image at `/root/.omc/dependencies/
gitnexus` — BEFORE the `COPY . /repo` layer, so it caches across repo changes.
Per-test containers inherit it; `gitnexus-ensure` exercises its verify path
(and its install path stays covered by prose review + the approved-source
refusal being pure text).

## Testing

- Unit: manifest/frontmatter for all 7 skills (internal markers on gitnexus-*);
  contract needles — approved URL literal + gitnexus-shared-first in ensure,
  provider mapping + wiki sync in document, primary-root resolution in index,
  compose-queries + no-explain-command in explain, explain-context lookup in
  explain (user-facing); dogfood files exist.
- E2E (claude, fail-loud): (a) `/omc:index` against `/repo` in-container —
  assert `.gitnexus/` exists and `gitnexus list` includes the repo;
  (b) `/omc:explain "how does omc start derive the branch slug?"` — judged,
  must cite the slug machinery; (c) `/omc:document` — assert markdown lands in
  `.omc/docs/gitnexus/docs/`.
- Local acceptance on this repo (the new chicken): `/omc:index` +
  `/omc:explain` via `claude --plugin-dir`, using the real `~/.omc`.

## Verified at design time (was verify-at-implementation)

- analyze suppression flags exist: `--skip-agents-md` and `--skip-skills`
  (analyze-config.ts) — the index skill passes both.
- `wiki --provider` accepts `openai, openrouter, azure, custom, cursor,
  claude, codex, opencode` (cli/index.ts) — identity mapping confirmed.

Still open: in-container build duration (pre-bake should hide it).
