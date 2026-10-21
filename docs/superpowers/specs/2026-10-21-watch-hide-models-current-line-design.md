# Watch: print "models current" only when it is news

Date: 2026-10-21 · Branch: `feature/watch-hide-models-current-line` · Status: design record

Amends one sentence of `2026-10-15-reconcile-stale-configured-models-design.md`
§4 — "Narration, one line per provider checked: `✓ claude models current
(8 listed)`" — for `omc watch` only: watch prints that line on its first tick
and on the first successful check after a failed one, and is quiet on every
other tick. Everything else in that record stands, Decision 4 included: watch
still reconciles at the start of every tick.

## 1. Problem and goal

While every configured model stays valid, a long-running `omc watch` (seen
on `omc watch --rebase --auto-build`, omc 0.1.35) prints
`✓ claude models current (6 listed)` on every 30 s poll. Over an hour that is
120 identical lines; the lines that matter — a sync, a rebase, a remap, a
failed hook — scroll off behind them. The user's words: "You do not need to
print the models are current if they are current".

Watch already has the rule for this. `_tick` narrates a repeatable quiet
outcome only when it *changed* since the last tick (`quiet(token, msg)` with
the `last` token: `· up to date — waiting for changes on origin/main` prints
once, not every 30 s), while action outcomes always narrate. The reconcile
line is the one tick-time line that escaped that rule, because `reconcile`
narrates for itself and `run_watch` cannot tell it what the previous tick saw.

Goal: in `omc watch`, `✓ <provider> models current (N listed)` prints on the
first tick (the command's entry, where it confirms the models the run will use)
and on the first successful check after a tick whose check failed (the
recovery is news, and it ends the `✗ model reconciliation failed` state);
otherwise the tick is silent about current models. Nothing that needs
attention is hidden.

Non-goals: `omc design`/`start`, `implement`, `review` and `omc dependency
watch` are unchanged — they reconcile once at entry and print the line once;
the E2E `tests/e2e/test_e2e_reconcile.py` asserts it on the second
`omc design --dry-run` and keeps passing. Watch still reconciles on every
tick (Decision 4 of the 2026-10-15 record): the *check* stays, only its
all-clear line is rate-limited.

## 2. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | Watch prints the `✓ … models current` line on the **first tick** and on the **first successful check after a failed one**; quiet otherwise | Recommended, user accepted. The first tick is the command's entry and confirms the run's models; the recovery line closes the `✗ model reconciliation failed` state that the previous tick opened — the same "say it when it changes" rule `_tick` applies to `· up to date` |
| 2 | Scope: **`omc watch` only**; `design`/`implement`/`review`/`dependency watch` keep their one line at entry | Recommended, user accepted. A command that reconciles once has no repetition to suppress, and the E2E asserts that line |
| 3 | A **keyword-only flag on `reconcile`** (`report_current`), not string-filtering `reconcile`'s `say` stream inside watch | Design call, accepted with the design. A filter keyed on the line's wording breaks silently the day the wording changes; a flag is a contract the type checker and the unit tests see |

Assumptions accepted with the design: a tick that remaps after the first
tick prints the `→ <leaf>: … config updated` lines and no `✓` line for the
providers that were current — the remap lines say what happened, and the
all-clear for the other provider is not news (builder's call, recorded here);
`omc watch --once` has exactly one tick, which is the first, so it prints the
line as today.

Assumption added at implementation (audit amendment, 2026-10-21): the
all-clear is emitted only once the whole reconcile transaction has succeeded —
on the no-stale return, or after the remap lines on the remap return — for
every caller. Before, a mixed-provider run printed the current provider's
`✓` line ahead of the other provider's remap and then raised if that remap
failed. In watch that would print a "recovery" `✓` on a tick whose check
fails, which contradicts Decision 1's "first *successful* check". For the
entry callers it changes only that ordering and drops the `✓` line from a
run that raises anyway.

## 3. Mechanism

`src/omc/reconcile.py` — `reconcile(ctx, cfg, leaves, *, say, dry_run,
report_current: bool = True) -> Config`. The new keyword guards one statement:
the `say(f"✓ {provider} models current ({len(ids)} listed)")` loop over
`current_providers` (today at line 278). Every other line `reconcile` emits
is unconditional and stays so: `→ refreshing <provider> model list with the
default model` and the probe's `✓`/`✗` line, `→ <leaf>: <value> is
unavailable; <provider> (default model) picked <id>; config updated`, and
the dry-run `· <leaf>: <value> is unavailable (would remap)` report. The
default `True` keeps the four entry callers in `cli/__init__.py` (design,
implement, review, dependency watch) printing the line once at entry; none of
them passes the keyword. The one change they see is the §2 implementation
assumption: the `✓` line follows any remap lines and is absent when the
reconcile raises.

`src/omc/watch.py` — `run_watch` gains one boolean beside `first_tick`:
`last_reconcile_failed`, initially `False`. The per-tick call becomes

```python
try:
    cfg = reconcile(
        ctx, cfg, model_groups, say=_say, dry_run=False,
        report_current=first_tick or last_reconcile_failed,
    )
    last_reconcile_failed = False
except OmcError as exc:
    if first_tick:
        raise
    _say(f"✗ model reconciliation failed: {exc}")
    last_reconcile_failed = True
```

The first-tick fail-loud path and the later-tick warn-and-skip path (§6 of
the 2026-10-15 record) are untouched; the flag only records which of the two
successful-check situations the next tick is in. The state lives in
`run_watch`, not in `reconcile`, for the same reason `_tick`'s `last` token
lives in the loop: `reconcile` is a one-shot function shared with four
single-shot callers, and only the loop knows there was a previous tick.

Tick-by-tick, with models current throughout: tick 1 prints `✓`, ticks 2…n
print nothing from reconcile. With a failure on tick k: tick k prints
`✗ model reconciliation failed: …`, tick k+1 prints `✓ … models current`,
tick k+2 is quiet again. A failure on *every* tick prints `✗` every tick, as
today — a failure is never a repeatable-quiet outcome.

**Rejected alternative — filter the line in watch.** Wrap `_say` for the
reconcile call in a closure that drops lines starting with `✓ ` and ending in
` listed)` unless first tick / after failure. No signature change, but it
couples watch to the exact wording of a line another module owns, fails open
(prints again) on a harmless rewording and fails closed (hides a *different*
`✓` line) on an unlucky one, and leaves no seam a unit test can assert on
other than the string itself. Decision 3.

## 4. Record amendment

`docs/superpowers/specs/2026-10-15-reconcile-stale-configured-models-design.md`
§4, the sentence "Narration, one line per provider checked: `✓ claude models
current (8 listed)`", gets a one-sentence pointer: *in `omc watch` this line
prints only on the first tick and on the first successful check after a
failed one — see `2026-10-21-watch-hide-models-current-line-design.md`.* Its
Decision 4 ("watch reconciles at the start of every tick") is not amended;
it remains true. The edit is an implementation step of this change, committed
with the code.

## 5. Testing

Red to green per the project's testing policy; the reconcile tests reuse the
existing fixtures (`_cli_stub`, `_codex_catalog`, `_context`, `_config`), the
watch tests reuse `_fake_watch_boot`.

`tests/unit/test_reconcile.py` (three new tests plus the existing two
assertions; what each one proves is stated because the `✓` line's own guard
makes one obvious shape vacuous):

- **suppression** — a current config (the shape of
  `test_old_blank_session_config_is_current_without_turn_or_write`) with
  `report_current=False` → `lines == []`, still no headless call and no
  write. This is the proof that the flag hides the line: with the default
  the same shape yields exactly `["✓ codex models current (2 listed)"]`.
- **nothing else hidden** — a stale config (the shape of
  `test_codex_stale_model_remaps_full_id_and_only_one_yaml_leaf`) with
  `report_current=False` → the `→ refreshing …`, the probe's `✓ OK` and the
  `→ <leaf>: … config updated` lines are all still emitted. This test says
  nothing about the `✓ … models current` line: `current_providers` is
  "selected minus stale" (reconcile.py:270), so with one provider and a
  stale leaf it is empty and the line is absent whatever the flag says. Its
  job is to catch a guard placed too widely.
- **remap tick, two providers** — the `omc watch --enable-documentation
  --auto-build` shape: `llm.default = codex` with a stale `model`,
  `llm.docs.provider = "claude"` with a current `docs_model` (both catalogs
  and both `_cli_stub` scripts in the one bindir), groups
  `{"orchestrator", "docs"}`, `report_current=False` → one `→ model: …
  config updated` line, no line containing `models current`; with the default
  the same run also prints `✓ claude models current (3 listed)`. This pins
  the assumption accepted in §2 and is the first reconcile-level
  two-provider test (the 2026-10-15 record's audit item 10 noted its absence).
- **failed mixed remap** (added at implementation) —
  `test_failed_mixed_provider_remap_does_not_report_current`: one provider
  current, the other's remap turn fails → `reconcile` raises and no line
  contains `models current`, under the default flag. Pins the §2
  implementation assumption.
- the existing assertions at lines 396 and 559 (`lines == ["✓ … models
  current (N listed)"]` with the default) stay as they are and keep passing —
  they are the proof that the default is `True`.

Assert the line's absence as `"models current" not in line`, never as "no
`✓` line": `docsllm._step` prints `✓ OK` for the successful refresh probe on
exactly the stale path.

`tests/unit/test_watch.py`:

- a four-tick run over outcomes good → failed (`OmcError`) → good → good with
  a stub `reconcile` recording `report_current` → `[True, False, True,
  False]`; the ✗ line is printed once, as
  `test_watch_later_tick_reconcile_failure_warns_and_keeps_previous_cfg`
  already asserts for its three ticks;
- the existing stub `reconcile` signatures in that file (`_current_models`
  autouse fixture line 35, `fake_reconcile` line 91, `failing_reconcile`
  line 180, `flaky_reconcile` line 205) accept the new keyword — otherwise
  `run_watch` raises `TypeError` on the first tick, which is the red the
  first new watch test fails with.

E2E: none added. `tests/e2e/test_e2e_reconcile.py` line 162 asserts
`✓ <provider> models current` on `omc design --dry-run`, which is unchanged;
it keeps passing. A watch E2E would need a multi-tick container run to
observe one missing line — a transcript assertion the testing policy tells us
not to write.

## 6. Size

One task, `Complexity: simple`: one keyword and one `if` in `reconcile.py`,
one boolean and two assignments in `watch.py`, one sentence in the 2026-10-15
record, three reconcile tests and one watch test plus four stub signatures.
Left to the builder: the flag's final name if `report_current` reads badly
at the call site; test names; whether the four-tick watch test extends
`test_watch_later_tick_reconcile_failure_warns_and_keeps_previous_cfg` or
stands beside it; and whether to keep `last_reconcile_failed` beside
`first_tick` as §3 shows it or collapse the two into one `report_current`
variable (start `True`, `False` after a success, `True` after a later-tick
failure) — both give the identical `[True, False, True, False]` sequence,
and `first_tick` is still needed for raise-versus-warn.

## 7. Out of scope

- Rate-limiting any other reconcile line (`→ refreshing`, `→ … config
  updated`, `✗ model reconciliation failed`): each reports an action or a
  failure and always prints.
- Changing how often watch reconciles (Decision 4 of the 2026-10-15 record).
- Any change to `omc design`/`implement`/`review`/`dependency watch`
  narration beyond the §2 implementation assumption (all-clear after the
  remap lines, absent on a raising reconcile), or to `omc internal models`.
- A general "quiet" verbosity switch for watch; `_tick`'s per-outcome
  `last` token and this flag are the two cases that exist, and neither needs
  a third mechanism.

## Implementation review

**2026-10-21 — auditor: claude (Opus 5.5), `/omc:audit`**

1. Important · code deviates from the record → record amended · §3 ("byte-for-byte"), §7 · `src/omc/reconcile.py:273-316`, `tests/unit/test_reconcile.py:599`. The all-clear moved into `say_current_models()`, which is emitted after a successful remap and never ahead of a failed one, so the entry callers' ordering and failure-path narration changed. Disposition: kept. It is what Decision 1's "successful check" requires and contradicts no row of §2. Recorded as a §2 implementation assumption, with §3, §5 and §7 amended to match.
2. Minor · record is stale → noted · §6 · `tests/unit/test_configure_pty.py:102-111`, `:207-238`. Gate repair: `MenuTerminal.wait_input_ready` drains PTY output while it polls for cbreak, which fixes the known `test_configure_pty` flake where a child blocked on a full PTY buffer never left canonical mode. Comes with the reproducing test `test_wait_input_ready_drains_output_before_child_enters_cbreak`. Test-only, no product change.

## Deliberate complexity

None.
