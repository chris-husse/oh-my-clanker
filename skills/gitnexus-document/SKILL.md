---
name: gitnexus-document
description: Internal — used by /omc:document; not meant for direct invocation. Regenerate the project's LLM documentation from its GitNexus graph and sync it to .omc/docs/gitnexus/docs in the primary worktree root.
---

# omc gitnexus-document (internal)

## Step 1 — ensure CLI + index

Run the `gitnexus-ensure` skill. Resolve the primary worktree root
(`git worktree list`, first entry). No `.gitnexus/` index there yet → run the
`gitnexus-index` skill first.

## Step 2 — generate the wiki

Determine the provider: omc's configured default (`llm.default` in
`~/.omc/config.json`; if unreadable, ask rather than guess). gitnexus's wiki
providers include `claude`, `codex`, and `opencode` natively — it drives the
LOCAL agent CLI, so this uses the same auth omc already requires. Pass the
provider EXPLICITLY (never fall through to gitnexus's `openai` default, which
needs credentials the user may not have); add `--model` only when omc config
sets one for that provider:

```sh
node <CLI> wiki --provider <omc default provider> [--model <configured model>]
```

Run it from the primary root. This is LLM-driven and can take a while on a
large repo — that's expected; stream/report its progress.

## Step 3 — sync to the omc layout

`gitnexus wiki` writes `.gitnexus/wiki/` (markdown + `index.html` +
`module_tree.json`). Mirror it to the user-visible location in the primary
root:

```sh
rm -rf .omc/docs/gitnexus/docs && mkdir -p .omc/docs/gitnexus && cp -R .gitnexus/wiki .omc/docs/gitnexus/docs
```

(`.omc/docs/` is generated output — keep it gitignored.)

## Step 4 — report

List what landed in `.omc/docs/gitnexus/docs/` (page count, top-level titles).
A failed wiki run → surface its output and stop; never sync a partial wiki
silently.
