"""The REAL first run: CLI installed via uv, plugin never installed.

The container fixture pre-installs the plugin (setup-plugins.sh), which is
exactly why this path went untested — so this test removes the plugin AND the
marketplace to recreate a fresh user, then demands `omc start` just work."""

from __future__ import annotations

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in, wire_mcp

pytestmark = pytest.mark.e2e


def test_fresh_user_start_self_heals_missing_plugin(container):
    require_token("claude")
    configure_omc(container, "claude")
    wire_mcp(container, "claude", "ok")

    # Recreate the fresh user: no omc plugin, no omc marketplace.
    run_in(container, ["claude", "plugin", "uninstall", "omc@oh-my-clanker"])
    run_in(container, ["claude", "plugin", "marketplace", "remove", "oh-my-clanker"])
    rc, listed = run_in(container, ["claude", "plugin", "list"])
    assert "omc@" not in listed, f"fresh-user setup failed:\n{listed[:600]}"

    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "start", "PROJ-1", "--headless"], cwd=repo, timeout=900)
    assert "Unknown command" not in out, (
        f"seeded /omc:start did not resolve — the exact first-run bug:\n{out[:2000]}"
    )
    assert rc == 0, out

    # the self-heal must have installed the plugin durably
    rc, listed = run_in(container, ["claude", "plugin", "list"])
    assert rc == 0 and "omc@" in listed, f"plugin not installed after self-heal:\n{listed[:600]}"
