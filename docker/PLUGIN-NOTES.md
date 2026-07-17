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
