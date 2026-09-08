# Codex Plugin Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make codex reach parity with claude for plugin registration — `omc start`/`configure`/`update` install, repair and refresh the omc plugin and superpowers for codex — by moving plugin capability onto the `Provider` ABC and deleting the two provider-name branches.

**Architecture:** Providers gain three pure members (`plugin_probe_argvs`, `parse_plugin_facts`, `plugin_repair_argvs`) that describe *what to run* and *what output means*; `src/omc/plugin.py:ensure_plugin` executes the plan and owns all I/O. New members are **concrete defaults** (not `@abstractmethod`) because `providers/registry.py` builds instances at module import — an abstract member breaks import until every provider implements it. Per-step metadata (`action`, `manual_fix`) carries the status string and remediation text, matching `src/omc/probe.py`'s convention of bundling argv with its label and hint.

**Tech Stack:** Python 3.12+, pytest, `dataclasses(frozen=True)`, `uv`, `just`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-08-fix-codex-integration-skill-registration-design.md`

## Global Constraints

- **Purity invariant** (`src/omc/providers/base.py` class docstring): "argv builders are pure — no I/O, no subprocess calls, no filesystem writes." All three new members obey this. `tests/unit/test_providers.py::test_notification_setup_defaults_and_purity` is the existing enforcement pattern.
- **Regression guard:** all 15 existing tests in `tests/unit/test_plugin.py` must pass **unmodified** through the entire refactor. Never edit that file's existing tests; only append.
- **Display contract** (`src/omc/configure.py:143`): `mark = "·" if status.startswith("unverified") else "✓"`. The `"unverified"` prefix must survive for opencode.
- **Exact status strings** returned by `ensure_plugin`: `"ok"`, `"installed"`, `"repaired"`, `"updated"`, `"installed superpowers"`, `"unverified (no scriptable check for this provider yet)"`, and check-only forms `"missing (omc start will install it)"`, `"failed to load: {problem} (omc start will reinstall it)"`, `"ok; superpowers missing (omc start will install it)"`.
- **Exact error wording** pinned by tests: `"fix manually: "` prefix (not probe.py's `"fix: "`), `"still fails to load"`, and the `` `claude plugin list --json` `` command echo in parse failures.
- **Three caller failure policies must not change:** `start.py:79` raises (blocks the session); `configure.py:142` prints `✗` and continues; `installer.py:86` prints `✗ … — continuing`.
- **Codex verbs** are `add`/`remove`, never `install`/`uninstall`.
- **Marketplace name** is `oh-my-clanker`; omc plugin ref is `omc@oh-my-clanker`; codex superpowers ref is `superpowers@superpowers-marketplace` from source `obra/superpowers-marketplace`.
- **Provider quirks are documented as comments at the exact code site that depends on them** (`.omc/skills/explain-context/SKILL.md`).
- Run the full unit suite with `uv run pytest tests/unit -q` at every checkpoint.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/omc/providers/base.py` | `PluginEntry`/`PluginFacts`/`RepairStep` value objects + the three concrete-default members | 1 |
| `src/omc/providers/claude.py` | Claude's probe argv, JSON parse, repair plan (relocated behaviour) | 2 |
| `src/omc/plugin.py` | Provider-agnostic `ensure_plugin`: execute, narrate, re-probe, raise | 3 |
| `src/omc/providers/codex.py` | Codex's two probe argvs, dual-JSON parse, repair plan | 4 |
| `tests/unit/_stubs.py` | `make_codex_stub` — stateful fake codex CLI | 4 |
| `src/omc/installer.py` | `run_update` collapses to one `ensure_plugin` call per provider | 5 |
| `src/omc/providers/opencode.py` | Loses its `plugin_update_argvs` stub; inherits defaults | 5 |
| `.codex-plugin/plugin.json` | `hooks` + `longDescription` | 6 |
| `docker/setup-plugins.sh` | Codex install steps for the E2E image | 6 |
| `tests/e2e/test_e2e_smoke.py` | Assert codex-side registration | 6 |
| `docker/PLUGIN-NOTES.md`, `README.md`, `src/omc/configure.py`, `.superpowers/sdd/progress.md` | Docs truth | 7 |

**Sequencing rationale:** Tasks 1→3 are a behaviour-preserving refactor gated on the 15 unmodified tests. Codex only becomes real in Task 4. `plugin_update_argvs` survives until Task 5 so the tree stays green at every checkpoint (it is still referenced by `installer.py` and 4 tests until then).

---

### Task 1: Provider plugin seam — value objects and ABC members

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/providers/base.py`
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `PluginEntry(id: str, enabled: bool, problems: tuple[str, ...])`; `PluginFacts(omc: PluginEntry | None, superpowers: PluginEntry | None, marketplace_source: str | None)`; `RepairStep(argv: list[str], label: str, fatal: bool, action: str = "", manual_fix: str = "")`; `Provider.plugin_probe_argvs() -> list[list[str]]`; `Provider.parse_plugin_facts(stdouts: list[str]) -> PluginFacts`; `Provider.plugin_repair_argvs(facts: PluginFacts, *, source: str, update: bool) -> list[RepairStep]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_providers.py`:

```python
def test_plugin_seam_defaults_are_inert():
    # New optional capability members are CONCRETE defaults, not abstract:
    # registry.py builds provider instances at module import, so an abstract
    # member would break import until every provider implements it.
    from omc.providers.base import PluginFacts

    for name in provider_names():
        p = get_provider(name)
        assert p.plugin_probe_argvs() == [] or isinstance(p.plugin_probe_argvs(), list)
    # opencode carries no plugin capability at all and rides the defaults
    op = get_provider("opencode")
    assert op.plugin_probe_argvs() == []
    facts = op.parse_plugin_facts([])
    assert isinstance(facts, PluginFacts)
    assert facts.omc is None and facts.superpowers is None and facts.marketplace_source is None
    assert op.plugin_repair_argvs(facts, source="x", update=False) == []


def test_repair_step_defaults():
    from omc.providers.base import RepairStep

    s = RepairStep(argv=["a", "b"], label="doing it", fatal=True)
    assert s.action == "" and s.manual_fix == ""
    with pytest.raises(Exception):  # frozen
        s.argv = []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_providers.py::test_plugin_seam_defaults_are_inert -v`
Expected: FAIL with `ImportError: cannot import name 'PluginFacts'` (or `AttributeError: 'OpencodeProvider' object has no attribute 'plugin_probe_argvs'`).

- [ ] **Step 3: Write minimal implementation**

In `src/omc/providers/base.py`, add after the imports (`from dataclasses import dataclass` must be added to the import block):

```python
@dataclass(frozen=True)
class PluginEntry:
    """One installed plugin, as the harness reports it."""

    id: str                        # "omc@oh-my-clanker"
    enabled: bool
    problems: tuple[str, ...]      # why it won't serve skills; includes the
                                   # disabled reason so callers check one field


@dataclass(frozen=True)
class PluginFacts:
    """What one probe round learned about a harness's plugin state."""

    omc: PluginEntry | None = None
    superpowers: PluginEntry | None = None
    marketplace_source: str | None = None   # registered source; None = unknown


@dataclass(frozen=True)
class RepairStep:
    """One command in a repair plan, bundled with its narration, its failure
    policy, the status it contributes, and its manual-fix hint.

    Per-step metadata follows probe.py's convention (spec tuples of
    (name, argv, hint) -> ProbeResult carries the hint alongside the argv):
    the provider owns the provider-specific text, the caller renders it.
    """

    argv: list[str]
    label: str                 # narrated before the step runs
    fatal: bool                # False = best-effort self-heal, failure tolerated
    action: str = ""           # contributes to ensure_plugin's status; last non-empty wins
    manual_fix: str = ""       # rendered as "fix manually: <text>" on fatal failure
```

Then add three concrete methods to `Provider` (keep `plugin_update_argvs` for now — Task 5 removes it):

```python
    def plugin_probe_argvs(self) -> list[list[str]]:
        """Commands whose stdout carries this harness's plugin state, in the
        order ``parse_plugin_facts`` expects them.

        ``[]`` means no scriptable probe exists for this provider — callers
        report the plugin as unverified and never mutate anything. Pure.
        """
        return []

    def parse_plugin_facts(self, stdouts: list[str]) -> PluginFacts:
        """Turn probe stdout (one entry per ``plugin_probe_argvs()`` command,
        same order) into facts. Pure — no I/O, and it must not raise on
        well-formed-but-empty output."""
        return PluginFacts()

    def plugin_repair_argvs(
        self, facts: PluginFacts, *, source: str, update: bool
    ) -> list[RepairStep]:
        """Ordered plan that makes ``facts`` healthy. ``[]`` = nothing to do.
        Pure: state in, commands out — the caller executes them."""
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_providers.py -v`
Expected: PASS, including all pre-existing tests (nothing was removed yet).

Run: `uv run pytest tests/unit -q`
Expected: PASS — the whole suite is still green.

- [ ] **Step 5: Commit**

```bash
git add src/omc/providers/base.py tests/unit/test_providers.py
git commit -m "feat(providers): add plugin probe/parse/repair seam to the Provider ABC"
```

---

### Task 2: Claude's probe, parse and repair plan

**Model:** heavy coding tier

Behaviour-preserving relocation. This task moves knowledge out of `plugin.py` into the claude adapter but does **not** yet rewire `plugin.py` — so the tree stays green and this task is reviewable on its own.

**Files:**
- Modify: `src/omc/providers/claude.py`
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Consumes: `PluginEntry`, `PluginFacts`, `RepairStep` from Task 1.
- Produces: `ClaudeProvider.plugin_probe_argvs()`, `.parse_plugin_facts()`, `.plugin_repair_argvs()`. Module constants `PLUGIN_REF = "omc@oh-my-clanker"`, `MARKETPLACE_NAME = "oh-my-clanker"`, `SUPERPOWERS_REF = "superpowers@claude-plugins-official"`, `OFFICIAL_MARKETPLACE = "anthropics/claude-plugins-official"` live in `src/omc/providers/claude.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_providers.py`:

```python
_CLAUDE_LIST_HEALTHY = json.dumps(
    [
        {"id": "omc@oh-my-clanker", "enabled": True},
        {"id": "superpowers@claude-plugins-official", "enabled": True},
    ]
)
_CLAUDE_LIST_BROKEN = json.dumps(
    [
        {"id": "omc@oh-my-clanker", "enabled": True, "errors": ["dep missing"]},
        {"id": "superpowers@superpowers-marketplace", "enabled": True},
    ]
)


def test_claude_plugin_probe_and_parse():
    p = get_provider("claude")
    assert p.plugin_probe_argvs() == [["claude", "plugin", "list", "--json"]]

    facts = p.parse_plugin_facts([_CLAUDE_LIST_HEALTHY])
    assert facts.omc is not None and facts.omc.problems == ()
    assert facts.superpowers is not None
    assert facts.marketplace_source is None  # claude exposes no source probe

    broken = p.parse_plugin_facts([_CLAUDE_LIST_BROKEN])
    assert broken.omc.problems == ("dep missing",)
    # superpowers from ANY marketplace satisfies the check
    assert broken.superpowers.id == "superpowers@superpowers-marketplace"


def test_claude_parse_disabled_becomes_a_problem():
    p = get_provider("claude")
    out = json.dumps([{"id": "omc@oh-my-clanker", "enabled": False}])
    facts = p.parse_plugin_facts([out])
    assert facts.omc.enabled is False
    assert "disabled" in facts.omc.problems[0]


def test_claude_repair_plan_installs_missing_omc():
    from omc.providers.base import PluginEntry, PluginFacts

    p = get_provider("claude")
    facts = PluginFacts(
        omc=None,
        superpowers=PluginEntry("superpowers@claude-plugins-official", True, ()),
    )
    steps = p.plugin_repair_argvs(facts, source="chris-husse/oh-my-clanker", update=False)
    argvs = [s.argv for s in steps]
    add_at = argvs.index(
        ["claude", "plugin", "marketplace", "add", "chris-husse/oh-my-clanker"]
    )
    install_at = argvs.index(
        ["claude", "plugin", "install", "omc@oh-my-clanker", "--scope", "user"]
    )
    assert add_at < install_at
    assert steps[install_at].fatal is True
    assert steps[install_at].action == "installed"
    assert "fix manually" not in steps[install_at].manual_fix  # the caller adds that prefix
    assert "claude plugin marketplace add" in steps[install_at].manual_fix


def test_claude_repair_plan_repairs_broken_omc_in_order():
    from omc.providers.base import PluginEntry, PluginFacts

    p = get_provider("claude")
    facts = PluginFacts(
        omc=PluginEntry("omc@oh-my-clanker", True, ("dep missing",)),
        superpowers=PluginEntry("superpowers@claude-plugins-official", True, ()),
    )
    argvs = [
        s.argv
        for s in p.plugin_repair_argvs(facts, source="chris-husse/oh-my-clanker", update=False)
    ]
    update_at = argvs.index(["claude", "plugin", "marketplace", "update", "oh-my-clanker"])
    uninstall_at = argvs.index(["claude", "plugin", "uninstall", "omc@oh-my-clanker"])
    install_at = argvs.index(
        ["claude", "plugin", "install", "omc@oh-my-clanker", "--scope", "user"]
    )
    assert update_at < uninstall_at < install_at


def test_claude_repair_plan_superpowers_and_update_and_noop():
    from omc.providers.base import PluginEntry, PluginFacts

    p = get_provider("claude")
    omc_ok = PluginEntry("omc@oh-my-clanker", True, ())
    sp_ok = PluginEntry("superpowers@claude-plugins-official", True, ())

    # superpowers missing -> official marketplace add + install, action set
    only_omc = p.plugin_repair_argvs(PluginFacts(omc=omc_ok), source="s", update=False)
    argvs = [s.argv for s in only_omc]
    assert ["claude", "plugin", "marketplace", "add", "anthropics/claude-plugins-official"] in argvs
    assert [
        "claude", "plugin", "install", "superpowers@claude-plugins-official", "--scope", "user",
    ] in argvs  # fmt: skip
    assert [s.action for s in only_omc if s.action] == ["installed superpowers"]

    # healthy + update -> marketplace update + plugin update, action "updated"
    upd = p.plugin_repair_argvs(
        PluginFacts(omc=omc_ok, superpowers=sp_ok), source="s", update=True
    )
    argvs = [s.argv for s in upd]
    assert ["claude", "plugin", "update", "omc@oh-my-clanker"] in argvs
    assert [s.action for s in upd if s.action] == ["updated"]

    # healthy, no update -> empty plan
    assert (
        p.plugin_repair_argvs(
            PluginFacts(omc=omc_ok, superpowers=sp_ok), source="s", update=False
        )
        == []
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_providers.py -k claude_plugin -v`
Expected: FAIL — `plugin_probe_argvs()` returns `[]` from the base default, so the first assertion fails.

- [ ] **Step 3: Write minimal implementation**

In `src/omc/providers/claude.py`, add the module constants and the three methods. `parse_plugin_facts` reproduces today's `plugin.py:_list_plugins` JSON contract, `_find` (match `name@` prefix, any marketplace) and `_problems` (errors + the disabled reason):

```python
PLUGIN_REF = "omc@oh-my-clanker"
MARKETPLACE_NAME = "oh-my-clanker"
SUPERPOWERS_REF = "superpowers@claude-plugins-official"
OFFICIAL_MARKETPLACE = "anthropics/claude-plugins-official"


def _entry(entries: list[dict], name: str) -> PluginEntry | None:
    """The installed plugin named ``name`` from ANY marketplace (ids are
    ``name@marketplace``). Cross-marketplace matching is deliberate: a
    manifest pinned to one marketplace made Claude refuse to load omc when
    superpowers came from another — see docker/PLUGIN-NOTES.md "Resolution 2"."""
    raw = next((e for e in entries if str(e.get("id", "")).startswith(name + "@")), None)
    if raw is None:
        return None
    problems = [str(e) for e in (raw.get("errors") or [])]
    enabled = raw.get("enabled") is not False
    if not enabled:
        problems.append("the plugin is disabled")
    return PluginEntry(id=str(raw.get("id", "")), enabled=enabled, problems=tuple(problems))
```

Methods on `ClaudeProvider`:

```python
    def plugin_probe_argvs(self):
        # `claude plugin list --json`: one entry per installed plugin with
        # `id`, `enabled` and — for a plugin Claude refused to load — an
        # `errors` list. The human listing carries the same facts as prose;
        # the JSON is the contract (verified 2026-09-02, claude 2.1.x).
        return [["claude", "plugin", "list", "--json"]]

    def parse_plugin_facts(self, stdouts):
        data = json.loads(stdouts[0] or "")
        if not isinstance(data, list):
            raise ValueError("expected a JSON array")
        entries = [e for e in data if isinstance(e, dict)]
        # marketplace_source stays None: claude exposes no source probe, and
        # re-adding an existing marketplace is benign.
        return PluginFacts(
            omc=_entry(entries, "omc"), superpowers=_entry(entries, "superpowers")
        )

    def plugin_repair_argvs(self, facts, *, source, update):
        omc_fix = (
            f"claude plugin marketplace add {source} && "
            f"claude plugin install {PLUGIN_REF}"
        )
        steps: list[RepairStep] = []
        if facts.superpowers is None:
            steps += [
                RepairStep(
                    ["claude", "plugin", "marketplace", "add", OFFICIAL_MARKETPLACE],
                    "installing superpowers (omc's start skill hands off to it)…",
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "install", SUPERPOWERS_REF, "--scope", "user"],
                    "",
                    fatal=True,
                    action="installed superpowers",
                    manual_fix=(
                        f"claude plugin marketplace add {OFFICIAL_MARKETPLACE} && "
                        f"claude plugin install {SUPERPOWERS_REF}"
                    ),
                ),
            ]
        if facts.omc is None:
            steps += [
                RepairStep(
                    ["claude", "plugin", "marketplace", "add", source],
                    f"installing the omc plugin from {source}…",
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "install", PLUGIN_REF, "--scope", "user"],
                    "",
                    fatal=True,
                    action="installed",
                    manual_fix=omc_fix,
                ),
            ]
        elif facts.omc.problems:
            steps += [
                RepairStep(
                    ["claude", "plugin", "marketplace", "add", source],
                    f"the omc plugin is installed but failed to load "
                    f"({facts.omc.problems[0]}) — reinstalling from {source}…",
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "marketplace", "update", MARKETPLACE_NAME],
                    "",
                    fatal=False,
                ),
                RepairStep(["claude", "plugin", "uninstall", PLUGIN_REF], "", fatal=False),
                RepairStep(
                    ["claude", "plugin", "install", PLUGIN_REF, "--scope", "user"],
                    "",
                    fatal=True,
                    action="repaired",
                    manual_fix=omc_fix,
                ),
            ]
        elif update:
            # Claude's docs note a restart is required to apply — running
            # sessions keep the old plugin.
            steps += [
                RepairStep(
                    ["claude", "plugin", "marketplace", "add", source], "", fatal=False
                ),
                RepairStep(
                    ["claude", "plugin", "marketplace", "update", MARKETPLACE_NAME],
                    "",
                    fatal=False,
                ),
                RepairStep(
                    ["claude", "plugin", "update", PLUGIN_REF],
                    "",
                    fatal=True,
                    action="updated",
                    manual_fix=omc_fix,
                ),
            ]
        return steps
```

Add `import json` and the base-class value-object imports at the top of `claude.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_providers.py -v`
Expected: PASS.

Run: `uv run pytest tests/unit -q`
Expected: PASS — `plugin.py` is untouched, so the 15 `test_plugin.py` tests still exercise the old path.

- [ ] **Step 5: Commit**

```bash
git add src/omc/providers/claude.py tests/unit/test_providers.py
git commit -m "feat(claude): implement the plugin probe/parse/repair seam"
```

---

### Task 3: Make `ensure_plugin` provider-agnostic

**Model:** heavy coding tier

The pivot. `plugin.py` stops knowing about claude and starts executing provider plans. **The 15 existing `test_plugin.py` tests are the acceptance criteria — do not modify them.**

**Files:**
- Modify: `src/omc/plugin.py`
- Test: `tests/unit/test_plugin.py` (existing tests only — no edits)

**Interfaces:**
- Consumes: `plugin_probe_argvs`/`parse_plugin_facts`/`plugin_repair_argvs` from Tasks 1–2.
- Produces: `ensure_plugin(ctx, provider, *, check_only=False, update=False) -> str` — signature unchanged. `marketplace_source(env) -> str` unchanged and still exported (`installer.py` imports it).

- [ ] **Step 1: Run the existing tests to establish the green baseline**

Run: `uv run pytest tests/unit/test_plugin.py -v`
Expected: PASS, 15 tests. Record the list — every one must still pass at Step 4.

- [ ] **Step 2: Rewrite `ensure_plugin`**

Replace the body of `src/omc/plugin.py` from `_list_plugins` through the end of `ensure_plugin` with the following. Delete `_list_plugins`, `_find`, `_problems`, `_install`, `PLUGIN_REF`, `MARKETPLACE_NAME`, `SUPERPOWERS_REF`, `_OFFICIAL_MARKETPLACE` (now owned by `providers/claude.py`); keep `marketplace_source`, `_FALLBACK_SOURCE` and `_say`:

```python
def _probe(ctx: ToolContext, provider, argvs: list[list[str]]):
    """Run the probe commands and parse them into facts. Any failure is an
    OmcError carrying the harness's own text — a probe we can't read is never
    silently treated as "healthy"."""
    stdouts = []
    for argv in argvs:
        try:
            cp = ctx.run(argv)
        except OSError as exc:
            raise OmcError(f"could not run `{' '.join(argv)}`: {exc}") from exc
        if cp.returncode != 0:
            detail = (cp.stderr or cp.stdout or "").strip()
            raise OmcError(f"`{' '.join(argv)}` failed (exit {cp.returncode}): {detail}")
        stdouts.append(cp.stdout or "")
    try:
        return provider.parse_plugin_facts(stdouts)
    except (ValueError, KeyError, TypeError) as exc:
        joined = " ".join(argvs[0])
        raise OmcError(
            f"could not parse the output of `{joined}`: {exc}\n"
            f"  output was: {stdouts[0].strip()[:200]!r}"
        ) from exc


def _describe(facts) -> str:
    """The --dry-run status: what `omc start` WOULD do, without doing it."""
    if facts.omc is None:
        return "missing (omc start will install it)"
    if facts.omc.problems:
        return f"failed to load: {facts.omc.problems[0]} (omc start will reinstall it)"
    if facts.superpowers is None:
        return "ok; superpowers missing (omc start will install it)"
    return "ok"


def ensure_plugin(
    ctx: ToolContext, provider: str, *, check_only: bool = False, update: bool = False
) -> str:
    """Ensure the omc plugin (and superpowers) is installed and healthy for
    ``provider``.

    Missing → install. Installed but failed to load / disabled → refresh the
    marketplace snapshot and reinstall. Healthy and ``update`` → run the
    provider's update sequence. Every mutating path re-probes afterwards and
    raises ``OmcError`` (carrying the harness's own error text) if the plugin
    still doesn't load — a broken plugin must never be reported as "ok".

    Returns a short status string for the progress/plan output. Providers with
    no scriptable probe (``plugin_probe_argvs() == []``) are left alone.
    ``check_only`` (the --dry-run path) never installs anything.
    """
    p = get_provider(provider)
    argvs = p.plugin_probe_argvs()
    if not argvs:
        return "unverified (no scriptable check for this provider yet)"

    facts = _probe(ctx, p, argvs)
    if check_only:
        return _describe(facts)

    source = marketplace_source(ctx.env)
    plan = p.plugin_repair_argvs(facts, source=source, update=update)
    if not plan:
        return "ok"

    for step in plan:
        if step.label:
            _say(step.label)
        cp = ctx.run(step.argv)
        if cp.returncode != 0 and step.fatal:
            detail = (cp.stderr or cp.stdout or "").strip()
            raise OmcError(
                f"could not run `{' '.join(step.argv)}` automatically.\n"
                f"  {detail}\n"
                f"  fix manually: {step.manual_fix}"
            )

    facts = _probe(ctx, p, argvs)
    if facts.omc is None:
        raise OmcError(
            "the omc plugin is still missing after an apparently successful install — "
            "check the harness's plugin list and the plugin's Status line"
        )
    if facts.omc.problems:
        joined = "\n  ".join(facts.omc.problems)
        raise OmcError(
            "the omc plugin still fails to load after reinstalling it:\n"
            f"  {joined}\n"
            "  check the harness's plugin list; if the error names a missing dependency, "
            "your marketplace snapshot may predate the fix — retry after `omc update`"
        )
    actions = [s.action for s in plan if s.action]
    return actions[-1] if actions else "ok"
```

Update the imports at the top of `plugin.py`: drop `json` and `re` if now unused (`re` is still needed by `marketplace_source`; `json` is not), keep `get_provider`.

- [ ] **Step 3: Run the regression suite**

Run: `uv run pytest tests/unit/test_plugin.py -v`
Expected: PASS, the same 15 tests, unmodified.

Two failures are likely and both are real bugs in the new code, not the tests:
- `test_install_failure_carries_manual_commands` expects `"fix manually: claude plugin marketplace add"` — the failing step's `manual_fix` must start with `claude plugin marketplace add`, so verify Task 2 set it on the *install* step.
- `test_unparseable_plugin_list_is_an_error` expects the message to contain `plugin list --json` — `_probe`'s parse-failure branch must echo `argvs[0]`, and `json.JSONDecodeError` must be caught (it subclasses `ValueError`, so the existing `except (ValueError, ...)` covers it).

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest tests/unit -q`
Expected: PASS. `installer.py` still calls `plugin_update_argvs` for non-claude providers, which still exists.

- [ ] **Step 5: Commit**

```bash
git add src/omc/plugin.py
git commit -m "refactor(plugin): drive ensure_plugin through the provider seam

Deletes the `provider != \"claude\"` branch; claude's probe/parse/repair
knowledge now lives in its adapter. All 15 test_plugin.py tests pass
unmodified."
```

---

### Task 4: Codex probe, parse, repair plan — and a stateful codex stub

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/providers/codex.py`
- Modify: `tests/unit/_stubs.py`
- Test: `tests/unit/test_providers.py`, `tests/unit/test_plugin.py` (append only)

**Interfaces:**
- Consumes: the seam from Tasks 1–3.
- Produces: `CodexProvider.plugin_probe_argvs()` → `[["codex","plugin","marketplace","list","--json"], ["codex","plugin","list","--json"]]`; `.parse_plugin_facts()`; `.plugin_repair_argvs()`. Module constants `CODEX_PLUGIN_REF = "omc@oh-my-clanker"`, `CODEX_MARKETPLACE_NAME = "oh-my-clanker"`, `SUPERPOWERS_SOURCE = "obra/superpowers-marketplace"`, `CODEX_SUPERPOWERS_REF = "superpowers@superpowers-marketplace"`. New test helper `make_codex_stub(bindir, *, plugins=None, marketplaces=None, add_rc=0) -> Path` returning the calls-log path.

- [ ] **Step 1: Write the failing provider tests**

Append to `tests/unit/test_providers.py`. The JSON fixtures below are the **real shapes captured from codex-cli 0.153.4**:

```python
# Captured from codex-cli 0.153.4 (2026-09-08). Note: NO `errors` field exists
# — `installed`/`enabled` are the entire health signal.
_CODEX_MKTS = json.dumps(
    {
        "marketplaces": [
            {
                "name": "oh-my-clanker",
                "root": "/checkout/omc",
                "marketplaceSource": {"sourceType": "local", "source": "/checkout/omc"},
            }
        ]
    }
)
_CODEX_MKTS_EMPTY = json.dumps({"marketplaces": []})
_CODEX_INSTALLED = json.dumps(
    {
        "installed": [
            {
                "pluginId": "omc@oh-my-clanker",
                "name": "omc",
                "marketplaceName": "oh-my-clanker",
                "version": "0.1.7",
                "installed": True,
                "enabled": True,
            }
        ],
        "available": [],
    }
)
_CODEX_NONE = json.dumps({"installed": [], "available": []})


def test_codex_plugin_probe_argvs():
    p = get_provider("codex")
    assert p.plugin_probe_argvs() == [
        ["codex", "plugin", "marketplace", "list", "--json"],
        ["codex", "plugin", "list", "--json"],
    ]


def test_codex_parse_reads_source_and_installed():
    p = get_provider("codex")
    facts = p.parse_plugin_facts([_CODEX_MKTS, _CODEX_INSTALLED])
    assert facts.marketplace_source == "/checkout/omc"
    assert facts.omc is not None and facts.omc.problems == ()
    assert facts.superpowers is None

    # Nothing registered at all: the JSON omits path/git marketplaces until
    # the plugin is installed, so "absent" is the only readable state.
    empty = p.parse_plugin_facts([_CODEX_MKTS_EMPTY, _CODEX_NONE])
    assert empty.marketplace_source is None and empty.omc is None


def test_codex_parse_disabled_becomes_a_problem():
    p = get_provider("codex")
    out = json.dumps(
        {
            "installed": [
                {"pluginId": "omc@oh-my-clanker", "name": "omc", "enabled": False,
                 "installed": True}
            ],
            "available": [],
        }
    )  # fmt: skip
    facts = p.parse_plugin_facts([_CODEX_MKTS, out])
    assert facts.omc.enabled is False
    assert "disabled" in facts.omc.problems[0]


def test_codex_repair_fresh_install_order():
    from omc.providers.base import PluginFacts

    p = get_provider("codex")
    steps = p.plugin_repair_argvs(PluginFacts(), source="chris-husse/oh-my-clanker", update=False)
    argvs = [s.argv for s in steps]
    add_at = argvs.index(
        ["codex", "plugin", "marketplace", "add", "chris-husse/oh-my-clanker"]
    )
    omc_at = argvs.index(["codex", "plugin", "add", "omc@oh-my-clanker"])
    sp_add_at = argvs.index(
        ["codex", "plugin", "marketplace", "add", "obra/superpowers-marketplace"]
    )
    sp_at = argvs.index(["codex", "plugin", "add", "superpowers@superpowers-marketplace"])
    assert add_at < omc_at
    assert sp_add_at < sp_at
    # codex verbs are add/remove, never install/uninstall
    assert not any("install" in a or "uninstall" in a for a in argvs)


def test_codex_repair_conflicting_source_removes_then_readds():
    from omc.providers.base import PluginEntry, PluginFacts

    p = get_provider("codex")
    # Registered from a DIFFERENT source than the one omc wants. Codex refuses
    # a same-named add from another source (exit 1), so it must be removed first.
    facts = PluginFacts(
        omc=PluginEntry("omc@oh-my-clanker", True, ()),
        superpowers=PluginEntry("superpowers@superpowers-marketplace", True, ()),
        marketplace_source="/old/checkout",
    )
    steps = p.plugin_repair_argvs(facts, source="chris-husse/oh-my-clanker", update=False)
    argvs = [s.argv for s in steps]
    remove_at = argvs.index(["codex", "plugin", "marketplace", "remove", "oh-my-clanker"])
    add_at = argvs.index(
        ["codex", "plugin", "marketplace", "add", "chris-husse/oh-my-clanker"]
    )
    # THE LOAD-BEARING ASSERTION: omc reads as installed in `facts`, but
    # removing the marketplace also removes its plugins, so the plan MUST
    # re-add omc anyway or codex is left with no plugin at all.
    omc_at = argvs.index(["codex", "plugin", "add", "omc@oh-my-clanker"])
    assert remove_at < add_at < omc_at
    assert steps[remove_at].fatal is False  # may legitimately not exist


def test_codex_repair_healthy_is_a_noop_and_same_source_is_not_a_conflict():
    from omc.providers.base import PluginEntry, PluginFacts

    p = get_provider("codex")
    facts = PluginFacts(
        omc=PluginEntry("omc@oh-my-clanker", True, ()),
        superpowers=PluginEntry("superpowers@openai-curated-remote", True, ()),
        marketplace_source="chris-husse/oh-my-clanker",
    )
    assert p.plugin_repair_argvs(facts, source="chris-husse/oh-my-clanker", update=False) == []


def test_codex_repair_update_local_source_refreshes_the_copy():
    from omc.providers.base import PluginEntry, PluginFacts

    p = get_provider("codex")
    facts = PluginFacts(
        omc=PluginEntry("omc@oh-my-clanker", True, ()),
        superpowers=PluginEntry("superpowers@superpowers-marketplace", True, ()),
        marketplace_source="/checkout/omc",
    )
    # Local source: the install is a COPY into a version-pinned dir, so an
    # unchanged version keeps serving stale skills. Remove before re-adding.
    local = p.plugin_repair_argvs(facts, source="/checkout/omc", update=True)
    argvs = [s.argv for s in local]
    rm_at = argvs.index(["codex", "plugin", "remove", "omc@oh-my-clanker"])
    add_at = argvs.index(["codex", "plugin", "add", "omc@oh-my-clanker"])
    assert rm_at < add_at
    assert local[rm_at].fatal is False
    assert [s.action for s in local if s.action] == ["updated"]

    # Git source: upgrade the snapshot, then re-add. No plugin remove.
    git_facts = PluginFacts(
        omc=PluginEntry("omc@oh-my-clanker", True, ()),
        superpowers=PluginEntry("superpowers@superpowers-marketplace", True, ()),
        marketplace_source="chris-husse/oh-my-clanker",
    )
    remote = p.plugin_repair_argvs(git_facts, source="chris-husse/oh-my-clanker", update=True)
    argvs = [s.argv for s in remote]
    assert ["codex", "plugin", "marketplace", "upgrade"] in argvs
    assert ["codex", "plugin", "remove", "omc@oh-my-clanker"] not in argvs
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_providers.py -k codex_plugin -v`
Expected: FAIL — `plugin_probe_argvs()` returns the base `[]`.

- [ ] **Step 3: Implement the codex members**

In `src/omc/providers/codex.py`. Replace the stale comment on `plugin_update_argvs` (the line claiming "codex has no scriptable per-marketplace add") with the verified facts:

```python
CODEX_PLUGIN_REF = "omc@oh-my-clanker"
CODEX_MARKETPLACE_NAME = "oh-my-clanker"
SUPERPOWERS_SOURCE = "obra/superpowers-marketplace"
CODEX_SUPERPOWERS_REF = "superpowers@superpowers-marketplace"


def _codex_entry(installed: list[dict], name: str) -> PluginEntry | None:
    raw = next((e for e in installed if e.get("name") == name), None)
    if raw is None:
        return None
    # No `errors` field exists in codex's JSON (verified 0.153.4) — there is
    # no load-error channel, so `installed`/`enabled` are the whole signal.
    enabled = raw.get("enabled") is not False
    problems = [] if enabled else ["the plugin is disabled"]
    if raw.get("installed") is False:
        problems.append("the plugin is not installed")
    return PluginEntry(
        id=str(raw.get("pluginId") or f"{name}@?"), enabled=enabled, problems=tuple(problems)
    )
```

Methods on `CodexProvider`:

```python
    def plugin_probe_argvs(self):
        # Two probes: the marketplace list carries the REGISTERED SOURCE, which
        # the plugin list does not. Both take --json (verified 0.153.4).
        # Order matters — parse_plugin_facts reads them positionally.
        return [
            ["codex", "plugin", "marketplace", "list", "--json"],
            ["codex", "plugin", "list", "--json"],
        ]

    def parse_plugin_facts(self, stdouts):
        markets = json.loads(stdouts[0] or "{}").get("marketplaces") or []
        ours = next(
            (m for m in markets if m.get("name") == CODEX_MARKETPLACE_NAME), None
        )
        source = None
        if ours:
            source = (ours.get("marketplaceSource") or {}).get("source") or ours.get("root")
        # `installed` only; `available` was empty even with uninstalled remote
        # plugins present, and path/git marketplaces are omitted from the JSON
        # entirely until their plugin is installed — so "absent" is the only
        # readable pre-install state.
        installed = json.loads(stdouts[1] or "{}").get("installed") or []
        installed = [e for e in installed if isinstance(e, dict)]
        return PluginFacts(
            omc=_codex_entry(installed, "omc"),
            superpowers=_codex_entry(installed, "superpowers"),
            marketplace_source=str(source) if source else None,
        )

    def plugin_repair_argvs(self, facts, *, source, update):
        omc_fix = (
            f"codex plugin marketplace add {source} && "
            f"codex plugin add {CODEX_PLUGIN_REF}"
        )
        # A same-named marketplace from a DIFFERENT source is refused with
        # exit 1 ("remove it before adding this source"), so re-register.
        conflict = facts.marketplace_source is not None and facts.marketplace_source != source
        steps: list[RepairStep] = []
        if conflict:
            steps.append(
                RepairStep(
                    ["codex", "plugin", "marketplace", "remove", CODEX_MARKETPLACE_NAME],
                    f"the {CODEX_MARKETPLACE_NAME} marketplace points at "
                    f"{facts.marketplace_source} — re-registering from {source}…",
                    fatal=False,
                )
            )
        needs_omc = facts.omc is None or facts.omc.problems or conflict
        if needs_omc:
            steps.append(
                RepairStep(
                    ["codex", "plugin", "marketplace", "add", source],
                    f"installing the omc plugin from {source}…",
                    fatal=True,
                    manual_fix=omc_fix,
                )
            )
            # `or conflict` is load-bearing: removing a marketplace also
            # removes its installed plugins, and `facts` predates the removal.
            steps.append(
                RepairStep(
                    ["codex", "plugin", "add", CODEX_PLUGIN_REF],
                    "",
                    fatal=True,
                    action="repaired" if facts.omc is not None else "installed",
                    manual_fix=omc_fix,
                )
            )
        if facts.superpowers is None:
            steps += [
                RepairStep(
                    ["codex", "plugin", "marketplace", "add", SUPERPOWERS_SOURCE],
                    "installing superpowers (omc's start skill hands off to it)…",
                    fatal=False,
                ),
                RepairStep(
                    ["codex", "plugin", "add", CODEX_SUPERPOWERS_REF],
                    "",
                    fatal=True,
                    action="installed superpowers",
                    manual_fix=(
                        f"codex plugin marketplace add {SUPERPOWERS_SOURCE} && "
                        f"codex plugin add {CODEX_SUPERPOWERS_REF}"
                    ),
                ),
            ]
        if update and not needs_omc:
            if not source.startswith(("http", "git@")) and "/" in source and source[0] in "/.~":
                # Local path: the install is a COPY into a version-pinned dir,
                # so an unchanged version keeps serving stale skills.
                steps.append(
                    RepairStep(
                        ["codex", "plugin", "remove", CODEX_PLUGIN_REF],
                        "refreshing the omc plugin copy…",
                        fatal=False,
                    )
                )
            steps += [
                RepairStep(
                    ["codex", "plugin", "marketplace", "upgrade"],
                    "",
                    fatal=False,
                ),
                RepairStep(
                    ["codex", "plugin", "add", CODEX_PLUGIN_REF],
                    "",
                    fatal=True,
                    action="updated",
                    manual_fix=omc_fix,
                ),
            ]
        return steps
```

Note the local-source test uses `/checkout/omc`, which starts with `/` — the guard above must classify it as local while `chris-husse/oh-my-clanker` is remote. Extract that decision into a module-level helper `_is_local_source(source: str) -> bool` returning `source.startswith(("/", "./", "~"))`, and use it in both places so the rule has one home.

- [ ] **Step 4: Run the provider tests**

Run: `uv run pytest tests/unit/test_providers.py -k codex -v`
Expected: PASS.

- [ ] **Step 5: Add the stateful codex stub**

In `tests/unit/_stubs.py`, following `make_claude_stub`'s shape exactly (stateful Python stub, JSON state files, an appended calls log, returns the calls path):

```python
def make_codex_stub(
    bindir: Path,
    *,
    plugins: list[dict] | None = None,
    marketplaces: list[dict] | None = None,
    add_rc: int = 0,
) -> Path:
    """A stateful `codex` stub for plugin-management tests.

    ``plugins`` seeds `codex plugin list --json`'s ``installed`` array (each
    entry: ``pluginId``, ``name``, optional ``enabled``); ``marketplaces``
    seeds `codex plugin marketplace list --json`. `plugin add X` installs X;
    `plugin remove X` uninstalls it; `plugin marketplace add SRC` registers it
    — and REFUSES with exit 1 when a same-named marketplace already exists
    from a different source, exactly like the real CLI (0.153.4); `plugin
    marketplace remove NAME` drops it AND its installed plugins (no zombie
    state). Every argv line is appended to the returned calls file.
    """
    import json
    import sys

    bindir.mkdir(parents=True, exist_ok=True)
    pstate = bindir / "codex.plugins.json"
    mstate = bindir / "codex.marketplaces.json"
    calls = bindir / "codex.calls"
    pstate.write_text(json.dumps([{"enabled": True, "installed": True, **e}
                                  for e in (plugins or [])]))
    mstate.write_text(json.dumps(marketplaces or []))
    script = f"""#!{sys.executable}
import json, sys
from pathlib import Path
pstate, mstate = Path({str(pstate)!r}), Path({str(mstate)!r})
calls = Path({str(calls)!r})
args = sys.argv[1:]
with calls.open("a") as fh:
    fh.write(" ".join(args) + "\\n")
if args[:1] == ["--version"]:
    print("codex-cli 0.153.4"); sys.exit(0)
plugins, markets = json.loads(pstate.read_text()), json.loads(mstate.read_text())
if args[:4] == ["plugin", "marketplace", "list", "--json"]:
    print(json.dumps({{"marketplaces": markets}})); sys.exit(0)
if args[:3] == ["plugin", "list", "--json"]:
    print(json.dumps({{"installed": plugins, "available": []}})); sys.exit(0)
if args[:3] == ["plugin", "marketplace", "add"]:
    if {add_rc} != 0:
        print("add failed", file=sys.stderr); sys.exit({add_rc})
    src = args[3]
    name = "superpowers-marketplace" if "superpowers" in src else "oh-my-clanker"
    same = [m for m in markets if m["name"] == name]
    if same and (same[0].get("marketplaceSource") or {{}}).get("source") != src:
        print("Error: marketplace '" + name + "' is already added from a "
              "different source; remove it before adding this source",
              file=sys.stderr)
        sys.exit(1)
    if not same:
        markets.append({{"name": name, "root": src,
                         "marketplaceSource": {{"sourceType": "local", "source": src}}}})
        mstate.write_text(json.dumps(markets))
    print("Added marketplace `" + name + "` from " + src); sys.exit(0)
if args[:3] == ["plugin", "marketplace", "remove"]:
    name = args[3]
    markets = [m for m in markets if m["name"] != name]
    mstate.write_text(json.dumps(markets))
    # removing a marketplace also removes its plugins - verified, no zombies
    plugins = [p for p in plugins if p.get("marketplaceName") != name]
    pstate.write_text(json.dumps(plugins))
    print("Removed marketplace `" + name + "`"); sys.exit(0)
if args[:3] == ["plugin", "marketplace", "upgrade"]:
    print("Upgraded 0 marketplace(s)."); sys.exit(0)
if args[:2] == ["plugin", "add"]:
    pid = args[2]
    name, _, mkt = pid.partition("@")
    plugins = [p for p in plugins if p.get("pluginId") != pid]
    plugins.append({{"pluginId": pid, "name": name, "marketplaceName": mkt,
                     "installed": True, "enabled": True, "version": "0.1.7"}})
    pstate.write_text(json.dumps(plugins))
    print("Added plugin `" + name + "`"); sys.exit(0)
if args[:2] == ["plugin", "remove"]:
    pid = args[2]
    pstate.write_text(json.dumps([p for p in plugins if p.get("pluginId") != pid]))
    print("Removed plugin"); sys.exit(0)
sys.exit(0)
"""
    path = bindir / "codex"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return calls


CODEX_OMC = {"pluginId": "omc@oh-my-clanker", "name": "omc",
             "marketplaceName": "oh-my-clanker"}
CODEX_SUPERPOWERS = {"pluginId": "superpowers@superpowers-marketplace",
                     "name": "superpowers",
                     "marketplaceName": "superpowers-marketplace"}
CODEX_MKT_LOCAL = {"name": "oh-my-clanker", "root": "/old/checkout",
                   "marketplaceSource": {"sourceType": "local",
                                         "source": "/old/checkout"}}
```

- [ ] **Step 6: Write the end-to-end `ensure_plugin` tests for codex**

Append to `tests/unit/test_plugin.py` (do **not** touch the 15 existing tests):

```python
def _codex_ctx(tmp_path, **kw):
    from ._stubs import make_codex_stub

    calls = make_codex_stub(tmp_path / "bin", **kw)
    return ToolContext.from_env(stub_env(tmp_path / "bin")), calls


def _codex_state(tmp_path):
    return {
        e["pluginId"]: e
        for e in json.loads((tmp_path / "bin" / "codex.plugins.json").read_text())
    }


def test_codex_fresh_install_registers_omc_and_superpowers(tmp_path, capsys):
    ctx, calls = _codex_ctx(tmp_path)
    assert ensure_plugin(ctx, "codex") == "installed superpowers"
    recorded = calls.read_text()
    assert "plugin marketplace add" in recorded
    assert "plugin add omc@oh-my-clanker" in recorded
    assert "plugin add superpowers@superpowers-marketplace" in recorded
    state = _codex_state(tmp_path)
    assert "omc@oh-my-clanker" in state
    assert "superpowers@superpowers-marketplace" in state
    assert "installing the omc plugin" in capsys.readouterr().err


def test_codex_healthy_is_left_alone(tmp_path):
    from ._stubs import CODEX_MKT_LOCAL, CODEX_OMC, CODEX_SUPERPOWERS

    mkt = {**CODEX_MKT_LOCAL, "root": "/checkout/omc",
           "marketplaceSource": {"sourceType": "local", "source": "/checkout/omc"}}
    ctx, calls = _codex_ctx(
        tmp_path, plugins=[CODEX_OMC, CODEX_SUPERPOWERS], marketplaces=[mkt]
    )
    # marketplace_source must match what marketplace_source(env) resolves to,
    # which with no uv receipt is the fallback repo -> so this IS a conflict.
    # Point the stub at the fallback instead to get the true no-op case.
    mkt["marketplaceSource"]["source"] = "chris-husse/oh-my-clanker"
    mkt["root"] = "chris-husse/oh-my-clanker"
    (tmp_path / "bin" / "codex.marketplaces.json").write_text(json.dumps([mkt]))
    assert ensure_plugin(ctx, "codex") == "ok"
    recorded = calls.read_text()
    assert "plugin add" not in recorded
    assert "marketplace remove" not in recorded


def test_codex_conflicting_marketplace_source_is_reregistered(tmp_path, capsys):
    from ._stubs import CODEX_MKT_LOCAL, CODEX_OMC, CODEX_SUPERPOWERS

    # Registered from /old/checkout while omc resolves the fallback repo.
    ctx, calls = _codex_ctx(
        tmp_path,
        plugins=[CODEX_OMC, CODEX_SUPERPOWERS],
        marketplaces=[CODEX_MKT_LOCAL],
    )
    assert ensure_plugin(ctx, "codex") == "repaired"
    lines = calls.read_text().splitlines()
    remove_at = lines.index("plugin marketplace remove oh-my-clanker")
    add_at = next(i for i, ln in enumerate(lines) if ln.startswith("plugin marketplace add"))
    omc_at = lines.index("plugin add omc@oh-my-clanker")
    assert remove_at < add_at < omc_at
    # THE REGRESSION THIS GUARDS: the marketplace removal wipes the plugin, so
    # without the `or conflict` clause codex would end up with no omc plugin.
    assert "omc@oh-my-clanker" in _codex_state(tmp_path)
    assert "re-registering" in capsys.readouterr().err


def test_codex_superpowers_from_another_marketplace_satisfies(tmp_path):
    from ._stubs import CODEX_OMC

    curated = {"pluginId": "superpowers@openai-curated-remote", "name": "superpowers",
               "marketplaceName": "openai-curated-remote"}
    mkt = {"name": "oh-my-clanker", "root": "chris-husse/oh-my-clanker",
           "marketplaceSource": {"sourceType": "git",
                                 "source": "chris-husse/oh-my-clanker"}}
    ctx, calls = _codex_ctx(tmp_path, plugins=[CODEX_OMC, curated], marketplaces=[mkt])
    assert ensure_plugin(ctx, "codex") == "ok"
    assert "plugin add" not in calls.read_text()


def test_codex_check_only_never_mutates(tmp_path):
    ctx, calls = _codex_ctx(tmp_path)
    status = ensure_plugin(ctx, "codex", check_only=True)
    assert "missing" in status
    recorded = calls.read_text()
    assert "plugin add" not in recorded
    assert "marketplace add" not in recorded


def test_codex_disabled_plugin_is_repaired(tmp_path):
    from ._stubs import CODEX_OMC, CODEX_SUPERPOWERS

    mkt = {"name": "oh-my-clanker", "root": "chris-husse/oh-my-clanker",
           "marketplaceSource": {"sourceType": "git",
                                 "source": "chris-husse/oh-my-clanker"}}
    ctx, _ = _codex_ctx(
        tmp_path,
        plugins=[{**CODEX_OMC, "enabled": False}, CODEX_SUPERPOWERS],
        marketplaces=[mkt],
    )
    assert ensure_plugin(ctx, "codex") == "repaired"
    assert _codex_state(tmp_path)["omc@oh-my-clanker"]["enabled"] is True


def test_codex_add_failure_carries_manual_commands(tmp_path):
    ctx, _ = _codex_ctx(tmp_path, add_rc=1)
    with pytest.raises(OmcError, match="fix manually: codex plugin marketplace add"):
        ensure_plugin(ctx, "codex")
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/unit/test_plugin.py -v`
Expected: PASS — 15 original + 7 new codex tests.

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/omc/providers/codex.py tests/unit/_stubs.py tests/unit/test_plugin.py tests/unit/test_providers.py
git commit -m "feat(codex): register the omc plugin and superpowers automatically

Codex sessions no longer open on 'Unknown command'. Includes the
different-source marketplace conflict repair, whose re-add is required
because removing a marketplace also removes its installed plugins."
```

---

### Task 5: Collapse `installer.py` and delete `plugin_update_argvs`

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/installer.py:79-118`
- Modify: `src/omc/providers/base.py` (remove `plugin_update_argvs`)
- Modify: `src/omc/providers/claude.py`, `src/omc/providers/codex.py`, `src/omc/providers/opencode.py` (remove their implementations)
- Test: `tests/unit/test_providers.py`, `tests/unit/test_installer.py`

**Interfaces:**
- Consumes: `ensure_plugin(ctx, name, update=True)` from Task 3.
- Produces: no new interfaces; `Provider.plugin_update_argvs` no longer exists.

- [ ] **Step 1: Delete the four obsolete tests**

Remove these from `tests/unit/test_providers.py` — the member they assert on is gone (the spec named only the first; there are four):

- `test_plugin_update_argvs_are_pure_and_per_provider` (line ~136)
- `test_claude_plugin_update_prepends_marketplace_add` (line ~147)
- `test_claude_plugin_update_without_source_omits_add` (line ~156)
- `test_codex_ignores_marketplace_source` (line ~163)

Their coverage is already replaced by the `plugin_repair_argvs` tests from Tasks 2 and 4 (the marketplace-add-before-install ordering, the update sequence, and codex's argv shapes are all asserted there).

- [ ] **Step 2: Write the failing installer test**

Append to `tests/unit/test_installer.py`:

```python
def test_update_uses_ensure_plugin_for_every_provider(monkeypatch, tmp_path):
    """run_update must not branch on provider name — every configured provider
    goes through ensure_plugin, and a failure in one never aborts the rest."""
    import omc.installer as installer

    seen = []

    def fake_ensure(ctx, name, *, check_only=False, update=False):
        seen.append((name, update))
        if name == "codex":
            raise OmcError("boom")
        return "updated"

    monkeypatch.setattr(installer, "ensure_plugin", fake_ensure)
    assert not hasattr(get_provider("claude"), "plugin_update_argvs")
    # (the rest of this test wires a GlobalConfig with providers
    #  ["claude", "codex", "opencode"] and asserts `seen` covers all three
    #  with update=True, and that the codex OmcError printed "✗ codex" and
    #  did not stop the loop — follow the existing monkeypatch/capsys style
    #  already used in tests/unit/test_installer.py for run_update.)
```

Read the existing `run_update` tests in `tests/unit/test_installer.py` first and match their fixture style for building the config and stubbing `gitnexus.update_gitnexus`; do not invent a new harness.

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/unit/test_installer.py -k ensure_plugin_for_every_provider -v`
Expected: FAIL — `plugin_update_argvs` still exists, so the `hasattr` assertion fails.

- [ ] **Step 4: Implement**

In `src/omc/installer.py`, replace lines 79–118 (the `source = marketplace_source(...)` line through the end of the loop) with:

```python
    for name in cfg.llm.providers:
        # No provider-name branching: ensure_plugin installs when missing,
        # repairs when the harness refuses to load it, and refreshes when
        # healthy. Providers with no scriptable probe report "unverified".
        try:
            status = ensure_plugin(ctx, name, update=True)
        except OmcError as exc:
            print(f"✗ {name}: {exc} — continuing", file=sys.stderr)
            continue
        mark = "·" if status.startswith("unverified") else "✓"
        print(f"{mark} {name}: omc plugin {status}", file=sys.stderr)
    return dep_rc
```

Drop the now-unused `marketplace_source` and `get_provider` imports from `installer.py` **only if nothing else there uses them** — check first (`marketplace_source` is imported at line 11 alongside `ensure_plugin`).

Then delete `plugin_update_argvs` from `base.py` (including its `@abstractmethod` decorator), `claude.py`, `codex.py` and `opencode.py`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit -q`
Expected: PASS. Confirm `grep -rn plugin_update_argvs src/ tests/` returns nothing.

- [ ] **Step 6: Commit**

```bash
git add src/omc/installer.py src/omc/providers/ tests/unit/test_providers.py tests/unit/test_installer.py
git commit -m "refactor(installer): route every provider through ensure_plugin

Deletes plugin_update_argvs; update is now an input to the repair planner."
```

---

### Task 6: Manifest, Docker image, and the codex E2E assertion

**Model:** standard coding tier

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `docker/setup-plugins.sh`
- Test: `tests/e2e/test_e2e_smoke.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (independent deliverable).
- Produces: no code interfaces.

- [ ] **Step 1: Add the manifest fields**

In `.codex-plugin/plugin.json`, add `"hooks": {}` and a `longDescription` inside `interface`, matching what superpowers 6.3.0 ships. **Do not touch `"version"`** — `tests/unit/test_plugin_manifests.py::test_all_version_strings_agree` locks it to `pyproject.toml` and both `.claude-plugin` files, and `scripts/stamp_version.py` owns it.

```json
  "skills": "./skills/",
  "hooks": {},
  "interface": {
    "displayName": "Oh My Clanker!",
    "shortDescription": "Ticket -> prepared worktree -> brainstorm-ready session",
    "longDescription": "Turn a ticket key, URL, or description into a prepared git worktree with a seeded, brainstorm-ready session: omc generates the branch slug, creates the worktree, and hands off to a design-first workflow that ends in a pushed branch.",
    "category": "Developer Tools"
  }
```

- [ ] **Step 2: Verify the manifest tests still pass**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -v`
Expected: PASS — especially `test_codex_plugin_manifest` and `test_all_version_strings_agree`.

- [ ] **Step 3: Add the codex install steps to the image**

In `docker/setup-plugins.sh`, replace the trailing codex section (currently a lone `codex plugin marketplace add /repo`, comment "Codex: repo marketplace registration") with:

```bash
# Codex: marketplace registration AND install — registration alone leaves the
# plugin "not installed", which serves no skills. Verbs are add/remove.
codex plugin marketplace add /repo 2>/dev/null || true
codex plugin add omc@oh-my-clanker 2>/dev/null || true

# superpowers for codex from obra: codex's own curated catalog entry is
# admin-blocked on managed machines (exit 1), so the git marketplace is the
# route that actually works.
codex plugin marketplace add obra/superpowers-marketplace 2>/dev/null || true
codex plugin add superpowers@superpowers-marketplace 2>/dev/null || true
```

- [ ] **Step 4: Write the E2E assertion**

Read `tests/e2e/test_e2e_smoke.py` and follow its existing container-invocation style. Add a test asserting codex-side registration — this needs **no auth**, so it is not token-gated:

```python
def test_codex_plugin_is_registered_in_the_image(container):
    """`codex plugin add` actually installs — registration alone leaves the
    plugin 'not installed' and serving no skills. Plugin commands need no
    auth, so this runs in CI unlike the seeded-session tests."""
    out = container.run("codex plugin list --json")
    installed = json.loads(out)["installed"]
    names = {e["name"] for e in installed}
    assert "omc" in names, out
    omc = next(e for e in installed if e["name"] == "omc")
    assert omc["installed"] is True and omc["enabled"] is True
```

Match the actual fixture/helper names in that file (`container.run` above is a placeholder for whatever the existing tests use — read them and use the real one).

- [ ] **Step 5: Run**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

E2E requires Docker; run it if available: `uv run pytest tests/e2e/test_e2e_smoke.py -k codex_plugin -v`. If the image must be rebuilt first, follow the `justfile` target the other E2E tests use. If Docker is unavailable in this environment, say so explicitly in the commit body rather than claiming the test passed.

- [ ] **Step 6: Commit**

```bash
git add .codex-plugin/plugin.json docker/setup-plugins.sh tests/e2e/test_e2e_smoke.py
git commit -m "feat(codex): install the plugin in the E2E image and assert registration"
```

---

### Task 7: Documentation truth

**Model:** standard coding tier

**Files:**
- Modify: `docker/PLUGIN-NOTES.md`
- Modify: `README.md:28,38`
- Modify: `src/omc/configure.py` (`_PLUGIN_HINTS`, line ~18)
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: nothing. Documentation only.
- Produces: nothing.

- [ ] **Step 1: Record the verified codex contract**

Append a new dated section to `docker/PLUGIN-NOTES.md`. The existing codex content is from codex-cli 0.144.5 and is now wrong in one specific way — mark it superseded rather than deleting the historical record (the file already uses that convention at the top). Copy the C1–C13 table from the spec's "The codex contract" section verbatim, headed:

```markdown
## Codex plugin registration — verified 2026-09-08 (codex-cli 0.153.4)

**Supersedes the codex claims above**, which were recorded against 0.144.5.
The key change: `codex plugin add` and `--json` output on both list commands
now exist, so `plugin_update_argvs`'s old comment ("codex has no scriptable
per-marketplace add") was false by the time it was read.
```

Include the two facts that most shaped the implementation: the absent `errors` channel, and the different-source `marketplace add` refusal (with its exact error text) plus the resulting remove-then-re-add repair and why the plugin must be re-added.

- [ ] **Step 2: Drop the stale manual instructions**

`README.md:28` currently reads:

```
| Codex | `codex plugin marketplace add chris-husse/oh-my-clanker`, then install `omc` from `/plugins` |
```

Replace with wording matching the claude row's "installed for you" framing, e.g.:

```
| Codex | installed for you by `omc start` / `omc update` / `omc configure`; by hand: `codex plugin marketplace add chris-husse/oh-my-clanker && codex plugin add omc@oh-my-clanker` |
```

`README.md:38` says superpowers for Codex/OpenCode is installed from obra by hand — update the Codex half to note omc installs it, keeping the OpenCode half unchanged.

In `src/omc/configure.py`, update `_PLUGIN_HINTS`: the Codex line loses "then install 'omc' from /plugins", and the superpowers block notes codex is handled automatically. Keep OpenCode's guidance exactly as-is.

- [ ] **Step 3: Close the ledger item**

Append one line to `.superpowers/sdd/progress.md` in the file's existing terse style, recording: codex plugin registration implemented (the `Provider` seam replaced the name branches; `plugin_update_argvs` deleted); superpowers for codex comes from obra because the curated entry is admin-blocked; the codex contract verified at 0.153.4; and the still-open item — whether a running session picks up a freshly installed plugin without a restart, which stays unverified for **both** providers.

- [ ] **Step 4: Verify nothing regressed**

Run: `uv run pytest tests/unit -q`
Expected: PASS. (`tests/unit/test_configure.py` may assert on `_PLUGIN_HINTS` content — check with `grep -n "PLUGIN_HINTS\|/plugins" tests/unit/test_configure.py` and update any pinned substring.)

- [ ] **Step 5: Commit**

```bash
git add docker/PLUGIN-NOTES.md README.md src/omc/configure.py .superpowers/sdd/progress.md
git commit -m "docs: record the verified codex plugin contract; drop manual install steps"
```

---

## Self-Review

**1. Spec coverage.** Every spec section maps to a task: Component changes 1→Task 1, 2→Task 2, 3→Task 4, 4→Task 5 (opencode loses its stub), 5→Task 3, 6→Task 5, 7→Task 6, 8→Task 6. "superpowers under codex"→Tasks 4 and 6. "Version pinning and `omc update`"→Task 4 (the local-source `plugin remove`). "Error handling"→Task 3 (`_probe`, fatal steps, re-probe) and Task 4 (the conflict narration). "Testing"→Tasks 1–6. Docs→Task 7.

**2. Deviations from the spec, made during the Phase 2 pressure-test** — flagged rather than silently applied:

- `RepairStep` gains **`action`** and **`manual_fix`** beyond the spec's `(argv, label, fatal)`. Required to preserve two test-locked behaviours: `ensure_plugin`'s exact status strings (today's `actions[-1]`) and `"fix manually: claude plugin marketplace add"`. Justified by `src/omc/probe.py`, which bundles argv with its label and remediation hint per item — the established convention.
- The three new members are **concrete defaults, not `@abstractmethod`**. `providers/registry.py` instantiates every provider at module import, so an abstract member breaks import until all three implement it. This also means opencode needs zero plugin code.
- The spec said one `test_providers.py` test needed replacing; there are **four**. All are removed in Task 5 with their coverage relocated to the new planner tests.

**3. Placeholder scan.** One deliberate gap remains: Task 5 Step 2 and Task 6 Step 4 tell the implementer to read the neighbouring tests and match their existing fixture style instead of inlining a guessed harness. `tests/unit/test_installer.py`'s `run_update` fixtures and `tests/e2e/test_e2e_smoke.py`'s container helper were not read while writing this plan, and inventing their names would produce code that doesn't run. Both steps name the exact file to read and the exact assertion to make.

**4. Type consistency.** `PluginEntry`/`PluginFacts`/`RepairStep` field names are identical across Tasks 1, 2 and 4. `plugin_repair_argvs(facts, *, source, update)` keyword-only signature matches in the ABC, both implementations, and `plugin.py`'s call. `_is_local_source` is defined once in Task 4 and used twice. Status strings in Task 2/4 `action` fields match the Global Constraints list exactly.
