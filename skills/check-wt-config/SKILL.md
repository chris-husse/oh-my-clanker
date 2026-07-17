---
name: check-wt-config
description: Analyze this project's worktrunk config (.config/wt.toml) against omc's faithful-worktree expectations - does it copy what matters, will its hooks do weird things. Insight and suggested edits only; never edits the file.
---

# omc check-wt-config

omc's worktree model needs `wt` to copy the important gitignored files into
every new worktree (.env, caches, and the `.gitnexus`/`.omc/docs` knowledge
snapshot). This skill judges whether the project's config actually does that.

## Step 1 — gather both sides

- The project's config: `<repo root>/.config/wt.toml` (missing → say `omc
  start`/`omc watch` will seed the starter automatically, and stop).
- omc's canonical starter for comparison: run `omc internal wt-template`.

## Step 2 — analyze (real judgment, not a diff)

- **Copy coverage**: is a `copy-ignored` step wired in `[post-start]`? Do
  `exclude` patterns skip things this project obviously needs — `.env`,
  credentials, build caches — or break the snapshot model by excluding
  `.gitnexus/` or `.omc/docs/`?
- **Hook safety**: do `pre-start`/`post-start`/other hooks look dangerous
  (destructive commands, `rm -rf`, network side effects), unbounded/slow, or
  likely to prompt and hang a non-interactive worktree creation?
- **Missing setup**: does the repo visibly use submodules (`.gitmodules`) or
  direnv (`.envrc`) without a matching `pre-start`? (See the starter's
  pre-start line.)
- Anything else that will plausibly "do weird things" on `wt switch
  --create` — trust your read of the project over the template.

## Step 3 — report

A short verdict first ("looks good" / "two things will bite you"), then each
finding with the config line, why it matters, and a concrete suggested edit.
**Never edit `.config/wt.toml` yourself** — it's the project's committed
file; the user applies what they agree with.
