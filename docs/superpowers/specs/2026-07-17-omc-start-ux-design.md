# `omc start` launch UX: progress, precise failures, plugin self-heal

Driven by live first-run feedback (2026-07-17): a minute of blank screen during
the slug call, and — worse — the seeded session opening on
`Unknown command: /omc:start` because nothing ensured the plugin was installed.
The E2E missed it because `setup-plugins.sh` pre-installs the plugin in every
container: the fresh-user state was untestable. Fixed red→green.

## Progress output (stderr, plain lines, no TTY tricks)

`omc start` narrates each phase as it begins, with honest timing expectations:

```
→ probing tools (git, wt, claude)
→ omc plugin for claude: ok            # or: missing — installing from <source>
→ generating slug via claude (LLM call, typically 15–60s)…
✓ slug: cops-855-migrate-chicken
→ creating worktree feature/cops-855-migrate-chicken (base origin/main)
✓ worktree: /path/to/worktree
→ launching claude session "cops-855-migrate-chicken" seeded with /omc:start
```

Failures are stage-labeled and keep the skill's structured diagnostics:
`✗ could not generate slug [mcp-unauthenticated]: <skill message>` (rc 2).

## Plugin self-heal (user decision: "start self-heals")

New module `src/omc/plugin.py`, `ensure_plugin(ctx, cfg, *, check_only)`:

- claude: probe `claude plugin list` for `omc@`. Missing → auto-install with
  NO consent prompt (chicken precedent: pinned-source auto-install; the plugin
  is omc's own repo): `claude plugin marketplace add <source>` (failure
  tolerated — may already exist) then
  `claude plugin install omc@oh-my-clanker --scope user`, then re-probe.
  Install failure → OmcError carrying the manual commands.
- The marketplace `<source>` comes from uv's receipt (`installsrc`): a
  directory install uses the checkout path; a git install parses
  `owner/repo` from the URL; fallback `chris-husse/oh-my-clanker`.
- codex/opencode: no verified scriptable probe in v1 — skipped silently
  (documented follow-up).
- `--dry-run` reports plugin status in the plan but NEVER installs.

## Testing (red first — the E2E gap is the bug)

- New E2E `test_e2e_first_run.py`: container simulates the REAL first run by
  removing the plugin AND the marketplace, then `omc start --headless` must
  succeed with NO `Unknown command` in the transcript and the plugin present
  afterwards. This test FAILS on the pre-fix code with the user's exact
  symptom.
- Existing headless-start E2Es additionally assert `"Unknown command" not in`
  the transcript (tightened).
- Unit: plugin probe/heal argv contracts against stub `claude`; progress-line
  presence and ordering via capsys; dry-run never installs.
