> **RESOLVED** — the "failed to load" issue documented below was fixed by
> qualifying the dependency with its marketplace. See "Resolution: marketplace-qualified
> dependency" at the bottom of this file. The "Decision" and "Not investigated
> further" sections below are preserved as the historical record of the
> original investigation but no longer reflect the current manifest or the
> current `docker/setup-plugins.sh` behavior — `--plugin-dir /repo` is no
> longer needed.

# Plugin registration in the E2E image — active mechanism

`docker/setup-plugins.sh` runs at image build time (best-effort) and again at
container start (idempotent). Inside `omc-e2e:dev` it actually succeeds
end-to-end at build time — no network/auth deferral needed:

```
claude plugin marketplace add /repo               -> Successfully added marketplace: oh-my-clanker
claude plugin install omc@oh-my-clanker --scope user -> Successfully installed plugin: omc@oh-my-clanker (scope: user)
claude plugin marketplace add obra/superpowers-marketplace -> Successfully added marketplace: superpowers-marketplace
claude plugin install superpowers@superpowers-marketplace --scope user -> Successfully installed plugin: superpowers@superpowers-marketplace (scope: user)
codex plugin marketplace add /repo                 -> Added marketplace `oh-my-clanker` from /repo
```

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
