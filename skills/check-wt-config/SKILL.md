---
name: check-wt-config
description: Analyze this project's worktrunk config (.config/wt.toml) against omc's faithful-worktree expectations - does it copy what matters, will its hooks do weird things. Insight and suggested edits only; never edits the file.
---

# omc check-wt-config

omc's worktree model needs `wt` to copy the project's important gitignored
files into every new worktree (.env, secrets, local caches).
omc itself copies the primary checkout's `.omc/` (including the `.omc/docs`
knowledge snapshot) into every cut, adding only what is missing, so the
config never needs to know about it. The starter excludes `.gitnexus/`
(omc uses the primary graph) and `target/` (build artifacts). This skill
judges whether the project's config does that.

## Step 1 — gather both sides

- The project's config: `<repo root>/.config/wt.toml` (missing → say `omc
  watch` will seed the starter automatically, and stop).
- omc's canonical starter for comparison: run `omc internal wt-template`.

## Step 2 — analyze (real judgment, not a diff)

- **Copy coverage**: is a `wt step copy-ignored` command wired in `pre-start`
  or `post-start`, as a string or a named step? Blocking `pre-start` makes the
  copy available before the new worktree is used. Excluding `.gitnexus/` and
  `target/` is expected; excluding `.env` or credentials leaves the worktree
  unfaithful. Excluding `.omc/` is harmless — omc copies it itself.
- **Hook safety**: do `pre-start`/`post-start`/other hooks look dangerous
  (destructive commands, `rm -rf`, network side effects), unbounded/slow, or
  likely to prompt and hang a non-interactive worktree creation?
- **Missing setup**: does the repo visibly use submodules (`.gitmodules`) or
  direnv (`.envrc`) without a matching `pre-start`? (See the starter's
  pre-start line.) If `.envrc` is ignored, the blocking copy must finish
  before `direnv allow .` so the copied file is trusted before launch. Guard
  the allow step when `.envrc` or direnv is absent.
- Anything else that will plausibly "do weird things" on `wt switch
  --create` — trust your read of the project over the template.

## Step 3 — report

A short verdict first ("looks good" / "two things will bite you"), then each
finding with the config line, why it matters, and a concrete suggested edit.
**Never edit `.config/wt.toml` yourself** — it's the project's committed
file; the user applies what they agree with.
