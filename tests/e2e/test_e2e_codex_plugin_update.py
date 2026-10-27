"""Codex update registry transitions with a private home and the real CLI."""

from __future__ import annotations

import json

import pytest

from .codex_plugin_update import (
    ENV,
    ROOT,
    create_fixtures,
    git_http_server,
    git_snapshot,
    install_sentinel,
    json_run,
    run,
    state,
    update,
)
from .harness import run_in

pytestmark = pytest.mark.e2e


def _assert_source_and_bytes(container, source, content, path):
    registration, listing = state(container)
    matches = [m for m in registration["marketplaces"] if m["name"] == "oh-my-clanker"]
    assert len(matches) == 1, registration
    assert matches[0]["marketplaceSource"]["source"] == source, registration
    installed = [p for p in listing["installed"] if p["pluginId"] == "omc@oh-my-clanker"]
    assert len(installed) == 1, listing
    assert installed[0]["installed"] and installed[0]["enabled"], installed[0]
    assert run(container, ["cat", path + "/skills/start/SKILL.md"]) == content + "\n"


def test_update_rebinds_receipt_source_and_installed_content(container):
    create_fixtures(container)
    source_a, source_b = f"{ROOT}/A", f"{ROOT}/B"
    run(container, ["codex", "plugin", "marketplace", "add", source_a, "--json"])
    installed = json_run(container, ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"])
    path = installed["installedPath"]
    _assert_source_and_bytes(container, source_a, "A CONTENT", path)

    rc, out = update(container, source_b)
    assert rc == 0, out
    _assert_source_and_bytes(container, source_b, "B CONTENT", path)
    rc, out = update(container, source_b)
    assert rc == 0, out
    _assert_source_and_bytes(container, source_b, "B CONTENT", path)


def test_update_repairs_unlistable_marketplace(container):
    create_fixtures(container)
    source_a, source_b = f"{ROOT}/A", f"{ROOT}/B"
    json_run(container, ["codex", "plugin", "marketplace", "add", source_a, "--json"])
    installed = json_run(container, ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"])
    sentinel_path = install_sentinel(container)
    run(container, ["rm", f"{source_a}/.claude-plugin/marketplace.json"])
    rc, out = run_in(container, ["codex", "plugin", "marketplace", "list", "--json"], env=ENV)
    assert rc != 0 and "supported manifest" in out, out

    rc, out = update(container, source_b)
    assert rc == 0, out
    _assert_source_and_bytes(container, source_b, "B CONTENT", installed["installedPath"])
    registration, listing = state(container)
    assert any(m["name"] == "sentinel" for m in registration["marketplaces"])
    assert any(p["pluginId"] == "sentinel@sentinel" and p["enabled"] for p in listing["installed"])
    assert run(container, ["cat", sentinel_path + "/skills/start/SKILL.md"]) == "SENTINEL CONTENT\n"


def test_update_refreshes_same_local_source(container):
    create_fixtures(container)
    source = f"{ROOT}/A"
    json_run(container, ["codex", "plugin", "marketplace", "add", source, "--json"])
    installed = json_run(container, ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"])
    path = installed["installedPath"]
    _assert_source_and_bytes(container, source, "A CONTENT", path)
    run(
        container,
        [
            "python3",
            "-c",
            "from pathlib import Path\n"
            "p = Path('/tmp/omc-codex-update/A/skills/start/SKILL.md')\n"
            "p.write_text('A ADVANCED\\n')",
        ],
    )
    rc, out = update(container, source)
    assert rc == 0, out
    _assert_source_and_bytes(container, source, "A ADVANCED", path)


def test_update_refreshes_git_snapshot_and_installed_content(container):
    create_fixtures(container)
    work = f"{ROOT}/A"
    source = "https://github.com/omc-test/update-fixture.git"
    run(
        container,
        [
            "python3",
            "-c",
            "from pathlib import Path; "
            f"p=Path({work!r})/'skills/obsolete/SKILL.md'; "
            "p.parent.mkdir(); p.write_text('OBSOLETE A\\n'); "
            f"(Path({work!r})/'skills/start/support.txt').write_text('SUPPORT A\\n')",
        ],
    )
    git_snapshot(container)

    with git_http_server(container):
        json_run(
            container,
            ["codex", "plugin", "marketplace", "add", "omc-test/update-fixture", "--json"],
        )
        first = json_run(container, ["codex", "plugin", "add", "omc@oh-my-clanker", "--json"])
        path = first["installedPath"]
        registration, _ = state(container)
        match = [m for m in registration["marketplaces"] if m["name"] == "oh-my-clanker"]
        assert len(match) == 1, registration
        snapshot = match[0]["root"]
        assert match[0]["marketplaceSource"]["source"].removesuffix(".git") == source.removesuffix(
            ".git"
        )
        assert run(container, ["cat", snapshot + "/skills/start/SKILL.md"]) == "A CONTENT\n"
        assert run(container, ["cat", path + "/skills/start/SKILL.md"]) == "A CONTENT\n"
        assert run(container, ["cat", path + "/skills/start/support.txt"]) == "SUPPORT A\n"
        assert run(container, ["cat", path + "/skills/obsolete/SKILL.md"]) == "OBSOLETE A\n"

        run(
            container,
            [
                "python3",
                "-c",
                "from pathlib import Path; "
                f"root=Path({work!r}); "
                "(root/'skills/start/SKILL.md').write_text('B CONTENT\\n'); "
                "(root/'skills/start/support.txt').write_text('SUPPORT B\\n'); "
                "(root/'skills/obsolete/SKILL.md').unlink(); "
                "p=root/'skills/new/SKILL.md'; p.parent.mkdir(); p.write_text('NEW B\\n')",
            ],
        )
        run(container, ["git", "-C", work, "add", "-A"])
        run(container, ["git", "-C", work, "commit", "-m", "version B"])
        run(container, ["git", "-C", work, "push", "origin", "HEAD:main"])
        run(container, ["git", "--git-dir", f"{ROOT}/origin.git", "update-server-info"])

        rc, out = update(container, source)
        assert rc == 0, out
        registration, listing = state(container)
        match = [m for m in registration["marketplaces"] if m["name"] == "oh-my-clanker"]
        assert len(match) == 1, registration
        assert match[0]["root"] == snapshot
        assert match[0]["marketplaceSource"]["source"].removesuffix(".git") == source.removesuffix(
            ".git"
        )
        installed = [p for p in listing["installed"] if p["pluginId"] == "omc@oh-my-clanker"]
        assert len(installed) == 1 and installed[0]["enabled"], listing
        for root in (snapshot, path):
            actual = run(container, ["cat", root + "/skills/start/SKILL.md"])
            if actual != "B CONTENT\n":
                _, origin_head = run_in(
                    container,
                    ["git", "--git-dir", f"{ROOT}/origin.git", "rev-parse", "HEAD"],
                    env=ENV,
                )
                _, snapshot_head = run_in(
                    container, ["git", "-C", snapshot, "rev-parse", "HEAD"], env=ENV
                )
                pytest.fail(
                    f"{root} stayed at {actual!r}; update={out!r}; "
                    f"origin HEAD={origin_head!r}; snapshot HEAD={snapshot_head!r}"
                )
            assert run(container, ["cat", root + "/skills/start/support.txt"]) == "SUPPORT B\n"
            assert run(container, ["cat", root + "/skills/new/SKILL.md"]) == "NEW B\n"
            rc, _ = run_in(container, ["test", "-e", root + "/skills/obsolete/SKILL.md"], env=ENV)
            assert rc != 0, root

        rc, out = update(container, source)
        assert rc == 0, out
        registration, listing = state(container)
        assert sum(m["name"] == "oh-my-clanker" for m in registration["marketplaces"]) == 1
        assert sum(p["pluginId"] == "omc@oh-my-clanker" for p in listing["installed"]) == 1
        assert run(container, ["cat", path + "/skills/start/SKILL.md"]) == "B CONTENT\n"
        copies = run(
            container,
            [
                "python3",
                "-c",
                "import json,sys; from pathlib import Path; "
                "p=Path(sys.argv[1]); "
                "print(json.dumps(sorted(x.name for x in p.parent.iterdir())))",
                path,
            ],
        )
        assert json.loads(copies) == [path.rsplit("/", 1)[-1]]
