# Project post-watch hook — `.omc/hooks/post-watch.sh`

Approved 2026-07-18. Gives a project a CLI-side hook into `omc watch`: a
project-owned script that runs after a watch cycle actually did work
(sync/refresh, including doc generation). This is omc's first CLI-side
project extension point, parallel to the session-side `.omc/skills/<name>`
namespace: skills extend sessions, `.omc/hooks/` extends the CLI. The
directory name deliberately leaves room for future events without building
an event registry now.

## Contract

- **Location**: `<primary root>/.omc/hooks/post-watch.sh`. Present → runs;
  absent → nothing, no narration. No `Config` change, no CLI flag —
  presence IS the opt-in (same doctrine as `.omc/skills/`). The hook is
  committed, project-owned repo content (like `.omc/skills/`), so a watch
  ff-sync can itself update the hook. Trust boundary: this means watch
  executes freshly-synced remote code unattended — push access to the base
  branch is code execution on the watching machine, the same trust class as
  `wt.toml` hooks/CI config. The sync delivering a new hook version is
  itself an action tick, so the new version runs on that same tick.
- **Trigger**: after a tick whose outcome token is an ACTION — `"synced"`
  (new commits arrived and the index/docs refresh ran) or `"refreshed"`
  (`--once` forced refresh) — so every `--once` run fires it. Quiet ticks
  (`up-to-date`, `off-branch`, `dirty`, `diverged`, fetch/merge failures)
  never fire it.
- **Invocation**: `["bash", <abs path to hook>]` through `ToolContext.run`
  (array argv, no shell interpolation; `bash` execution means no
  executable-bit footgun). cwd = primary root. Synchronous, inside the
  tick — watch never backgrounds anything (no-daemon doctrine).
- **Environment**: inherits the watch process env plus
  `OMC_WATCH_OUTCOME` = `synced` | `refreshed`, so a hook can distinguish
  "new commits landed" from "forced refresh".
- **Timeout**: 600 s (hardcoded; no config knob — a project with slower
  work should background it itself). Backgrounded work MUST redirect its
  output (`cmd >/dev/null 2>&1 &`) — a background child holding the stdout
  pipe makes the run block to the full timeout and report a false failure.
  Timeout is reported as a failure.

## Narration (stderr, `_say` style)

- Start (always — it is an action outcome, quiet-token doctrine does not
  apply): `→ running project post-watch hook (.omc/hooks/post-watch.sh)`
- Success: `✓ post-watch hook done`
- Failure/timeout: `✗ post-watch hook failed (exit N) — log: <path>`
  (timeout narrates `timeout` in place of the exit code).

## Logging

Hook stdout+stderr are captured (normal `ToolContext.run` capture) and
written to a temp log on EVERY run: `tempfile` with
`prefix="omc-post-watch-"`, `suffix=".log"` (system temp dir — `/tmp/…` on
Linux, `$TMPDIR` on macOS). The path is narrated only on failure; the file
is left for the OS to clean up.

## Failure semantics

Watch doctrine applies unchanged: a failing or timing-out hook warns and
the loop continues — never crashes, never affects the next tick. `--once`
still exits 0 when the hook fails (consistent with index/wiki refresh
failures, which also never change the exit code).

## Implementation shape

All in `src/omc/watch.py`:

- New helper
  `_post_watch_hook(ctx: ToolContext, root: str, outcome: str) -> None` —
  discover, narrate start, run with timeout and
  `extra_env={"OMC_WATCH_OUTCOME": outcome}`, write the log, narrate the
  result. Never raises (wraps `TimeoutExpired`/`OSError`, precedent
  `toolctx.tool_version`; `TimeoutExpired` carries the partial
  stdout/stderr, which still goes into the log).
- Call site: `run_watch`'s loop (covers `--once` too), after `_tick`
  returns, gated on the token: `if last in ("synced", "refreshed")`.
  Keeping the call OUT of `_tick` preserves `_tick`'s contract (one cycle →
  outcome token) and its tests.

## Testing

Unit (`tests/unit/test_watch.py`, fake-ToolContext style, TDD red→green):

1. Hook present + synced tick → hook argv ran, start line narrated, `✓` on
   rc 0.
2. Hook present + quiet tick (loop-mode up-to-date) → hook did NOT run.
3. Hook absent + synced tick → no hook narration.
4. Hook exits non-zero → `✗ post-watch hook failed (exit N) — log:` with an
   existing log path whose content is the hook's output; loop survives;
   `--once` still returns 0.
5. Hook timeout → failure narration, loop survives.

E2E (`tests/e2e/test_e2e_watch.py`, Docker-per-test, real `omc watch
--once` — required):

1. Happy path: `make_work_repo`, seed `.omc/hooks/post-watch.sh` that
   writes `$OMC_WATCH_OUTCOME` into a marker file; run `omc watch --once`;
   assert rc 0, hook narration present, marker exists with the outcome
   token (proves execution, cwd, and env).
2. Failure path: hook echoes to stderr and exits 1; assert rc 0, `✗
   post-watch hook failed (exit 1) — log:` present; extract the log path
   from output, `cat` it in the container, assert the stderr line was
   captured.

## `--auto-build` (added mid-build at user request, 2026-07-18)

`omc watch --auto-build` runs the project's **build stage** with the default
LLM after each action tick, mirroring the post-watch hook's UX.

- **Trigger**: same as the hook — action tokens only — and it runs AFTER
  the post-watch hook, so a hook that regenerates artifacts feeds the build
  that validates them.
- **Cost gate**: LLM-heavy, so it is flag-opt-in (same doctrine as
  `--enable-documentation`). Additionally, watch pre-checks
  `<root>/.omc/skills/build/SKILL.md` and skips the LLM call entirely when
  the project has no build stage (narrating `· no project build stage
  configured — skipping auto-build`). This existence check deliberately
  duplicates the build skill's own step 2 — a documented exception to the
  "skills are black boxes" rule, justified as a cost guard: without it every
  action tick would spend an LLM call to discover "nothing to do".
- **Invocation**: exactly the `slug.fetch_slug` pattern —
  `get_provider(cfg.llm.default).headless_argv(prompt, model=<configured>,
  allowed_tools=["Bash", "Read", "Glob", "Grep"])` via `ToolContext.run`,
  where `prompt` is the packaged `build` proxy skill's body
  (`skills_source.skill_prompt("build")`, a new helper that strips
  frontmatter; `slug.build_prompt` is refactored onto it). The build skill
  itself resolves and runs the PROJECT's `.omc/skills/build` stage and ends
  with the machine verdict line.
- **Verdict**: parse the LAST `OMC_STAGE {json}` line from combined
  stdout+stderr. Success = rc 0 AND verdict present AND `"passed": true`.
  Anything else — nonzero rc, missing/unparseable verdict, `passed: false`,
  timeout — is a failure.
- **Timeout**: module constant `_BUILD_TIMEOUT = 1800` (LLM builds are
  slower than shell hooks; still bounded so the loop never wedges).
- **Narration**: start `→ running project build stage via <provider>
  (LLM-heavy)`; success `✓ auto-build passed`; failure `✗ auto-build failed
  (<reason>) — log: <path>` where reason is `exit N` / `no verdict` /
  `timeout` / the verdict's summary when `passed` is false.
- **Logging**: full provider transcript written to a temp log on every run
  (`_write_hook_log` generalized to take the prefix; `omc-auto-build-`),
  path narrated only on failure — identical UX to the hook.
- **Failure semantics**: identical to the hook — warn, continue, `--once`
  still exits 0.
- **Testing**: unit tests stub the `claude` binary on PATH (same pattern as
  the recorded `node` stub) to emit pass/fail/no-verdict transcripts; the
  unconfigured pre-check test asserts the provider binary is NEVER invoked.
  E2E: token-free container test of the unconfigured-skip path plus a
  PATH-shim `claude` test driving the full real `omc watch --once
  --auto-build` flow.

## Docs

- README: `omc watch` prose (line ~68) and the command table row gain the
  hook contract (path, when it fires, log-on-failure).
- `skills/integrate/SKILL.md`: the surface inventory (step 2) and the
  per-surface sections gain `.omc/hooks/post-watch.sh` — integrate audits
  every omc surface, so a surface it doesn't know about would silently
  fall out of `/omc:integrate` reviews.
