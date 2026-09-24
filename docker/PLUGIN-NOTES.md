> **RESOLVED (twice)** — the "failed to load" issue documented below was first
> fixed by qualifying the dependency with its marketplace ("Resolution:
> marketplace-qualified dependency"), which only moved the failure to every
> machine whose superpowers came from a *different* marketplace. The current
> manifest declares **no dependency at all**; omc installs superpowers itself.
> See "Resolution 2: no manifest dependency" at the bottom. The "Decision" and
> "Not investigated further" sections below are the historical record of the
> original investigation and no longer reflect the current manifest or
> `docker/setup-plugins.sh` — `--plugin-dir /repo` is no longer needed.

# Plugin registration in the E2E image — active mechanism

`docker/setup-plugins.sh` runs at image build time (best-effort) and again at
container start (idempotent). Inside `omc-e2e:dev` it actually succeeds
end-to-end at build time — no network/auth deferral needed:

```
claude plugin marketplace add /repo               -> Successfully added marketplace: oh-my-clanker
claude plugin install omc@oh-my-clanker --scope user -> Successfully installed plugin: omc@oh-my-clanker (scope: user)
claude plugin marketplace add obra/superpowers-marketplace -> Successfully added marketplace: superpowers-marketplace
claude plugin install superpowers@superpowers-marketplace --scope user -> Successfully installed plugin: superpowers@superpowers-marketplace (scope: user)
cp /repo/.opencode/plugins/omc.js ~/.config/opencode/plugins/omc.js   (opencode has no marketplace)
codex plugin marketplace add /repo                 -> Added marketplace `oh-my-clanker` from /repo
codex plugin add omc@oh-my-clanker                 -> installs it; registration alone serves no skills
codex plugin marketplace add obra/superpowers-marketplace
codex plugin add superpowers@superpowers-marketplace
```

The three `codex plugin …` lines and the opencode copy were added when codex
registration landed (2026-09-08). Codex verbs are `add`/`remove` — never
`install`/`uninstall`.

## The failure: `omc@oh-my-clanker` installs but fails to load

Despite the successful `install` call above, `claude plugin list` inside the
built image shows:

```
❯ omc@oh-my-clanker
  Version: 0.1.0
  Scope: user
  Status: ✘ failed to load
  Error: Dependency "superpowers@oh-my-clanker" is not installed — run
  `claude plugin install superpowers@oh-my-clanker`, or check that its
  marketplace is added

❯ superpowers@superpowers-marketplace
  Version: 6.1.1
  Scope: user
  Status: ✔ enabled
```

**Root cause:** `.claude-plugin/plugin.json` declares `"dependencies":
["superpowers"]` (a bare name, by design — see `tests/unit/test_plugin_manifests.py::test_claude_plugin_manifest`,
which locks this in and is out of scope for this task to change). Claude
Code's plugin CLI resolves a bare dependency name against the *same
marketplace* as the dependent plugin, i.e. it looks specifically for
`superpowers@oh-my-clanker`. The `oh-my-clanker` marketplace
(`.claude-plugin/marketplace.json`) only lists `omc` — it has no
`superpowers` entry and never will (superpowers is a third-party
marketplace: `obra/superpowers-marketplace`). Cross-marketplace dependency
resolution is not supported by the installed-plugin path, so this dependency
can never be satisfied that way. Confirmed directly:

```
$ claude plugin install superpowers@oh-my-clanker --scope user
✘ Failed to install plugin "superpowers@oh-my-clanker": Plugin "superpowers"
  not found in marketplace "oh-my-clanker". Your local copy may be out of
  date — try `claude plugin marketplace update oh-my-clanker`.
```

Confirmed **order-independent**: uninstalling both plugins and reinstalling
`superpowers@superpowers-marketplace` before `omc@oh-my-clanker` reproduces
the identical "failed to load" status.

This is the scenario the Task 14 brief anticipated: the `install` command
itself runs non-interactively without error (no auth/network prompt), but
the resulting plugin state is unusable — `omc`'s skills/commands are not
served while it's in "failed to load" status.

## Active fallback: `claude --plugin-dir /repo`

Verified working in the built image. `--plugin-dir <path>` ("Load a plugin
from a directory ... for this session only") loads `/repo` directly and does
**not** go through marketplace-scoped dependency resolution:

```
$ claude plugin validate /repo
Validating marketplace manifest: /repo/.claude-plugin/marketplace.json
✔ Validation passed

$ claude --plugin-dir /repo -p "hello"
Not logged in · Please run /login
```

The session gets past plugin loading (no dependency error) and reaches the
expected "not logged in" stage — expected and correct, since this bare image
carries no credentials; auth arrives as env tokens at `docker run` time per
the Dockerfile's top comment. This confirms `--plugin-dir /repo` is a viable
load path unblocked by the dependency issue above.

**Decision: `--plugin-dir /repo` is the active mechanism for Task 16's
seeded-session E2E tests.** Launch the provider CLI with it instead of
relying on the marketplace-installed `omc@oh-my-clanker` plugin, e.g.:

```
claude --plugin-dir /repo -p "<seeded prompt>" ...
```

`docker/setup-plugins.sh` is left as written (per the Task 14 brief) — it
still registers the marketplaces and installs both plugins, which is
harmless, keeps `superpowers@superpowers-marketplace` genuinely enabled, and
gives a real signal in `claude plugin list` for debugging. It is simply not
the path Task 16 should depend on for `omc`'s own skills.

## Not investigated further (out of scope for Task 14)

A real fix likely exists at the manifest layer — e.g. adding a `superpowers`
entry to `.claude-plugin/marketplace.json` that points at the superpowers
source, or a marketplace-qualified dependency string in `plugin.json` if the
CLI's schema supports one. That touches Task 1-13 deliverables (and a
locked-in unit test), so it's flagged here rather than changed.

## OpenCode and Codex plugin paths

> **SUPERSEDED for codex (2026-09-08, codex-cli 0.153.4)** — the codex bullet
> below is the 0.144.5 record, when only registration was possible. Codex now
> installs: `docker/setup-plugins.sh` runs `codex plugin add omc@oh-my-clanker`
> **and** `codex plugin add superpowers@superpowers-marketplace`, and omc's own
> self-heal does the same on the user's machine. See "Codex plugin registration
> — verified 2026-09-08" below. The OpenCode bullet still holds.

Unaffected by the above — they don't go through Claude Code's
marketplace/dependency system:

- OpenCode: `docker/setup-plugins.sh` copies `.opencode/plugins/omc.js` to
  `~/.config/opencode/plugins/omc.js` directly (verified present and correct
  in the built image).
- Codex: `codex plugin marketplace add /repo` succeeds
  (`codex plugin list` shows `omc@oh-my-clanker` from marketplace
  `oh-my-clanker`, status `not installed` — registration only, per the task
  brief's interface: "Codex: repo marketplace registration". No install step
  was requested for Codex in `setup-plugins.sh`).

## Resolution: marketplace-qualified dependency

Follow-up experiment, run against `omc-e2e:dev` rebuilt from
`docker/Dockerfile.e2e` after each candidate edit to
`.claude-plugin/plugin.json` (each rebuild ~10s wall-clock — only the
`COPY . /repo` layer onward invalidates; apt/npm/wt layers stay cached).

**Candidate (a) — marketplace-qualified dependency string:**

```diff
- "dependencies": ["superpowers"]
+ "dependencies": ["superpowers@superpowers-marketplace"]
```

Rebuild:

```
$ docker build -f docker/Dockerfile.e2e -t omc-e2e:dev .
...
#12 [ 8/10] RUN bash /repo/docker/setup-plugins.sh || echo "plugin setup deferred to test time"
#12 0.647 Installing plugin "omc@oh-my-clanker"...✔ Successfully installed plugin: omc@oh-my-clanker (scope: user)
#12 2.958 Installing plugin "superpowers@superpowers-marketplace"...✔ Successfully installed plugin: superpowers@superpowers-marketplace (scope: user)
...
real  0m10.3s
```

In-container check, fresh container (`docker run --rm omc-e2e:dev bash -c "bash /repo/docker/setup-plugins.sh; claude plugin list"`):

```
Installed plugins:

  ❯ omc@oh-my-clanker
    Version: 0.1.0
    Scope: user
    Status: ✔ enabled

  ❯ superpowers@superpowers-marketplace
    Version: 6.1.1
    Scope: user
    Status: ✔ enabled
```

**No "failed to load."** This worked on the first try, so candidate (b) (an
object-form dependency, if the CLI schema supports one — would have been
checked via `claude plugin install --help` / `claude plugin --help`) was not
needed.

Reproducibility and independence from the build-time run were both checked
in two more fresh containers:

- `docker run --rm omc-e2e:dev bash -c "claude plugin list"` (**no** re-run of
  `setup-plugins.sh` — i.e. checking the state baked in at `docker build`
  time, not something the container-start re-run papers over): same
  `✔ enabled` result for both plugins.
- A second independent fresh container running the full
  `setup-plugins.sh; claude plugin list` sequence again: identical result,
  plus `claude plugin validate /repo` → `✔ Validation passed`.

**Conclusion:** `.claude-plugin/plugin.json`'s `dependencies` now reads
`["superpowers@superpowers-marketplace"]`. This is the kept, final manifest
shape — `tests/unit/test_plugin_manifests.py::test_claude_plugin_manifest`
was updated to assert it. `omc@oh-my-clanker` loads cleanly with no
dependency error, order-independent, reproducible across fresh containers,
and this is true from the image build itself (no container-start
`setup-plugins.sh` re-run required for the fix to take effect — that
re-run remains useful only for images that skip the build-time step, or to
add `superpowers` when it wasn't present at build time).

**`--plugin-dir /repo` fallback: now obsolete.** With `omc@oh-my-clanker`
loading normally through the standard marketplace-installed-plugin path,
there is no longer a reason for E2E (or any other) sessions to launch via
`claude --plugin-dir /repo` instead of relying on the installed plugin.

## omc update: per-provider plugin update verification (COPS-987)

> **SUPERSEDED (2026-09-08, codex-cli 0.153.4)** — this section's references
> to `Provider.plugin_update_argvs()` (lines below citing it, including the
> `test_plugin_update_argvs_are_pure_and_per_provider` assertions) describe a
> member that **no longer exists**. `plugin_update_argvs()` was deleted;
> `omc update` now routes every configured provider through a single
> `ensure_plugin(ctx, name, update=True)` call, backed by three pure
> `Provider` members — `plugin_probe_argvs()`, `parse_plugin_facts()`, and
> `plugin_repair_argvs(facts, *, source, update)` — that replace it with a
> strictly more expressive probe/plan/execute split. The codex subsection's
> framing below (end users are *told* to run
> `codex plugin marketplace add chris-husse/oh-my-clanker` by hand) is also
> now false in the same way: `codex plugin add` on the CLI, and `--json`
> output on both `plugin list` and `plugin marketplace list`, now exist, and
> omc runs the marketplace-add/plugin-add sequence itself. See "Codex plugin
> registration — verified 2026-09-08 (codex-cli 0.153.4)" at the bottom of
> this file for the current, verified contract. The empirical findings kept
> below — `marketplace upgrade` refreshing Git-sourced snapshots in place,
> and opencode having no scriptable cache-refresh command — are unaffected
> by this and remain accurate.

**Not verified (both providers): whether a running, authenticated agent
session picks up a refreshed snapshot/cache without a restart — live-session
proof stays token-gated and deferred, per the standing live-E2E follow-up.**

`Provider.plugin_update_argvs()` (Task 6) is what `omc update` runs per
provider after the `uv tool upgrade omc` step. This section records the
empirical checks behind each provider's implementation, run against
`omc-e2e:dev` (built from this branch's `docker/Dockerfile.e2e`).
CLI versions in the image: `codex-cli 0.144.5`, `opencode 1.18.3`,
`claude 2.1.212 (Claude Code)`.

### codex — `codex plugin marketplace upgrade` — CONFIRMED, no code change

The image's own `oh-my-clanker` marketplace is registered as a **local
path** (`docker/setup-plugins.sh` runs `codex plugin marketplace add
/repo`). Repro in a fresh container:

```
$ docker run --rm omc-e2e:dev bash -c "
    codex plugin marketplace add /repo
    codex plugin marketplace list
    codex plugin marketplace upgrade
    codex plugin marketplace list
  "
Marketplace `oh-my-clanker` is already added from /repo.
Installed marketplace root: /repo
MARKETPLACE    ROOT
oh-my-clanker  /repo
No configured Git marketplaces to upgrade.
MARKETPLACE    ROOT
oh-my-clanker  /repo
```

`codex plugin marketplace upgrade --help` explains why: *"Refresh configured
Git marketplace snapshots. Omit MARKETPLACE_NAME to upgrade all configured
Git marketplaces."* A marketplace added from a local filesystem path is not
a "Git marketplace" in codex's bookkeeping, so `upgrade` correctly reports
zero Git marketplaces and leaves it untouched — this is expected, not a
bug: the local-path registration only exists in this dev image for
convenience; end users are told (`_PLUGIN_HINTS` in
`src/omc/configure.py`) to run `codex plugin marketplace add
chris-husse/oh-my-clanker`, an `owner/repo` spec, which codex resolves and
tracks as a **Git** marketplace.

To verify `upgrade` actually refreshes a Git marketplace (`file://` is
rejected — `codex plugin marketplace add` only accepts `owner/repo[@ref]`,
an HTTPS/SSH Git URL, or a local path — so a `git+http://` dumb-HTTP mirror
was used to simulate a real remote without needing network access to a real
host):

```
# one container, one session:
git init --bare /tmp/mkt-origin.git
# ... commit "test v1" to .claude-plugin/marketplace.json, push, `git
# update-server-info`, serve /tmp via `python3 -m http.server 8080`

$ codex plugin marketplace add http://localhost:8080/mkt-origin.git
Added marketplace `fake-git-marketplace` from http://localhost:8080/mkt-origin.git.
Installed marketplace root: /root/.codex/.tmp/marketplaces/fake-git-marketplace

# bump origin to "test v2", commit, push, update-server-info again

$ codex plugin marketplace upgrade
Upgraded 1 marketplace(s).
Installed marketplace root: /root/.codex/.tmp/marketplaces/fake-git-marketplace

$ grep -rl "test v2" ~/.codex
/root/.codex/.tmp/marketplaces/fake-git-marketplace/.claude-plugin/marketplace.json
$ grep -rl "test v1" ~/.codex
# (no output — old content is gone)
```

**Conclusion:** `codex plugin marketplace upgrade` (Task 6's wiring) is
correct and needs no change — it refreshes Git-sourced marketplace
snapshots in place, confirmed by content diff before/after. The existing
code comment in `src/omc/providers/codex.py` ("Refreshes ALL configured git
marketplace snapshots") already matches this precisely.
`tests/unit/test_providers.py::test_plugin_update_argvs_are_pure_and_per_provider`'s
assertion (`codex == [["codex", "plugin", "marketplace", "upgrade"]]`) is
unchanged.

### opencode — no scriptable update command exists — `[]` confirmed correct

`opencode --help` has no plugin-cache-refresh subcommand. The closest
candidate, `opencode plugin <module> [-g] [--force]`, is described as
"install plugin and update config" — it manipulates `opencode.json`'s
`plugin` array, not the fetched package cache.

Repro (one container session, same dumb-HTTP local-mirror trick as above,
serving a throwaway npm-shaped package `fakeplugin` whose `index.js` prints
`FAKEPLUGIN_VERSION=v1`/`v2` at load time so the loaded content is directly
observable):

```
$ cat /tmp/proj2/opencode.json
{"plugin": ["fakeplugin@git+http://localhost:8080/plg-origin.git"]}

$ opencode debug config 2>&1 | grep FAKEPLUGIN     # origin at v1
FAKEPLUGIN_VERSION=v1

# bump origin to v2, commit, push, update-server-info

$ opencode debug config 2>&1 | grep FAKEPLUGIN     # re-run, no flags
FAKEPLUGIN_VERSION=v1                                # <- still v1, not refetched

$ opencode plugin "fakeplugin@git+http://localhost:8080/plg-origin.git" --force
◇  Plugin package ready
◇  Detected server target
◇  Plugin config updated
●  Added to /tmp/proj2/.opencode/opencode.json
◆  Installed fakeplugin@git+http://localhost:8080/plg-origin.git

$ opencode debug config 2>&1 | grep FAKEPLUGIN     # after --force
FAKEPLUGIN_VERSION=v1                                # <- still v1

$ rm -rf "/root/.cache/opencode/packages/fakeplugin@git+http:"
$ opencode debug config 2>&1 | grep FAKEPLUGIN     # after manual cache nuke
FAKEPLUGIN_VERSION=v2                                 # <- only this refetches
```

The git-ref plugin is fetched once into
`~/.cache/opencode/packages/<spec>/node_modules/<name>/` (spec-as-directory-name,
confirmed via `find` — e.g. `fakeplugin@git+http:/localhost:8080/...`)
and never revisited: neither an unflagged re-run nor `opencode plugin
<same-spec> --force` re-fetches it (`--force` only rewrites the
`opencode.json` plugin-array entry — "Plugin config updated" — not the
package cache). The only way observed to force a refresh is deleting that
package's cache directory directly, which is an unsupported reach into
opencode's internal cache layout (exact path shape is not documented and
could change across opencode releases) — not something `omc update` should
script.

**Conclusion:** `OpencodeProvider.plugin_update_argvs()` returning `[]`
(Task 6) is correct and unchanged. The in-app hint (`_PLUGIN_HINTS`) telling
users how to install the git-ref plugin stands as the extent of scripted
support; there is no verified command to force-refresh it, so `omc update`
correctly does nothing for opencode.
`tests/unit/test_providers.py`'s `get_provider("opencode").plugin_update_argvs()
== []` assertion is unchanged.

### Chain v2 E2E (`tests/e2e/test_e2e_chain.py`)

`test_chain_creates_and_migrates_in_container` drives two scenarios inside a
fresh container: (1) `omc configure --defaults` in a repo with no chain at
all creates the v2 symlinks, gitignore entries, and the project starter
file; (2) the same command in a repo carrying a v1 chain (relative symlinks
into a committed `.omc/internal/AGENTS.md`) migrates it to v2 in place while
preserving the pre-existing `.omc/config/AGENTS.md` content. Both checks run
with `set -e` inside each script block (the brief's original sketch left
that off for the assertion blocks, which would have let an early `test`
failure be masked by the exit code of the last line in the block — see
`docker/PLUGIN-NOTES.md`'s sibling report, `.superpowers/sdd/task-9-report.md`,
for the full note). Passing run: `1 passed in 17.60s`.


## Resolution 2: no manifest dependency (2026-09-02)

The marketplace-qualified dependency above fixed the Docker image, where
`setup-plugins.sh` installs superpowers from `obra/superpowers-marketplace`.
On a real machine superpowers is far more often installed from the official
marketplace (`claude-plugins-official`, pre-registered by Claude Code), and
there the same failure came straight back:

```
❯ omc@oh-my-clanker
  Version: 0.1.4
  Scope: user
  Status: ✘ failed to load
  Error: Dependency "superpowers@superpowers-marketplace" is not installed — run
  `claude plugin install superpowers@superpowers-marketplace`, or check that
  its marketplace is added

❯ superpowers@claude-plugins-official
  Version: 6.3.0
  Scope: user
  Status: ✔ enabled
```

Claude Code matches a declared dependency by its exact `name@marketplace`
id — a superpowers from any other marketplace does not count — and it never
installs the dependency for you. Worse, omc's own probe (`"omc@" in claude
plugin list`) read this state as "ok", so `omc start` launched a session
whose seeded `/omc:start` was "Unknown command".

Reproduced in an isolated `HOME` (claude 2.1.x): superpowers installed from
`anthropics/claude-plugins-official`, then omc from a local marketplace
checkout. With the current manifest, `claude plugin list --json` reports the
dependency error above for `omc@oh-my-clanker`; with `"dependencies"`
removed from `.claude-plugin/plugin.json`, the same sequence reports omc
with no `errors` key. Superpowers from `superpowers-marketplace` works
identically (the image still installs it from there).

**Kept shape:** `.claude-plugin/plugin.json` declares no `dependencies`
(locked in by `tests/unit/test_plugin_manifests.py::test_claude_plugin_manifest`).
`src/omc/plugin.py::ensure_plugin` — run by `omc start`, `omc update` and
`omc configure` — now probes `claude plugin list --json` (an `errors` array
per plugin is the contract), installs superpowers from the official
marketplace when no `superpowers@*` plugin is present, installs omc when
missing, and reinstalls it (after `claude plugin marketplace update`) when it
is present but failed to load. Every mutating path re-probes and raises with
Claude's own error text if the plugin still doesn't load.

## Codex plugin registration — verified 2026-09-08 (codex-cli 0.153.4)

**Supersedes the codex claims above** (the COPS-987 section's codex
subsection), which were recorded against codex-cli 0.144.5. The key change:
`codex plugin add` and `--json` output on both list commands now exist, so
`plugin_update_argvs`'s old comment ("codex has no scriptable
per-marketplace add") was false by the time it was read. `omc start`,
`omc configure`, and `omc update` now install, repair and refresh **the omc
plugin** for codex, and install **superpowers when it is absent** — no manual
step. The asymmetry is deliberate and holds for BOTH providers: the
superpowers block is gated on `facts.superpowers is None`, and the update
branch touches only the omc ref, so nothing repairs or refreshes superpowers.
Whatever superpowers a machine already has is left exactly as it is (any
marketplace satisfies the check). `plugin_update_argvs`
itself is gone; codex's plugin behaviour now lives in
`Provider.plugin_probe_argvs()` / `parse_plugin_facts()` /
`plugin_repair_argvs(facts, *, source, update)` on `CodexProvider`.

Established empirically this session, partly in an isolated `CODEX_HOME`.
Full design context: `docs/superpowers/specs/2026-09-08-fix-codex-integration-skill-registration-design.md`,
section "The codex contract".

**The two facts that cost the most to learn:**

- **No `errors` field anywhere in codex's plugin JSON.** Codex has no
  load-error channel at all — `installed`/`enabled` are the *entire* health
  signal. Claude's `_problems()` (reading a per-plugin `errors` array) has
  no codex analogue, and claude's "failed to load → uninstall + reinstall"
  repair collapses, for codex, to a plain re-add — there is nothing to
  distinguish "installed but broken" from "not installed" beyond those two
  booleans.
- **`codex plugin marketplace add` refuses a same-named marketplace from a
  different source with exit 1** — `marketplace 'oh-my-clanker' is already
  added from a different source; remove it before adding this source` — AND
  **codex normalises `owner/repo` to `https://github.com/<owner>/<repo>.git`**
  before storing it as the registered source. Naively comparing the
  registered source (already normalised) against omc's own computed source
  (still `owner/repo`) produces a **permanent false conflict** for the
  default GitHub-installed user: every `omc start` would remove the
  marketplace, re-add it, and reinstall the plugin, reporting "repaired" —
  forever. That normalisation is invisible in the CLI's own output shape
  (`marketplace list --json`'s `source` field just looks like "the source",
  not "the source after silent rewriting") and was the single most expensive
  bug found in this work. The fix compares *canonicalised* sources and
  treats an unprovable difference as **not** a conflict — a false positive
  churns the user's config forever and silently; a false negative produces
  one loud, actionable `marketplace add` exit-1 with a `fix manually:` line.
  Loud beats silent churn.

| # | Fact | Consequence |
|---|---|---|
| C1 | `codex plugin list --json` → `{"installed":[…],"available":[…]}`. Entries: `pluginId` (= `name@marketplaceName`), `name`, `marketplaceName`, `version`, `installed`, `enabled`, `source`, `marketplaceSource`, `installPolicy`, `authPolicy` | A real machine-readable probe exists; no table scraping |
| C2 | **No `errors` field** | Codex has no load-error channel. `installed`/`enabled` are the whole health signal; claude's `_problems()` has no analogue and the "failed to load → uninstall + reinstall" repair collapses to a plain re-add |
| C3 | The JSON **omits** path/git marketplaces until the plugin is installed. `available` was `[]` throughout, even with dozens of uninstalled remote plugins | The probe confirms "installed & enabled" but cannot distinguish "registered, not installed" from "nothing registered". Acceptable: the repair is idempotent, so it simply re-runs both commands |
| C4 | `codex plugin marketplace list --json` → `{"marketplaces":[{name, root, marketplaceSource:{sourceType, source}}]}` | The registered source *is* readable — required for C7 |
| C5 | Codex reads **`.claude-plugin/marketplace.json`** (it prints that path as the marketplace root), for omc's checkout and obra's marketplace alike. No `.codex-plugin/marketplace.json` exists or is needed | No new marketplace manifest to author |
| C6 | `codex plugin marketplace add` accepts a local path **and** `owner/repo`. Re-adding the *same* source → exit 0, "already added" | Idempotent; safe to run unconditionally |
| C7 | Re-adding a same-named marketplace from a **different** source → **exit 1**: `marketplace 'oh-my-clanker' is already added from a different source; remove it before adding this source` | A live bug generator: `marketplace_source(env)` varies with how omc was installed, so moving from a local checkout to a GitHub install hard-fails. Must be handled explicitly |
| C8 | `codex plugin add omc@oh-my-clanker` → exit 0; repeating it → exit 0, same message | Idempotent; safe to run unconditionally |
| C9 | Verbs are `add` / `remove`, **not** `install` / `uninstall` | Diverges from claude; belongs in the codex adapter |
| C10 | `marketplace remove` succeeds even with the plugin installed, and the plugin then disappears from `installed` — no zombie state | Remove-then-re-add is a safe repair |
| C11 | Failing commands exit 1 with the message on **stderr** | Existing `ctx.run` + returncode handling works unchanged |
| C12 | Install root is version-pinned: `~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/`, a full repo copy | See the "Version pinning and `omc update`" discussion in the design spec |
| C13 | `CODEX_HOME` fully isolates config and cache | Clean sandbox for E2E and future probing |

**The repair, in outline** (see `CodexProvider.plugin_repair_argvs` for the
exact per-condition table): a detected source conflict (C7) is repaired by a
non-fatal `marketplace remove` followed by a fatal `marketplace add` of the
correct source; because removing a marketplace also removes its installed
plugins (C10), the omc plugin re-add step is **unconditional** whenever a
conflict was present, even though `facts` (captured before the repair ran)
still showed omc as installed — otherwise a conflict repair would silently
leave codex with no omc plugin at all.

**superpowers for codex** comes from `obra/superpowers-marketplace`, not
codex's own curated catalog: `superpowers@openai-curated-remote` (6.3.0) is
in the official curated marketplace, but installing it returns exit 1,
`remote plugin plugins~Plugin_… is disabled by admin` — org policy on a
managed machine, so it is not usable as the primary route. `obra/superpowers-marketplace`
works end to end (`marketplace add` → exit 0, `plugin add
superpowers@superpowers-marketplace` → exit 0, `installed: true`, `enabled:
true`, 14 skills present). Superpowers from *any* marketplace satisfies the
check, so a user who already has it from the curated catalog is left alone.
This is a deliberate per-provider asymmetry: claude installs
`superpowers@claude-plugins-official`; codex installs
`superpowers@superpowers-marketplace`.

**Still open, unverified for both claude and codex:** whether a running,
already-authenticated session picks up a freshly installed/repaired plugin
without a restart. This has never been tested live and is not claimed either
way — it stays a tracked follow-up, not a fact.
