# Grug lens not triggered: honest plugin updates and a fix-first lens

**Date:** 2026-10-01
**Slug:** `grug-lens-not-triggered`
**Status:** design, hardened with `/omc:explain`

## 1. Problem

The grug complexity lens (`skills/grug/SKILL.md`, PR #43, stamped 0.1.13)
never fires during `/omc:spec` hardening or the `/omc:review` stage. Every
session still loads the 0.1.11 plugin snapshot, which has no `grug` skill.
Review therefore reports a passing stage with no lens applied, and spec
hardening silently skips its second call.

### Evidence (host, Claude Code 2.1.286, 2026-10-01)

| Fact | Where |
| --- | --- |
| Installed omc plugin is 0.1.11, enabled, no load errors, last updated 2026-09-28 | `claude plugin list --json`, `~/.claude/plugins/installed_plugins.json` |
| Marketplace clone is at 0.1.13 (commit 0466956), refreshed 2026-10-01 11:57Z | `claude plugin marketplace list --json`, clone `git log` |
| Cache directories for 0.1.12 and 0.1.13 exist; 0.1.13 contains `skills/grug`; nothing points at them | `~/.claude/plugins/cache/oh-my-clanker/omc/` |
| `omc update` ran twice on 2026-10-01 at 08:57 local and exited 0 | fish history |
| `claude plugin list --available --json` lists 318 plugins; the intersection with the 8 installed plugins is empty | read-only probe during investigation |

## 2. Root cause

Two defects, one direct and one latent.

**Direct.** `ensure_plugin` (`src/omc/plugin.py`) runs the update path
through `_prepare_marketplace`, which ends with `_available(ctx)` on the live
Claude config. `_available` requires `omc@oh-my-clanker` to appear in the
`available` array of `claude plugin list --available --json`. On the real
CLI that array lists only plugins that are **not installed**. So on every
machine where omc is already installed, the update path raises
"refusing plugin replacement: marketplace does not offer omc@oh-my-clanker"
before `claude plugin update` ever runs. `run_update`
(`src/omc/installer.py`) catches the error as a best-effort warning
("✗ claude: … — continuing") and exits 0. The guard entered with commit
945fef9 (authored 2026-09-25, merged 2026-09-28, marketplace-source repair).
The last successful plugin update ran with the older in-process CLI and
predates it.

**Latent.** `ensure_plugin` judges health as "enabled and no load errors"
(`_problems`). It never compares the installed version against the version
the registered marketplace offers. `omc start` therefore reports "ok" on a
stale plugin, and an update whose `plugin update` step did nothing would
still be reported as "updated". The 2026-07-23 memory on the version-pin
trap described the same shape with a different trigger.

**Why tests passed.** `make_claude_stub` (`tests/unit/_stubs.py`) returns
omc in `available` regardless of installed state, so
`test_update_refreshes_a_healthy_plugin` validates against a model of Claude
that differs from the real one. The Docker E2Es in
`tests/e2e/test_e2e_marketplace_repair.py` cover marketplace replacement,
where the plugin is uninstalled before the final check. No E2E exercises the
plain "installed, healthy, run `omc update`" path against a real Claude.

## 3. Goals and non-goals

Goals:

1. `omc update` actually advances the Claude plugin and never reports
   "updated" when the installed version did not move.
2. `omc start` notices a stale plugin and heals it before launching, so the
   session it opens carries the current skills.
3. The review and spec skills fail loud when the grug skill cannot be
   resolved, instead of passing with no lens.
4. Important grug findings are fixed first by the top-tier model; only what
   cannot be fixed reaches the user, batched.
5. Unit tests model the real `--available` semantics and plugin versions;
   one Docker E2E covers the healthy-update path.

Non-goals:

- Editing Claude's registry or settings files (`installed_plugins.json`,
  `known_marketplaces.json`, `settings.json`). omc drives the `claude plugin`
  CLI only. Existing convention, unchanged.
- Making plugin sync a fatal step of `omc update`. It stays best-effort and
  never blocks the CLI upgrade. It must simply tell the truth.
- Changing the grug skill's rules, severities, or output contract. The lens
  itself is a leaf and stays one.
- Codex plugin handling. Codex has no scriptable probe today and keeps its
  "unverified" status.

## 4. Design

### 4.1 Prove the marketplace offers omc without the `available` list

`_available` stays valid only inside the isolated probe
(`CLAUDE_CONFIG_DIR` scratch) in `_prepare_marketplace`, where omc is not
installed and therefore does appear in `available`. The final live-config
call is replaced by a check that holds whether or not omc is installed:
read the registered marketplace's clone (the `installLocation` that
`claude plugin marketplace list --json` reports), open
`.claude-plugin/marketplace.json`, and confirm it lists a plugin named `omc`.
That same read yields the offered version, which section 4.2 needs, so one
helper serves both purposes.

`installLocation` is present for both source kinds: for a GitHub source it
is Claude's clone under `~/.claude/plugins/marketplaces/<name>`, for a
directory source it is the directory itself, which already carries the
manifest (the E2E fixture `_old_marketplace` copies `.claude-plugin` there).
`_prepare_marketplace` has two `_available` calls; only the live-config one
changes. The probe-config call keeps `_available`, because inside the
scratch config omc is not installed and the list is a valid proof.

When the clone or manifest is unreadable, the check fails with a message
naming the path. That is the honest answer: omc cannot prove the marketplace
offers it, so it does not proceed to remove or replace anything.

The quirk is documented at the code site with the verified Claude version
(2.1.286), per the provider-quirk convention: `available` excludes installed
plugins, so it proves offering only for a plugin that is not installed.

### 4.2 Detect version skew in `ensure_plugin`

A new health dimension alongside load errors and disabled state: the
installed version (`version` field of `claude plugin list --json`) differs
from the offered version (section 4.1's manifest read). Skew is
"loadable but stale". It does not trigger the uninstall-and-reinstall
repair path, because the plugin loads; it triggers the update sequence
(`claude plugin marketplace update oh-my-clanker`, then
`claude plugin update omc@oh-my-clanker`).

Skew is computed in `ensure_plugin` only and is **not** folded into
`_problems`. That helper is also the probe's load check inside
`_prepare_marketplace`, where a fresh install has no skew concept, and
folding skew in would route a stale plugin through the destructive
reinstall path.

"Offered" means what the locally registered marketplace snapshot offers at
the time of the read. `omc update` refreshes that snapshot first
(`marketplace update`), so its comparison is against the remote head.
`omc start --dry-run` reads the snapshot as it is, and says so in its
status when the two differ.

Status strings grow one case. Today's values are `ok`, `installed`,
`repaired`, `updated`, `missing (…)`, `failed to load: …`. The new ones are
`stale (0.1.11 → 0.1.13 offered; omc start will update it)` under
`check_only` (mirroring the sibling `missing (omc start will install it)`), and
`updated (0.1.11 → 0.1.13)` after a successful heal. The arrow form is the
progress-line vocabulary the start UX already uses.

A missing version on either side (older Claude output without `version`, or
a manifest without one) is treated as no skew, with a code comment saying
why: omc never fails over its own plumbing. Comparison is plain string
inequality, not semver ordering. The installed snapshot always comes from
the registered marketplace, so "different" means "stale" in practice, and
after a heal "equal" is the only honest success criterion.

### 4.3 Honest update verdicts

After any mutating action, `ensure_plugin` already re-probes load health.
It now also re-probes the version. If the action was an update (or a heal
of skew) and the installed version still differs from the offered version,
the function raises `OmcError` with both versions and the manual command
(`claude plugin update omc@oh-my-clanker`), so `run_update` prints
"✗ claude: …" with the real reason instead of "✓ … updated".

`run_update` keeps its best-effort contract: the error is printed and the
loop continues to the next provider; the CLI upgrade result is unaffected.
What changes is only that the printed line is true.

### 4.4 `omc start` heals skew before launching

`run_start` (`src/omc/start.py`) already calls `ensure_plugin` before the
seeded session and repairs a broken plugin. With skew now a health signal,
the same call heals a stale plugin by running the update sequence. The
progress line reads `→ omc plugin for claude: updated (0.1.11 → 0.1.13)`.
Claude's "restart required to apply" is satisfied because the session omc
launches is new.

`--dry-run` (`check_only`) reports `stale (… → … offered)` and does nothing,
matching how it reports "missing" and "failed to load" today.

If the heal leaves the version unchanged, `ensure_plugin` raises, and start
stops the same way it stops for a plugin that still fails to load after
reinstall. One rule, everywhere: after any mutating action the plugin is in
the promised state or omc says so and stops. The error carries both
versions and the manual command. The alternative, warning and launching on
the stale plugin, was considered and rejected: a start that proceeds would
seed `/omc:start` into skills that prescribe verbs the plugin half lacks,
the exact skew the install-state memory records, and the path is rare once
the guard is fixed.

### 4.5 Review and spec fail loud without grug

`skills/review/SKILL.md` step 3 and `skills/spec/SKILL.md` step 2 get one
rule each: when the internal `grug` skill cannot be invoked (unknown skill,
not listed, or its usage line comes back for a well-formed payload), the
stage does not proceed as if the lens ran.

- Review ends with
  `OMC_STAGE {"stage": "review", "configured": …, "passed": false, "summary": "grug skill unavailable — plugin stale? run omc update"}`.
- Spec hardening stops and reports the same sentence instead of continuing
  with explain-only hardening.

`tests/unit/test_plugin_manifests.py` contracts
(`test_review_proxy_runs_grug`, the spec contract test) assert the
sentence is present in both skills.

### 4.6 Fix first, by the top tier, before asking the user

Both callers of grug currently offer the user questions at the point a
finding cannot be folded or waived from the record. The new rule puts an
explicit fix attempt in front of every question, and makes the model doing
it the top tier:

- **Under spec.** For each Important finding, the top-tier model first
  rewrites the section with the simpler alternative when that keeps the
  converged design. Only a finding the rewrite rejects (it would change a
  decision in "Decisions taken during brainstorm", or the alternative loses
  value the section depends on) proceeds to waive-by-record or to the user.
- **Under review.** For each Important finding, the top-tier model first
  applies the behavior-preserving fix and runs `/omc:check`. Only a finding
  whose fix fails check, or whose fix would change behavior, proceeds to
  waive. Nothing reaches the user from review unless no honest waive reason
  exists.
- Questions that survive are **batched** into one numbered list at the end
  of the hardening or review pass, never one dialog per finding. This
  matches the presentation rule the plan phase already passes to the
  brainstorm.
- "Top tier" is the behavior layer's model-tier policy (`AGENTS.md`, Model
  selection): where the harness can pick a model per subagent, dispatch the
  fix as a top-tier subagent; otherwise the session model does it. Never a
  cheaper tier.

The grug skill's Step 5 text ("the caller decides which path applies") is
unchanged. Its disposition vocabulary (`fixed`, `waived`, `UNDISPOSITIONED`)
already covers the outcome.

### 4.7 Tests

**Unit stub realism.** `make_claude_stub` models what the real CLI does:
`--available` returns only plugins not present in the installed state, each
installed entry carries a `version`, and `plugin update X` advances X to the
version the stub's marketplace manifest offers. Marketplace entries the stub
creates get an `installLocation` under a temp root (today it is the fake
`/cache/<name>`), and the stub writes a real
`.claude-plugin/marketplace.json` there with the offered version, so
section 4.1's read works against it. One knob sets the offered version;
`available_omc` goes away because the real semantics replace it.

**Unit tests** (`tests/unit/test_plugin.py`, `tests/unit/test_start.py`):

- installed and healthy, update requested: update sequence runs, version
  advances, status `updated (a → b)`.
- installed and healthy, update requested, `plugin update` does not move
  the version: `OmcError` names both versions and the manual command.
- skew with no update requested (start path): heal runs, status reports the
  transition; `check_only` reports `stale (…)` and performs no mutation.
- no skew: status `ok`, no update commands issued (regression guard for the
  guard itself).
- `--available` excludes installed plugins and the live-config check still
  passes (the bug this spec fixes, as a test).
- missing version fields: "version unknown", no skew, no error.

**E2E** (`tests/e2e/test_e2e_marketplace_repair.py`, Docker-per-test, no
model calls): reuse the `_old_marketplace` shape. Copy the plugin payload to
a directory marketplace with its manifests rewritten to an older version,
install omc from it, then rewrite the manifests back to the checkout's
version to stand in for "the marketplace advanced". Run `ensure_plugin`
with `update=True` through the existing `_repair` boundary and assert that
`claude plugin list --json` now reports the newer version and the status
reads `updated (old → new)`. Offline and deterministic. The dry-run and
no-skew paths are unit-tested; they get no Docker case of their own.

Pinned Claude in `docker/Dockerfile.e2e` (`CLAUDE_VERSION=2.1.281`) moves to
2.1.286, the version on which the `available` semantics were verified, and
the four existing marketplace E2Es re-run on it. The `PLUGIN-NOTES.md`
entry records the version.

### 4.8 Documentation

- `docker/PLUGIN-NOTES.md`: a dated entry with the `available`-list
  semantics, the 2026-10-01 timeline, and the E2E that now covers the
  healthy-update path.
- `README.md`: the update and start wording stays truthful ("start heals a
  missing, broken, or stale plugin"); no new user-facing commands.
- The memory on the version-pin trap is superseded by this record; the
  operator-facing fact is simply "run `omc update`, read the ✓/✗ line".

## 5. Decisions taken during brainstorm

| # | Decision | Reason |
| --- | --- | --- |
| 1 | Fix the guard **and** add skew detection, not the guard alone | The guard fix restores updates; skew detection is what makes "ok" and "updated" honest and prevents the next silent regression. |
| 2 | `omc start` heals skew rather than only reporting it | Start already self-heals missing and broken plugins; the launched session is new, so the restart requirement is met. |
| 3 | Review and spec fail loud when grug is unresolvable | A review that passes without the lens is the symptom that hid this bug for three days. |
| 4 | Unit stub corrected and an E2E added for the healthy-update path | The passing unit test modelled a Claude that does not exist; only a real-CLI E2E can catch the next semantic change. |
| 5 | Important grug findings are auto-fixed by the top tier before any user question; surviving questions are batched | User requirement during implement: the best model fixes first, the user decides only what the model could not. |
| 6 | Proof that the marketplace offers omc comes from the clone's manifest, not from treating "installed" as sufficient | The manifest read is needed anyway for the offered version, so one helper serves both; "installed" alone would not catch a marketplace that dropped the plugin. |
| 7 | A heal that leaves the version unchanged raises, in start as in update | One rule after any mutating action; matches the existing "still broken after reinstall" behaviour; the warn-and-launch alternative seeds a session on skills the plugin lacks. Taken during hardening. |
| 8 | One Docker E2E (healthy update advances the version); dry-run and no-skew are unit-only; version compare is string inequality | grug `80-20` during hardening: each Docker case costs minutes, and the unit stub now models versions faithfully. |

## 6. Risks and open points

- **Claude CLI drift.** The `available` semantics were verified on 2.1.286
  only. If an older or newer CLI includes installed plugins in `available`,
  nothing breaks: the manifest read does not depend on that list.
- **Manifest shape.** `marketplace.json` has one versioned `omc` entry today
  and `stamp_version.py` already requires exactly one; the read follows the
  same assumption and fails loud on anything else.
- **Marketplace update as a side effect.** Claude's `marketplace update`
  materialized the 0.1.13 cache without switching the registry. The design
  does not rely on that behaviour either way; it reads the clone manifest
  for the offered version and the plugin list for the installed one.
- **Immediate remediation** is outside this change: the user runs
  `claude plugin update omc@oh-my-clanker` on the host and opens a new
  session. The session that designed this never mutates host plugin state.

## 7. Hardening notes

The grug skill could not be invoked in the session that wrote this spec:
the session runs the 0.1.11 plugin, which is the defect under repair. The
spec-side rules were applied by hand from the checkout's
`skills/grug/SKILL.md`. `/omc:explain` ran per section against the fresh
knowledge graph.

- **4.1** explain: `_available` is called twice in `_prepare_marketplace`;
  only the live-config call is wrong. `installLocation` exists for both
  source kinds. Spec now says so.
- **4.2** explain: `_problems` is shared with the probe's load check, so
  skew must stay out of it or a stale plugin would take the destructive
  reinstall path. Spec now says so. grug `80-20`: the "version unknown"
  status string was dropped; a missing version is simply no skew, with a
  comment.
- **4.3 and 4.4** explain: a failed heal in start was underspecified
  (raise or warn). Decision 7 settles it as raise, matching the existing
  still-broken-after-reinstall rule. `configure` also calls `ensure_plugin`
  and therefore heals skew too, consistent with the README's "configure,
  update and start all install (and repair)".
- **4.5** explain: the skills a session executes are the installed
  plugin's, not the checkout's. The branch that ships this change is
  reviewed by the 0.1.11 review skill, so the new fail-loud rule cannot
  block its own merge. After the plugin advances, a stale plugin fails
  `finish` at the review stage by design.
- **4.6** explain: the model-tier policy already places review and judging
  on the top tier; the fix attempt is an extension of that, and Codex
  cannot switch per-subagent models, so the "session model, never cheaper"
  fallback is required text.
- **4.7** grug `80-20`: the dry-run Docker case was dropped (decision 8).
  grug `test-level`: the project's own policy distrusts stubs and uses
  Docker-per-test for real-CLI behaviour, which the one E2E honours.
  explain: the stub's fake `/cache/<name>` install location has to become a
  real temp path for the manifest read to be testable.
- **Whole spec**: new surface is one helper (manifest read and offered
  version), one health signal, two status strings, one rule in each of two
  skills, stub realism, and tests. No new dependency, no new layer.

## Deliberate complexity

None.
