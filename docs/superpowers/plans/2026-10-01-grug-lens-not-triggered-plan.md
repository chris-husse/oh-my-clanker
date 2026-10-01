# Grug Lens Not Triggered Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `omc update` and `omc start` actually advance the Claude omc plugin and tell the truth about it, so newly shipped skills (grug) reach sessions; make the review and spec skills fail loud without grug and fix Important findings with the top-tier model before asking the user.

**Architecture:** The live-config proof that the marketplace offers omc moves from Claude's `--available` list (which excludes installed plugins) to a read of the registered marketplace's `.claude-plugin/marketplace.json`. That same read yields the offered version, which `ensure_plugin` compares with the installed version to detect skew, heal it through the existing update sequence, and refuse to report "updated" when the version did not move. The unit stub is made faithful to the real CLI so the regression is testable; one Docker E2E pins the healthy-update path against real Claude. Two skill texts gain a fail-loud rule and a fix-first rule.

**Tech Stack:** Python 3 (`src/omc`), pytest with a stateful `claude` stub (`tests/unit/_stubs.py`), Docker-per-test E2E (`tests/e2e`, `just e2e-tests`), Markdown skills under `skills/`.

**Spec:** `docs/superpowers/specs/2026-10-01-grug-lens-not-triggered-design.md`

## Global Constraints

- omc never edits Claude's registry or settings files (`installed_plugins.json`, `known_marketplaces.json`, `settings.json`). It drives the `claude plugin` CLI and reads files.
- Provider quirks are documented as comments at the exact code site, naming the verified Claude version (`2.1.286`, verified 2026-10-01).
- Plugin sync in `run_update` stays best-effort: errors are printed as `✗ claude: … — continuing`; the CLI upgrade result is unaffected.
- A missing version on either side is **not** skew. Version comparison is plain string inequality.
- Skew is computed in `ensure_plugin` only, never inside `_problems`.
- Status strings: `stale (<installed> → <offered> offered; omc start will update it)` under `check_only`; `updated (<before> → <after>)` after a heal that moved the version; a heal that leaves the version unchanged raises `OmcError` naming both versions and `claude plugin update omc@oh-my-clanker`.
- Skill sentences (verbatim): `grug skill unavailable — plugin stale? run omc update`.
- Quick gate after every task: `just check` (runs `uv run pytest -m "not e2e and not local_iterm2" -q`). Ruff line length 100.
- Commit after every task with a conventional subject and the attribution line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Model tiers are named, never pinned ids: `top tier`, `heavy coding tier`, `standard coding tier`.

## Review Focus

1. omc installed from a marketplace with a different name (the `_find` prefix match accepts `omc@anything`): no `oh-my-clanker` registration → no skew, status `ok`, start proceeds. Pinned in Task 2 (`test_unregistered_marketplace_is_not_skew`).
2. Marketplace registered but its directory deleted (the stale-worktree case) while the plugin loads: `check_only` must return `ok`, not crash. Pinned in Task 2 (`test_unreadable_manifest_is_not_skew_in_dry_run`).
3. Marketplace list entry without `installLocation` (older Claude, directory source): the proof falls back to `path`. Pinned in Task 1 (`test_live_proof_falls_back_to_path`).
4. A marketplace that dropped omc from its manifest: the live proof fails on the manifest, not on the available list, and no `plugin update` runs. Pinned in Task 1 (`test_live_proof_reads_the_marketplace_manifest`).
5. Installed version newer than offered (developer switched marketplaces): treated as skew; an update that cannot move it raises with both versions, not a silent `ok`. Pinned in Task 2 (`test_update_that_does_not_move_the_version_fails_loud` parametrized with the downgrade pair).

---

### Task 1: Prove the marketplace offers omc from its manifest; make the stub real

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/plugin.py` (`_available` docstring, new `_offered`, end of `_prepare_marketplace`)
- Modify: `tests/unit/_stubs.py:46-165` (`make_claude_stub`)
- Modify: `tests/unit/test_plugin.py` (constants, `test_update_refreshes_a_healthy_plugin`, `test_missing_omc_in_replacement_does_not_remove_existing_marketplace`, `test_update_replaces_stale_marketplace_source`, `test_marketplace_failure_does_not_uninstall_plugin`, new tests)

**Interfaces:**
- Consumes: `_claude_json(ctx, argv)`, `_checked`, `MARKETPLACE_NAME`, `PLUGIN_REF`, `OmcError` (all existing in `src/omc/plugin.py`).
- Produces: `_offered(ctx: ToolContext) -> str | None` in `src/omc/plugin.py`: raises `OmcError` when the `oh-my-clanker` marketplace is not registered, has no install location, its manifest is unreadable, or lists no plugin named `omc`; returns the offered version string, or `None` when the manifest entry has no `version`. Stub knobs `offers_omc: bool`, `offered_version: str | None`, `update_moves_version: bool`; stub writes `<tmp>/marketplaces/<name>/.claude-plugin/marketplace.json`; installed entries carry `version`. Test helper `_remote(tmp_path) -> dict` in `tests/unit/test_plugin.py`.

- [ ] **Step 1: Rewrite the stub to model the real CLI**

Replace `make_claude_stub` in `tests/unit/_stubs.py` (keep `stub_env`, `make_stub`, `HEALTHY_PLUGINS` as they are) with:

```python
def make_claude_stub(
    bindir: Path,
    *,
    plugins: list[dict] | None = None,
    stdout: str = "",
    rc: int = 0,
    install_rc: int = 0,
    install_errors: list[str] | None = None,
    marketplaces: list[dict] | None = None,
    marketplace_failures: dict[str, str] | None = None,
    offers_omc: bool = True,
    offered_version: str | None = "0.1.0",
    update_moves_version: bool = True,
) -> Path:
    """A stateful `claude` stub for plugin-management tests, faithful to the
    real CLI where omc depends on it (claude 2.1.286, 2026-10-01):

    - `plugin list --json` lists installed plugins; each carries a ``version``
      (seeded entries default to ``offered_version``).
    - `plugin list --available --json` lists only plugins NOT installed — the
      real semantics; a stub that listed omc regardless hid the bug where
      omc's live-config proof always failed on an installed plugin.
    - Marketplace registrations carry a real ``installLocation`` under
      ``<bindir.parent>/marketplaces/<name>`` holding
      ``.claude-plugin/marketplace.json``; it offers ``omc`` at
      ``offered_version`` when ``offers_omc`` (no ``version`` key when the
      version is None). Seeded ``marketplaces`` entries whose installLocation
      lies under that root get the same manifest written; others (e.g. a
      deleted worktree path) stay unreadable on purpose.
    - `plugin install X` adds X at the offered version (healthy unless
      ``install_errors`` is set, or fails with ``install_rc``); `plugin
      uninstall X` removes it; `plugin update X` moves X to the offered
      version unless ``update_moves_version`` is False (a Claude that says
      ok and changes nothing).
    - Marketplace add models Claude's source conflict; remove cascades to the
      marketplace's plugins; failures can be injected by operation name.

    `--version` answers like the real CLI. Every other invocation prints
    ``stdout`` and exits ``rc`` (the slug/verdict path). Every argv line is
    appended to the returned calls file.
    """
    import json
    import sys

    bindir.mkdir(parents=True, exist_ok=True)
    root = bindir.parent / "marketplaces"
    root.mkdir(parents=True, exist_ok=True)
    state = bindir / "claude.plugins.json"
    calls = bindir / "claude.calls"
    entries = [{"enabled": True, "version": offered_version, **e} for e in (plugins or [])]
    state.write_text(json.dumps(entries))
    markets = bindir / "claude.marketplaces.json"
    markets.write_text(json.dumps(marketplaces or []))

    def write_manifest(entry: dict) -> None:
        location = str(entry.get("installLocation") or "")
        if not location.startswith(str(root)):
            return
        plugin = {"name": "omc", "source": "./"}
        if offered_version is not None:
            plugin["version"] = offered_version
        manifest = Path(location) / ".claude-plugin" / "marketplace.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"name": entry["name"], "plugins": [plugin] if offers_omc else []})
        )

    for entry in marketplaces or []:
        write_manifest(entry)

    script = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
state, calls = Path({str(state)!r}), Path({str(calls)!r})
markets = Path({str(markets)!r})
root = Path({str(root)!r})
offers_omc, offered, moves = {offers_omc!r}, {offered_version!r}, {update_moves_version!r}
isolated = os.environ.get("CLAUDE_CONFIG_DIR")
if isolated:
    cfg = Path(isolated)
    cfg.mkdir(parents=True, exist_ok=True)
    state, markets = cfg / "plugins.json", cfg / "marketplaces.json"
    for path in (state, markets):
        if not path.exists(): path.write_text("[]")
args = sys.argv[1:]
with calls.open("a") as fh:
    fh.write(("isolated " if isolated else "") + " ".join(args) + "\\n")

def write_manifest(entry):
    location = str(entry.get("installLocation") or "")
    if not location.startswith(str(root)):
        return
    plugin = {{"name": "omc", "source": "./"}}
    if offered is not None:
        plugin["version"] = offered
    manifest = Path(location) / ".claude-plugin" / "marketplace.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({{"name": entry["name"], "plugins": [plugin] if offers_omc else []}}))

if args[:1] == ["--version"]:
    print("2.1.286 (Claude Code)"); sys.exit(0)
if args[:2] == ["plugin", "list"]:
    entries = json.loads(state.read_text())
    if "--json" in args:
        if "--available" in args:
            installed = {{e["id"] for e in entries}}
            catalog = ["omc@oh-my-clanker"] if offers_omc else []
            available = [{{"pluginId": pid}} for pid in catalog if pid not in installed]
            print(json.dumps({{"installed": entries, "available": available}}))
        else:
            print(json.dumps(entries))
    else:
        print("Installed plugins:")
        for e in entries:
            print("  " + e["id"])
    sys.exit(0)
if args[:2] == ["plugin", "install"]:
    if {install_rc} != 0:
        print("install failed", file=sys.stderr); sys.exit({install_rc})
    pid = args[2]
    entries = [e for e in json.loads(state.read_text()) if e["id"] != pid]
    entry = {{"id": pid, "enabled": True, "version": offered}}
    if pid.startswith("omc@") and {install_errors!r}:
        entry["errors"] = {install_errors!r}
    entries.append(entry)
    state.write_text(json.dumps(entries)); print("installed " + pid); sys.exit(0)
if args[:2] == ["plugin", "uninstall"]:
    pid = args[2]
    state.write_text(json.dumps([e for e in json.loads(state.read_text()) if e["id"] != pid]))
    print("uninstalled " + pid); sys.exit(0)
if args[:2] == ["plugin", "update"]:
    pid = args[2]
    entries = json.loads(state.read_text())
    if moves:
        for e in entries:
            if e["id"] == pid:
                e["version"] = offered
        state.write_text(json.dumps(entries))
    print("ok"); sys.exit(0)
if args[:2] == ["plugin", "marketplace"]:
    op = args[2]
    failures = {marketplace_failures or {}!r}
    if op in failures:
        print(failures[op], file=sys.stderr); sys.exit(1)
    entries = json.loads(markets.read_text())
    if op == "list":
        print(json.dumps(entries)); sys.exit(0)
    if op == "add":
        source = args[3]
        if source.startswith("/"):
            new = {{"name": "oh-my-clanker", "source": "directory",
                   "path": source, "installLocation": source}}
        else:
            name = "oh-my-clanker" if source.endswith("oh-my-clanker") else source.split("/")[-1]
            new = {{"name": name, "source": "github", "repo": source,
                   "installLocation": str(root / name)}}
        old = next((m for m in entries if m["name"] == new["name"]), None)
        if old and any(old.get(k) != new.get(k) for k in ("source", "path", "repo")):
            print("source differs from the one declared in settings", file=sys.stderr); sys.exit(1)
        entries = [m for m in entries if m["name"] != new["name"]] + [new]
        markets.write_text(json.dumps(entries))
        write_manifest(new)
    if op == "remove":
        name = args[3]
        markets.write_text(json.dumps([m for m in entries if m["name"] != name]))
        plugins = [p for p in json.loads(state.read_text()) if not p["id"].endswith("@" + name)]
        state.write_text(json.dumps(plugins))
    print("ok"); sys.exit(0)
sys.stdout.write({stdout!r} + "\\n"); sys.exit({rc})
"""
    path = bindir / "claude"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return calls
```

- [ ] **Step 2: Adapt the test constants and the tests that used the old knob**

In `tests/unit/test_plugin.py`, replace the `REMOTE_MARKETPLACE` constant with a helper and update its users:

```python
def _remote(tmp_path):
    """The registration the stub creates for `marketplace add chris-husse/oh-my-clanker`."""
    return {
        "name": "oh-my-clanker",
        "source": "github",
        "repo": "chris-husse/oh-my-clanker",
        "installLocation": str(tmp_path / "marketplaces" / "oh-my-clanker"),
    }
```

- In `test_update_replaces_stale_marketplace_source`: `assert entries == [_remote(tmp_path)]`.
- In `test_marketplace_failure_does_not_uninstall_plugin`: `marketplaces=[STALE_MARKETPLACE if failure == "add" else _remote(tmp_path)]`.
- In `test_missing_omc_in_replacement_does_not_remove_existing_marketplace`: replace `available_omc=False` with `offers_omc=False`.
- Delete the module-level `REMOTE_MARKETPLACE` dict.

Then make the healthy-update test exercise the registered marketplace (this is the regression test for the bug):

```python
def test_update_refreshes_a_healthy_plugin(tmp_path):
    # The 2026-10-01 regression: with omc INSTALLED, Claude's `--available`
    # list no longer contains it, and the live-config proof aborted every
    # `omc update` before `plugin update` ran. The proof must not depend on
    # that list.
    ctx, calls = _ctx(tmp_path, plugins=HEALTHY_PLUGINS, marketplaces=[_remote(tmp_path)])
    assert ensure_plugin(ctx, "claude", update=True) == "updated"
    lines = calls.read_text().splitlines()
    assert "plugin marketplace update oh-my-clanker" in lines
    assert "plugin update omc@oh-my-clanker" in lines
    assert "plugin install omc@oh-my-clanker --scope user" not in lines
```

Add two new tests after it:

```python
def test_live_proof_reads_the_marketplace_manifest(tmp_path):
    # A marketplace that dropped omc is caught by its manifest — and nothing
    # is updated or removed.
    ctx, calls = _ctx(
        tmp_path, plugins=HEALTHY_PLUGINS, marketplaces=[_remote(tmp_path)], offers_omc=False
    )
    with pytest.raises(OmcError, match="does not offer omc@oh-my-clanker"):
        ensure_plugin(ctx, "claude", update=True)
    lines = calls.read_text().splitlines()
    assert "plugin update omc@oh-my-clanker" not in lines
    assert "plugin marketplace remove oh-my-clanker --scope user" not in lines


def test_live_proof_falls_back_to_path(tmp_path):
    # Older registrations carry `path` but no `installLocation` for a
    # directory source; the proof reads the manifest from `path`. Tested on
    # the helper directly: through ensure_plugin this registration would be
    # REPLACED (marketplace_source() falls back to the GitHub source in a
    # stub env), and the stub's fresh registration always has installLocation.
    location = tmp_path / "marketplaces" / "oh-my-clanker"
    registration = {"name": "oh-my-clanker", "source": "directory", "path": str(location)}
    ctx, _ = _ctx(tmp_path, plugins=HEALTHY_PLUGINS, marketplaces=[registration])
    (location / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (location / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "oh-my-clanker", "plugins": [{"name": "omc", "version": "0.1.0"}]})
    )
    assert _offered(ctx) == "0.1.0"


def test_live_proof_without_registration_or_manifest_is_an_error(tmp_path):
    ctx, _ = _ctx(tmp_path, plugins=HEALTHY_PLUGINS)
    with pytest.raises(OmcError, match="not registered"):
        _offered(ctx)
    ctx, _ = _ctx(tmp_path / "second", plugins=HEALTHY_PLUGINS, marketplaces=[STALE_MARKETPLACE])
    with pytest.raises(OmcError, match="cannot read the marketplace manifest"):
        _offered(ctx)
```

Change the import line at the top of the module to `from omc.plugin import _offered, ensure_plugin, marketplace_source`. The `_ctx(tmp_path / "second", …)` call needs the stub's `bindir.parent` to exist: `make_claude_stub` does `bindir.mkdir(parents=True, exist_ok=True)`, so it does.

- [ ] **Step 3: Run the suite to see the regression test fail**

Run: `uv run pytest tests/unit/test_plugin.py -q`
Expected: `test_update_refreshes_a_healthy_plugin` FAILS with `OmcError: refusing plugin replacement: marketplace does not offer omc@oh-my-clanker` (the live-config `_available` call on an installed plugin). `test_live_proof_reads_the_marketplace_manifest` may pass by coincidence (same error text); `test_live_proof_falls_back_to_path` FAILS with the same `_available` error. The replacement-path tests (`test_update_replaces_stale_marketplace_source`, …) still pass because the probe's `_available` sees an empty isolated state.

- [ ] **Step 4: Add `_offered` and use it as the live proof**

In `src/omc/plugin.py`, change `_available`'s body to carry the quirk and add `_offered` right after it:

```python
def _available(ctx: ToolContext) -> None:
    """Prove the marketplace offers omc — PROBE CONFIG ONLY.

    `plugin list --available --json` lists plugins that are NOT installed
    (claude 2.1.286, verified 2026-10-01: the installed set and the available
    set were disjoint for all eight installed plugins). Inside the isolated
    probe omc is not installed yet, so its presence here is a valid proof.
    On the live config, where omc IS installed, this list can never contain
    it — see _offered for the live-config proof.
    """
    data = _claude_json(ctx, ["claude", "plugin", "list", "--available", "--json"])
    available = data.get("available") if isinstance(data, dict) else None
    if not isinstance(available, list) or not any(
        isinstance(p, dict) and p.get("pluginId") == PLUGIN_REF for p in available
    ):
        raise OmcError(f"refusing plugin replacement: marketplace does not offer {PLUGIN_REF}")


def _offered(ctx: ToolContext) -> str | None:
    """Prove the registered oh-my-clanker marketplace offers omc on the LIVE
    config, and return the version it offers (None when the manifest entry
    carries no version).

    Reads the registration's installLocation from `claude plugin marketplace
    list --json` — Claude's clone for a github source, the directory itself
    for a directory source (older registrations carry only `path`) — then
    `.claude-plugin/marketplace.json` there. Deliberately NOT `plugin list
    --available`: that list excludes installed plugins (see _available), so
    using it here made every `omc update` on an installed plugin abort
    before `plugin update` ran — the installed plugin sat at 0.1.11 while
    the marketplace offered 0.1.13 (2026-10-01).
    """
    entries = _claude_json(ctx, ["claude", "plugin", "marketplace", "list", "--json"])
    current = None
    if isinstance(entries, list):
        current = next(
            (e for e in entries if isinstance(e, dict) and e.get("name") == MARKETPLACE_NAME),
            None,
        )
    if current is None:
        raise OmcError(f"the {MARKETPLACE_NAME} marketplace is not registered")
    location = current.get("installLocation") or current.get("path")
    if not location:
        raise OmcError(f"the {MARKETPLACE_NAME} marketplace registration has no install location")
    manifest = Path(str(location)) / ".claude-plugin" / "marketplace.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise OmcError(f"cannot read the marketplace manifest at {manifest}: {exc}") from exc
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entry = next(
        (p for p in (plugins or []) if isinstance(p, dict) and p.get("name") == "omc"), None
    )
    if entry is None:
        raise OmcError(
            f"refusing plugin replacement: marketplace at {location} does not offer {PLUGIN_REF}"
        )
    version = entry.get("version")
    return str(version) if version else None
```

At the end of `_prepare_marketplace`, replace the last two lines

```python
    _available(ctx)
    return replaced
```

with

```python
    _offered(ctx)  # live-config proof: the manifest, never the available list
    return replaced
```

and add to `_prepare_marketplace`'s docstring, after "Never edit Claude's registry/settings files ourselves.":

```
    The probe proves offering through `plugin list --available` (valid there:
    omc is not installed in the scratch config); the live config proves it
    through the marketplace manifest (`_offered`).
```

- [ ] **Step 5: Run the plugin tests to verify they pass**

Run: `uv run pytest tests/unit/test_plugin.py -q`
Expected: all PASS. If `test_live_proof_falls_back_to_path` fails because the flow replaced the registration, apply the simplification noted in Step 2 (assert on `_offered(ctx)` directly) and re-run.

- [ ] **Step 6: Run the quick gate**

Run: `just check`
Expected: exit 0, all unit tests green (other suites use `make_claude_stub` with defaults, which are unchanged in behaviour for them).

- [ ] **Step 7: Commit**

```bash
git add src/omc/plugin.py tests/unit/_stubs.py tests/unit/test_plugin.py
git commit -m "fix: prove the marketplace offers omc from its manifest, not Claude's available list

Claude 2.1.286's \`plugin list --available\` excludes installed plugins, so
the live-config proof failed on every machine with omc installed and
\`omc update\` never reached \`plugin update\`. The stub now models the
real list, installed versions, and marketplace manifests.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Detect version skew, heal it, and report honestly

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/plugin.py` (new `_skew`, `ensure_plugin` body)
- Test: `tests/unit/test_plugin.py` (new tests)
- Test: `tests/unit/test_start.py` (two new tests)

**Interfaces:**
- Consumes: `_offered(ctx) -> str | None` (Task 1), stub knobs `offered_version`, `update_moves_version`, helper `_remote(tmp_path)` (Task 1).
- Produces: `_skew(ctx: ToolContext, omc: dict) -> tuple[str, str] | None` returning `(installed, offered)` when both known and different, else `None`. `ensure_plugin` statuses: `stale (a → b offered; omc start will update it)` (check_only), `updated (a → b)` (version moved), `updated` (update requested, version unchanged because it was already current), `OmcError("the omc plugin is still a after updating (b offered) — run: claude plugin update omc@oh-my-clanker")`.

- [ ] **Step 1: Write the failing tests in `tests/unit/test_plugin.py`**

Append:

```python
STALE_OMC = {**OMC, "version": "0.1.11"}


def test_update_advances_a_stale_plugin(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[STALE_OMC, SUPERPOWERS],
        marketplaces=[_remote(tmp_path)],
        offered_version="0.1.13",
    )
    assert ensure_plugin(ctx, "claude", update=True) == "updated (0.1.11 → 0.1.13)"
    lines = calls.read_text().splitlines()
    assert "plugin marketplace update oh-my-clanker" in lines
    assert "plugin update omc@oh-my-clanker" in lines
    assert "plugin uninstall omc@oh-my-clanker" not in lines  # stale is never the reinstall path
    assert _state(tmp_path)["omc@oh-my-clanker"]["version"] == "0.1.13"


@pytest.mark.parametrize(("installed", "offered"), [("0.1.11", "0.1.13"), ("0.1.13", "0.1.9")])
def test_update_that_does_not_move_the_version_fails_loud(tmp_path, installed, offered):
    # Claude says ok and changes nothing (the shape `omc update` lied about
    # for three days) — and the downgrade pair: "different" is skew too.
    ctx, _ = _ctx(
        tmp_path,
        plugins=[{**OMC, "version": installed}, SUPERPOWERS],
        marketplaces=[_remote(tmp_path)],
        offered_version=offered,
        update_moves_version=False,
    )
    with pytest.raises(OmcError) as info:
        ensure_plugin(ctx, "claude", update=True)
    message = str(info.value)
    assert f"still {installed}" in message
    assert f"{offered} offered" in message
    assert "claude plugin update omc@oh-my-clanker" in message


def test_stale_plugin_is_healed_on_start_path(tmp_path, capsys):
    # No update flag: this is what `omc start` and `omc configure` call.
    ctx, calls = _ctx(
        tmp_path,
        plugins=[STALE_OMC, SUPERPOWERS],
        marketplaces=[_remote(tmp_path)],
        offered_version="0.1.13",
    )
    assert ensure_plugin(ctx, "claude") == "updated (0.1.11 → 0.1.13)"
    lines = calls.read_text().splitlines()
    assert "plugin update omc@oh-my-clanker" in lines
    assert "plugin install omc@oh-my-clanker --scope user" not in lines
    assert "stale" in capsys.readouterr().err


def test_check_only_reports_stale_without_mutating(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[STALE_OMC, SUPERPOWERS],
        marketplaces=[_remote(tmp_path)],
        offered_version="0.1.13",
    )
    status = ensure_plugin(ctx, "claude", check_only=True)
    assert status.startswith("stale (0.1.11 → 0.1.13 offered")
    recorded = calls.read_text()
    assert "plugin update" not in recorded
    assert "plugin install" not in recorded
    assert "plugin marketplace update" not in recorded


def test_current_plugin_is_ok_without_update_commands(tmp_path):
    ctx, calls = _ctx(
        tmp_path,
        plugins=[{**OMC, "version": "0.1.13"}, SUPERPOWERS],
        marketplaces=[_remote(tmp_path)],
        offered_version="0.1.13",
    )
    assert ensure_plugin(ctx, "claude") == "ok"
    assert "plugin update" not in calls.read_text()


def test_unregistered_marketplace_is_not_skew(tmp_path):
    # omc installed, no oh-my-clanker registration (e.g. installed under
    # another marketplace name): plumbing is never skew, start proceeds.
    ctx, calls = _ctx(tmp_path, plugins=[STALE_OMC, SUPERPOWERS], offered_version="0.1.13")
    assert ensure_plugin(ctx, "claude") == "ok"
    assert "plugin update" not in calls.read_text()


def test_unreadable_manifest_is_not_skew_in_dry_run(tmp_path):
    # Registered, but the directory is gone (stale worktree) while the plugin
    # still loads: dry-run says ok rather than crashing over plumbing.
    ctx, _ = _ctx(
        tmp_path, plugins=[STALE_OMC, SUPERPOWERS], marketplaces=[STALE_MARKETPLACE]
    )
    assert ensure_plugin(ctx, "claude", check_only=True) == "ok"


@pytest.mark.parametrize("side", ["installed", "offered"])
def test_unknown_versions_are_not_skew(tmp_path, side):
    plugins = [{**OMC, "version": ""} if side == "installed" else STALE_OMC, SUPERPOWERS]
    ctx, calls = _ctx(
        tmp_path,
        plugins=plugins,
        marketplaces=[_remote(tmp_path)],
        offered_version=None if side == "offered" else "0.1.13",
    )
    assert ensure_plugin(ctx, "claude") == "ok"
    assert "plugin update" not in calls.read_text()
```

- [ ] **Step 2: Write the failing tests in `tests/unit/test_start.py`**

Append next to `test_dry_run_reports_a_plugin_that_fails_to_load`:

```python
def _remote(tmp_path):
    return {
        "name": "oh-my-clanker",
        "source": "github",
        "repo": "chris-husse/oh-my-clanker",
        "installLocation": str(tmp_path / "marketplaces" / "oh-my-clanker"),
    }


def test_start_heals_a_stale_plugin(tmp_path, capsys):
    # The plugin loads but is behind the marketplace: start updates it BEFORE
    # the seeded session runs, so the new session carries the new skills
    # (Claude's "restart required" is satisfied by the launch itself).
    bindir = tmp_path / "bin"
    _make_git_stub(bindir)
    stale = [
        {"id": "omc@oh-my-clanker", "version": "0.1.11"},
        {"id": "superpowers@claude-plugins-official"},
    ]
    calls = make_claude_stub(
        bindir,
        plugins=stale,
        marketplaces=[_remote(tmp_path)],
        offered_version="0.1.13",
        stdout=OK_VERDICT,
    )
    make_stub(bindir, "wt", stdout=json.dumps({"path": str(tmp_path / "wtree")}))
    (tmp_path / "wtree").mkdir()
    ctx = ToolContext.from_env(stub_env(bindir, SHELL="/bin/bash"))
    assert run_start(ctx, Config(), "PROJ-1", headless=True) == 0
    assert "→ omc plugin for claude: updated (0.1.11 → 0.1.13)" in capsys.readouterr().err
    lines = calls.read_text().splitlines()
    update_at = lines.index("plugin update omc@oh-my-clanker")
    seed_at = next(i for i, ln in enumerate(lines) if ln.startswith("-p /omc:start"))
    assert update_at < seed_at


def test_dry_run_reports_a_stale_plugin(tmp_path, capsys):
    bindir = tmp_path / "bin"
    _make_git_stub(bindir)
    stale = [
        {"id": "omc@oh-my-clanker", "version": "0.1.11"},
        {"id": "superpowers@claude-plugins-official"},
    ]
    calls = make_claude_stub(
        bindir,
        plugins=stale,
        marketplaces=[_remote(tmp_path)],
        offered_version="0.1.13",
        stdout=OK_VERDICT,
    )
    make_stub(bindir, "wt", stdout=json.dumps({"path": str(tmp_path / "wtree")}))
    ctx = ToolContext.from_env(stub_env(bindir, SHELL="/bin/bash"))
    assert run_start(ctx, Config(), "PROJ-1", dry_run=True) == 0
    assert "→ omc plugin for claude: stale (0.1.11 → 0.1.13 offered" in capsys.readouterr().err
    assert "plugin update" not in calls.read_text()  # dry run never mutates
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_plugin.py tests/unit/test_start.py -q -k "stale or skew or move_the_version or unknown_versions or current_plugin or unregistered or unreadable"`
Expected: the skew/stale tests FAIL (status is `ok` where `stale`/`updated (…)` is expected; no `OmcError` where one is expected). `test_unregistered_marketplace_is_not_skew`, `test_unreadable_manifest_is_not_skew_in_dry_run`, `test_current_plugin_is_ok_without_update_commands`, and `test_unknown_versions_are_not_skew` already PASS (they pin the non-regression).

- [ ] **Step 4: Implement `_skew` and the new `ensure_plugin` body**

In `src/omc/plugin.py`, add after `_offered`:

```python
def _skew(ctx: ToolContext, omc: dict) -> tuple[str, str] | None:
    """(installed, offered) when both versions are known and differ — the
    plugin loads but is stale. None otherwise, including when the marketplace
    is not registered or its manifest is unreadable: omc never fails over its
    own plumbing, and a missing version on either side (older Claude output
    without `version`, a manifest without one) is not skew. Plain string
    inequality: the installed snapshot always came from the registered
    marketplace, so "different" means "stale" in practice, and after a heal
    "equal" is the only honest success criterion.
    """
    installed = omc.get("version")
    if not installed:
        return None
    try:
        offered = _offered(ctx)
    except OmcError:
        return None
    if not offered or offered == str(installed):
        return None
    return str(installed), offered
```

Replace the body of `ensure_plugin` from `entries = _list_plugins(ctx)` to the end with:

```python
    entries = _list_plugins(ctx)
    omc = _find(entries, "omc")
    superpowers = _find(entries, "superpowers")
    omc_problems = _problems(omc) if omc is not None else []
    # Skew is judged HERE only — never inside _problems, which also serves the
    # probe's load check in _prepare_marketplace and would route a stale but
    # loadable plugin through the destructive uninstall/reinstall path.
    skew = _skew(ctx, omc) if omc is not None and not omc_problems else None

    if check_only:
        if omc is None:
            return "missing (omc start will install it)"
        if omc_problems:
            return f"failed to load: {omc_problems[0]} (omc start will reinstall it)"
        if skew is not None:
            return f"stale ({skew[0]} → {skew[1]} offered; omc start will update it)"
        if superpowers is None:
            return "ok; superpowers missing (omc start will install it)"
        return "ok"

    source = marketplace_source(ctx.env)
    refresh = omc is None or bool(omc_problems) or update or skew is not None
    if refresh:
        _check_project_source(ctx, source)
    omc_fix = (
        f"claude plugin marketplace add {shlex.quote(source)} && "
        f"claude plugin install {PLUGIN_REF} --scope user"
    )
    actions: list[str] = []

    if superpowers is None:
        _say("installing superpowers (omc's start skill hands off to it)…")
        # Best-effort: the official marketplace is usually pre-registered.
        ctx.run(["claude", "plugin", "marketplace", "add", _OFFICIAL_MARKETPLACE])
        _install(
            ctx,
            SUPERPOWERS_REF,
            manual_fix=(
                f"claude plugin marketplace add {_OFFICIAL_MARKETPLACE} && "
                f"claude plugin install {SUPERPOWERS_REF}"
            ),
        )
        actions.append("installed superpowers")

    replaced = False
    if refresh:
        try:
            replaced = _prepare_marketplace(ctx, source)
        except OmcError as exc:
            raise OmcError(
                f"{exc}\n  if the old marketplace is still registered, remove it first: "
                f"claude plugin marketplace remove {MARKETPLACE_NAME} --scope user\n"
                f"  fix manually: {omc_fix}"
            ) from exc

    before = str(omc.get("version") or "") if omc is not None else ""
    if omc is None:
        _say(f"installing the omc plugin from {source}…")
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("installed")
    elif omc_problems:
        _say(
            f"the omc plugin is installed but failed to load ({omc_problems[0]}) "
            f"— reinstalling from {source}…"
        )
        if not replaced:
            _checked(ctx, ["claude", "plugin", "uninstall", PLUGIN_REF])
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("repaired")
    elif update or skew is not None:
        if skew is not None and not update:
            _say(f"the omc plugin is stale ({skew[0]} installed, {skew[1]} offered) — updating…")
        if replaced:
            _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        else:
            _checked(ctx, ["claude", "plugin", "update", PLUGIN_REF])
        actions.append("updated")

    if not actions:
        return "ok"

    omc = _find(_list_plugins(ctx), "omc")
    if omc is None:
        raise OmcError(
            "the omc plugin is still missing after an apparently successful install — "
            "check `claude plugin list` and the plugin's Status line"
        )
    problems = _problems(omc)
    if problems:
        joined = "\n  ".join(problems)
        raise OmcError(
            "the omc plugin still fails to load after reinstalling it:\n"
            f"  {joined}\n"
            "  check `claude plugin list`; if the error names a missing dependency, "
            "your marketplace snapshot may predate the fix — retry after `omc update`"
        )
    if actions[-1] == "updated":
        # An update that left the version behind is not an update. Say so with
        # both versions and the manual command, instead of "✓ updated".
        still = _skew(ctx, omc)
        if still is not None:
            raise OmcError(
                f"the omc plugin is still {still[0]} after updating ({still[1]} offered) — "
                f"run: claude plugin update {PLUGIN_REF}"
            )
        after = str(omc.get("version") or "")
        if before and after and before != after:
            return f"updated ({before} → {after})"
    return actions[-1]
```

Also extend the docstring's first paragraph with one sentence: "Installed, loadable, but behind the marketplace's offered version (skew) → run the update sequence, and report the version transition."

- [ ] **Step 5: Run the plugin and start tests**

Run: `uv run pytest tests/unit/test_plugin.py tests/unit/test_start.py -q`
Expected: all PASS, including the pre-existing `test_update_replaces_stale_marketplace_source` (the stub's `add` writes a manifest at the offered version `0.1.0`, the seeded omc defaults to `0.1.0`, so no post-update skew) and `test_source_replacement_does_not_refresh_again_after_destructive_remove`.

- [ ] **Step 6: Run the quick gate**

Run: `just check`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/omc/plugin.py tests/unit/test_plugin.py tests/unit/test_start.py
git commit -m "feat: detect a stale omc plugin, heal it on start and update, and never report an update that did not move

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Review and spec skills: fail loud without grug, fix first by the top tier

**Model:** top tier

**Files:**
- Modify: `skills/review/SKILL.md` (step 3)
- Modify: `skills/spec/SKILL.md` (step 2, step 4)
- Test: `tests/unit/test_plugin_manifests.py` (`test_review_proxy_runs_grug`, `test_spec_skill_contract`)

**Interfaces:**
- Consumes: the grug skill's disposition vocabulary (`fixed`, `waived`, `UNDISPOSITIONED`) and summary block, unchanged.
- Produces: the verbatim sentence `grug skill unavailable — plugin stale? run omc update` in both skills; the phrases `Fix first, top tier` / `Fix, by the top tier` and `Ask, batched` that the contract tests anchor on.

- [ ] **Step 1: Extend the contract tests**

In `tests/unit/test_plugin_manifests.py`, add to the needle tuple in `test_review_proxy_runs_grug`:

```python
        "grug skill unavailable — plugin stale? run omc update",
        "top-tier",
        "batched",
```

and after the existing `steps` ordering assertion add:

```python
    # the lens is unavailable → the stage FAILS, it never passes lens-less
    unavailable = text.index("grug skill unavailable")
    assert '"passed": false' in text[unavailable - 400 : unavailable + 200]
    # fix first (top tier), waive second, ask last — and batched
    assert text.index("Fix first, top tier") < text.index("waive") < text.index("batched")
```

Careful: `text.index("waive")` finds the first occurrence; the review skill's first "waive" must come after the fix-first heading. Place the new fix-first paragraph as the FIRST bullet under step 3 (before the existing "fix now" / "waive" bullets).

In `test_spec_skill_contract`, add to the needle tuple:

```python
        "grug skill unavailable — plugin stale? run omc update",
        "Fix, by the top tier",
        "Ask, batched",
```

and after the existing `step2` assertions add:

```python
    step4 = text.index("## Step 4")
    assert text.index("Fix, by the top tier", step4) < text.index("Waive by record", step4)
    assert text.index("Waive by record", step4) < text.index("Ask, batched", step4)
```

- [ ] **Step 2: Run the contract tests to verify they fail**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q -k "review_proxy_runs_grug or spec_skill_contract"`
Expected: both FAIL on the missing needles.

- [ ] **Step 3: Edit `skills/review/SKILL.md` step 3**

Replace step 3 (from `3. **Grug lens.**` up to but not including `4. **Always** end with`) with:

```markdown
3. **Grug lens.** Determine the base branch the way `finish` does
   (`worktree.base_branch` in `.omc/config.yaml`, else the remote's HEAD
   branch) and invoke the internal `grug` skill with `grug diff <base>`.
   - **Grug unavailable.** If the `grug` skill cannot be invoked (unknown
     skill, not listed, or it answers a well-formed payload with its usage
     line), the lens did NOT run. Go straight to step 4 with
     `"passed": false` and the summary
     `grug skill unavailable — plugin stale? run omc update`. A review that
     passes without the lens is the failure this rule exists to prevent.

   Otherwise read its `grug summary:` block and disposition every Important
   finding in this order:
   - **Fix first, top tier.** Before any waive, the top-tier model (the
     behavior layer's model-tier policy, `AGENTS.md` Model selection)
     attempts the behavior-preserving simplification: the code does the
     same thing with less of it. Dispatch it as a top-tier subagent where
     the harness can pick a model per subagent; otherwise the session model
     does it — never a cheaper tier. After the fix, invoke `/omc:check`
     once; a failing check means the fix is wrong: revert it. Only a finding
     whose fix failed check, or whose fix would change behavior, proceeds.
   - **waive** — one line of reason, written into the branch's design
     record under "Deliberate complexity" (grug's summary names the record;
     append the section if missing; replace a `None.` placeholder). With no
     design record on the branch, the reason lives only in the summary. A fix
     that broke check is waived as `simplification broke check; left as-is`.
   - Any Important finding left without a disposition fails this stage.
   - Questions for the user, if any remain, are **batched** into one numbered
     list at the end of the stage — never one dialog per finding.
   Minor findings are listed in the summary and never gate.
```

Check that every needle the existing test already asserts (`grug diff`, `fix now`, `waive`, `/omc:check`, `Deliberate complexity`) is still present. `fix now` must survive: keep the phrase by writing the first bullet's opening as `**Fix first, top tier** (the old "fix now", done by the best model).` — i.e. change the bullet's first line to:

```markdown
   - **Fix first, top tier** (the "fix now" path, done by the best model).
     Before any waive, the top-tier model …
```

- [ ] **Step 4: Edit `skills/spec/SKILL.md` steps 2 and 4**

In Step 2, after item 2 (the `grug section` call) and before "Refine the section with both answers", insert:

```markdown
   If the `grug` skill cannot be invoked (unknown skill, not listed, or it
   answers a well-formed payload with its usage line), stop hardening and
   report `grug skill unavailable — plugin stale? run omc update`. Never
   continue with explain-only hardening as if the lens had run.
```

Replace Step 4's body (from "Repeat steps 2–3 until neither explain nor grug surfaces real issues." to the end of that section) with:

```markdown
Repeat steps 2–3 until neither explain nor grug surfaces real issues. Every
Important grug finding is dispositioned in this order — fix first, ask last:

1. **Fix, by the top tier.** The top-tier model (the behavior layer's
   model-tier policy, `AGENTS.md` Model selection) rewrites the section with
   the simpler alternative when that keeps the converged design. Dispatch it
   as a top-tier subagent where the harness can pick a model per subagent;
   otherwise the session model does it — never a cheaper tier.
2. **Waive by record.** A finding the rewrite rejects because it contradicts
   an entry in the record's "Decisions taken during brainstorm" table is
   waived into "Deliberate complexity" citing that decision — the
   brainstorm already settled it, so it is not a CRITICAL question.
3. **Ask, batched.** Only what survives both goes to the user, as ONE
   numbered list of CRITICAL follow-up questions at the end of the pass —
   never one dialog per finding, never a silent choice on their behalf.
   What the user waives goes into "Deliberate complexity" with its reason.
```

Check the existing needles still hold: `follow-up`, `review`, `architectural`, `plan phase`, `Deliberate complexity`, `None.`, `whole-spec`, `EACH section`.

- [ ] **Step 5: Run the contract tests**

Run: `uv run pytest tests/unit/test_plugin_manifests.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the quick gate**

Run: `just check`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add skills/review/SKILL.md skills/spec/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "feat: review and spec fail loud without grug and fix Important findings by the top tier before asking

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Docker E2E for the healthy-update path, Claude pin, notes, README

**Model:** heavy coding tier

**Files:**
- Modify: `tests/e2e/test_e2e_marketplace_repair.py` (new fixture `_stale_marketplace`, new test)
- Modify: `docker/Dockerfile.e2e:39-43` (pin + comment)
- Modify: `docker/PLUGIN-NOTES.md` (new dated entry at the top)
- Modify: `README.md:30`

**Interfaces:**
- Consumes: `_run`, `_old_marketplace`, `_repair` (existing in the test module); `configure_omc`, `run_in` from `tests/e2e/harness.py`; `ensure_plugin` status `updated (a → b)` (Task 2).
- Produces: `_stale_marketplace(container, old_version: str) -> str` returning the checkout's current version; test `test_update_advances_an_installed_plugin`.

- [ ] **Step 1: Write the E2E**

Add to `tests/e2e/test_e2e_marketplace_repair.py` after `_repair`:

```python
_REWRITE = (
    "import re, sys; from pathlib import Path\n"
    "root, version = sys.argv[1], sys.argv[2]\n"
    "for rel in ('.claude-plugin/plugin.json', '.claude-plugin/marketplace.json'):\n"
    "    p = Path(root) / rel\n"
    "    p.write_text(re.sub(r'\"version\": *\"[^\"]*\"', '\"version\": \"' + version + '\"', p.read_text()))\n"
)


def _checkout_version(container) -> str:
    out = _run(container, ["python3", "-c", "import json; print(json.load(open('/repo/.claude-plugin/plugin.json'))['version'])"])
    return out.strip().splitlines()[-1]


def _stale_marketplace(container, old_version: str) -> str:
    """Install omc from a directory marketplace stamped ``old_version``, then
    move that marketplace to the checkout's version — the shape of 'the
    marketplace advanced after I installed'. Returns the checkout version."""
    configure_omc(container, "claude")
    _run(container, ["claude", "plugin", "marketplace", "remove", "oh-my-clanker"])
    _run(
        container,
        [
            "python3",
            "-c",
            "import shutil; from pathlib import Path; "
            "p=Path('/tmp/old-omc'); p.mkdir(); "
            "shutil.copytree('/repo/.claude-plugin', p/'.claude-plugin'); "
            "shutil.copytree('/repo/skills', p/'skills')",
        ],
    )
    _run(container, ["python3", "-c", _REWRITE, "/tmp/old-omc", old_version])
    _run(container, ["claude", "plugin", "marketplace", "add", "/tmp/old-omc"])
    _run(container, ["claude", "plugin", "install", "omc@oh-my-clanker", "--scope", "user"])
    current = _checkout_version(container)
    _run(container, ["python3", "-c", _REWRITE, "/tmp/old-omc", current])
    return current


def test_update_advances_an_installed_plugin(container):
    # The 2026-10-01 regression against the REAL CLI: omc installed and
    # healthy, the marketplace has moved on, `omc update`'s plugin step must
    # advance the installed version and say so.
    current = _stale_marketplace(container, "0.0.1")
    before = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    omc_before = next(p for p in before if p["id"] == "omc@oh-my-clanker")
    assert omc_before["version"] == "0.0.1", omc_before
    assert not omc_before.get("errors"), omc_before

    rc, out = _repair(container, "/tmp/old-omc")
    assert rc == 0, out
    assert f"updated (0.0.1 → {current})" in out, out
    after = json.loads(_run(container, ["claude", "plugin", "list", "--json"]))
    omc_after = next(p for p in after if p["id"] == "omc@oh-my-clanker")
    assert omc_after["version"] == current, omc_after
    assert omc_after["enabled"] and not omc_after.get("errors"), omc_after
    assert "grug" in _run(container, ["ls", omc_after["installPath"] + "/skills"])
```

- [ ] **Step 2: Move the Claude pin**

In `docker/Dockerfile.e2e`, replace the comment block that begins `# The supported provider CLIs.` through the line `ARG CLAUDE_VERSION=2.1.281` (leave the following `RUN npm install -g …` line untouched) with:

```dockerfile
# The supported provider CLIs. Pin Codex to the plugin commands verified in this
# harness; 0.144.5 only registered marketplaces and did not install plugins.
# Claude 2.1.281 enforces declared marketplace sources; 2.1.286 is the version on
# which `plugin list --available` was verified to EXCLUDE installed plugins
# (2026-10-01) — the behaviour omc's live-config proof must not depend on.
ARG CODEX_VERSION=0.156.1
ARG CLAUDE_VERSION=2.1.286
```

- [ ] **Step 3: Build the image and run the marketplace E2Es**

Run: `just e2e-tests tests/e2e/test_e2e_marketplace_repair.py -vv`
Expected: all five cases PASS (the four existing ones on 2.1.286 plus the new one). Requires Docker and network for the GitHub-source case; no model calls.

If `test_update_advances_an_installed_plugin` fails at `_repair` with Claude rejecting `claude plugin marketplace update oh-my-clanker` for a **directory** source, there is nothing to fetch for a directory marketplace. Add this guard in `_prepare_marketplace` immediately before `if not replaced:`:

```python
    if not replaced and current is not None and current.get("source") == "directory":
        # A directory marketplace is read in place: `marketplace update` has
        # nothing to fetch and claude <version observed> rejects it. The
        # plugin step still re-copies the directory's current manifest.
        _offered(ctx)
        return replaced
```

Replace `<version observed>` with the version the failure printed, add the Claude error text to the PLUGIN-NOTES entry (Step 4), and re-run the suite. If the test fails for another reason, stop and report the output; do not weaken the assertions.

- [ ] **Step 4: Record the evidence in `docker/PLUGIN-NOTES.md`**

Insert at the very top of the file:

```markdown
## Claude `--available` excludes installed plugins (2026-10-01)

Observed on the host with Claude 2.1.286: `claude plugin list --available
--json` returns `{"installed": [...], "available": [...]}` and the `available`
array lists only plugins that are NOT installed — the intersection with the
eight installed plugin ids was empty. omc's marketplace-source repair
(2026-09-25) proved "the marketplace offers omc" by looking for omc in that
array on the LIVE config, which can never succeed once omc is installed. The
update path therefore raised "refusing plugin replacement: marketplace does not
offer omc@oh-my-clanker" before `claude plugin update` ran, `omc update`
printed the error as a best-effort warning and exited 0, and the installed
plugin stayed at 0.1.11 while the marketplace clone sat at 0.1.13. The
symptom: the grug skill shipped in 0.1.13 never fired in any session.

Timeline from shell history and file timestamps: `omc update` at 08:57 local
refreshed the marketplace clone (known_marketplaces lastUpdated 11:57:56Z)
and Claude materialized a 0.1.13 cache directory one second later, but
`installed_plugins.json` kept its 2026-09-28 entry for 0.1.11.

The live-config proof now reads the registered marketplace's
`.claude-plugin/marketplace.json` at its `installLocation` (`_offered` in
`src/omc/plugin.py`); the isolated probe keeps using the available list, where
omc is legitimately not installed. `ensure_plugin` also compares the installed
version with the offered one and treats a difference as "stale": start,
configure and update heal it through the update sequence, and an update that
leaves the version unchanged raises instead of reporting "updated".

`tests/e2e/test_e2e_marketplace_repair.py::test_update_advances_an_installed_plugin`
covers the healthy-update path against the real CLI: install from a directory
marketplace stamped 0.0.1, rewrite it to the checkout version, run the update
path, assert the registry reports the new version and the cached payload
carries `skills/grug`. The E2E image pins Claude 2.1.286 for this behaviour.
```

If Step 3 required the directory-source guard, append one paragraph quoting Claude's error text for `marketplace update` on a directory source and the version observed.

- [ ] **Step 5: README wording**

In `README.md` line 30, change

```
   For Claude Code you can skip this table: `omc configure`, `omc update` and `omc start` all install (and repair) the plugin for you.
```

to

```
   For Claude Code you can skip this table: `omc configure`, `omc update` and `omc start` all install the plugin, repair one Claude refuses to load, and refresh one that fell behind the marketplace (`omc start --dry-run` reports `stale (<installed> → <offered> offered)` without touching anything).
```

- [ ] **Step 6: Run the quick gate**

Run: `just check`
Expected: exit 0 (the E2E is marked `e2e` and excluded here).

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_e2e_marketplace_repair.py docker/Dockerfile.e2e docker/PLUGIN-NOTES.md README.md src/omc/plugin.py
git commit -m "test: cover the healthy plugin update against real Claude 2.1.286 and record the available-list semantics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

(Drop `src/omc/plugin.py` from the `git add` if Step 3 needed no guard.)

---

## Self-review

**Spec coverage.** 4.1 → Task 1. 4.2, 4.3, 4.4 → Task 2 (start needs no code change; its tests pin the progress line and dry-run). 4.5, 4.6 → Task 3. 4.7 → Tasks 1, 2 (unit), 4 (E2E, pin). 4.8 → Task 4. Decision 7 (raise on an unmoved heal) → Task 2 `test_update_that_does_not_move_the_version_fails_loud`. Decision 8 (one E2E, string inequality) → Task 4 has one Docker case; `_skew` compares strings.

**Type consistency.** `_offered(ctx) -> str | None` is used by `_prepare_marketplace` (Task 1) and `_skew` (Task 2). `_skew(ctx, omc) -> tuple[str, str] | None` is used twice in `ensure_plugin`. Stub knobs `offers_omc`, `offered_version`, `update_moves_version` are spelled identically in Tasks 1, 2. `_remote(tmp_path)` is defined in both test modules with the same body (test modules do not import from each other).

**Review Focus.** All five lines have a named test in Tasks 1 and 2.
