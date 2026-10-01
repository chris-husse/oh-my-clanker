"""Pure helpers of the private iTerm2 fixture: argv, parsing, env scoping (no app launched)."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.local.conftest import unmarked_local_items
from tests.local.private_iterm import (
    Identity,
    ProbeError,
    instance_pid,
    launch_env,
    leftover_domains,
    new_identity,
    open_argv,
    parse_pids,
    pids_with_env,
    preference_plists,
    scoped_env,
    sdk_env,
    server_processes,
)

CHECKOUT = Path("/Users/me/omc")


def test_identity_paths_are_short_and_private():
    identity = new_identity(CHECKOUT, "0123456789ab")
    assert identity.suite == "omc-0123456789ab"
    assert identity.root == Path("/private/tmp/omc-0123456789ab")
    assert identity.copy == identity.root / "iTerm.app"
    assert identity.home == identity.root / "home"
    assert identity.socket == (
        identity.home / "Library" / "Application Support" / identity.suite / "private" / "socket"
    )
    assert len(str(identity.socket).encode()) <= 103  # sockaddr_un limit on macOS


def test_identity_refuses_socket_paths_over_sockaddr_un():
    with pytest.raises(ProbeError, match="sockaddr_un"):
        new_identity(CHECKOUT, "x" * 60)


def test_open_argv_is_exact():
    identity = new_identity(CHECKOUT, "0123456789ab")
    assert open_argv(identity, "/opt/fish dir/bin/fish") == [
        "/usr/bin/open", "-n", "-g", "-j", "-a", "/private/tmp/omc-0123456789ab/iTerm.app",
        "--env", "HOME=/private/tmp/omc-0123456789ab/home",
        "--env", "CFFIXED_USER_HOME=/private/tmp/omc-0123456789ab/home",
        "--env", "XDG_CONFIG_HOME=/private/tmp/omc-0123456789ab/home/.config",
        "--env", "OMC_HOME=/private/tmp/omc-0123456789ab/home/.omc",
        "--env", "PATH=/Users/me/omc/.venv/bin:/usr/bin:/bin",
        "--env", "IT2_SUITE=omc-0123456789ab",
        "--env", "IT2_APP_PATH=/private/tmp/omc-0123456789ab/iTerm.app",
        "--args", "-suite", "omc-0123456789ab", "-EnableAPIServer", "YES",
        "-OpenNoWindowsAtStartup", "YES", "-openNewWindowAtStartup", "NO",
        "-runJobsInServers", "NO", "-SUEnableAutomaticChecks", "NO",
        "-SUHasLaunchedBefore", "YES", "-PromptOnQuit", "NO",
        "-moveToApplicationsFolderAlertSuppress", "YES",
        "-New Bookmarks",
        '({"Guid" = "omc-private-fish"; "Name" = "omc private fish"; "Custom Command" = "Yes"; '
        '"Command" = "\'/opt/fish dir/bin/fish\' -i"; "Run Command In Login Shell" = "No";})',
        "-Default Bookmark Guid", "omc-private-fish",
    ]  # fmt: skip


def test_launch_env_passes_nothing_from_the_runner(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "HOME"):
        monkeypatch.setenv(name, "runner-value")
    assert launch_env() == {"PATH": "/usr/bin:/bin"}


def test_sdk_env_scopes_only_the_three_sdk_variables():
    identity = new_identity(CHECKOUT, "0123456789ab")
    assert sdk_env(identity) == {
        "HOME": "/private/tmp/omc-0123456789ab/home",
        "IT2_SUITE": "omc-0123456789ab",
        "IT2_APP_PATH": "/private/tmp/omc-0123456789ab/iTerm.app",
    }


def test_parse_pids_ignores_blank_lines():
    assert parse_pids("123\n\n 456 \n") == [123, 456]
    assert parse_pids("") == []


def test_instance_pid_needs_exactly_one_process_inside_the_copy():
    copy = Path("/private/tmp/omc-0123456789ab/iTerm.app")
    inside = "/private/tmp/omc-0123456789ab/iTerm.app/Contents/MacOS/iTerm2 -suite omc-0123456789ab"
    outside = "/Applications/iTerm.app/Contents/MacOS/iTerm2 -suite omc-0123456789ab"
    assert instance_pid({7: inside, 8: outside}, copy) == 7
    with pytest.raises(ProbeError, match="no private iTerm2 process"):
        instance_pid({8: outside}, copy)
    with pytest.raises(ProbeError, match="2 processes"):
        instance_pid({7: inside, 9: inside}, copy)


def test_pids_with_env_matches_the_private_home_only():
    output = (
        "10 /bin/fish -i HOME=/private/tmp/omc-0123456789ab/home PATH=/usr/bin\n"
        "11 /bin/zsh HOME=/Users/me PATH=/usr/bin\n"
        "12 /usr/bin/env HOME=/private/tmp/omc-0123456789abcdef/home\n"
    )
    assert pids_with_env(output, "HOME=/private/tmp/omc-0123456789ab/home") == [10]


def test_server_processes_finds_the_private_daemonized_iterm_server():
    identity = new_identity(CHECKOUT, "0123456789ab")
    output = (
        "20 /private/tmp/omc-0123456789ab/iTerm.app/Contents/MacOS/iTerm2 -suite omc-0123456789ab\n"
        "21 /private/tmp/omc-0123456789ab/home/Library/Application Support/omc-0123456789ab/"
        "iTermServer-3.7.3 /private/tmp/omc-0123456789ab/home/Library/Application Support/"
        "omc-0123456789ab/iterm2-daemon-1.socket\n"
        "22 /opt/homebrew/bin/fish -i\n"
        "23 /Users/me/Library/Application Support/iTerm2/iTermServer-3.6.8\n"
    )
    assert server_processes(output, identity) == [21]
    assert server_processes("", identity) == []


def test_leftover_domains_reads_defaults_domains_output():
    output = (
        "com.apple.Terminal, omc-0123456789ab, omc-0123456789ab.private, com.googlecode.iterm2\n"
    )
    assert leftover_domains(output, "omc-0123456789ab") == [
        "omc-0123456789ab",
        "omc-0123456789ab.private",
    ]
    assert leftover_domains("com.apple.Terminal", "omc-0123456789ab") == []


def test_preference_plists_are_the_two_suite_files_in_the_real_preferences(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/me")
    identity = new_identity(CHECKOUT, "0123456789ab")
    assert preference_plists(identity) == [
        Path("/Users/me/Library/Preferences/omc-0123456789ab.plist"),
        Path("/Users/me/Library/Preferences/omc-0123456789ab.private.plist"),
    ]


def test_scoped_env_restores_and_unsets(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/me")
    monkeypatch.delenv("IT2_SUITE", raising=False)
    with scoped_env({"HOME": "/private/tmp/x/home", "IT2_SUITE": "omc-x"}):
        assert os.environ["HOME"] == "/private/tmp/x/home"
        assert os.environ["IT2_SUITE"] == "omc-x"
    assert os.environ["HOME"] == "/Users/me"
    assert "IT2_SUITE" not in os.environ


def test_scoped_env_restores_after_an_exception(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/me")
    with pytest.raises(RuntimeError):
        with scoped_env({"HOME": "/elsewhere"}):
            raise RuntimeError("boom")
    assert os.environ["HOME"] == "/Users/me"


def test_scoped_env_scrubs_variables_written_inside_the_block(monkeypatch):
    monkeypatch.delenv("ITERM2_COOKIE", raising=False)
    with scoped_env({"HOME": "/private/tmp/x/home"}, scrub=("ITERM2_COOKIE",)):
        os.environ["ITERM2_COOKIE"] = "issued-by-the-private-instance"
    assert "ITERM2_COOKIE" not in os.environ


def _item(path, nodeid, marked):
    return SimpleNamespace(
        path=path,
        nodeid=nodeid,
        get_closest_marker=lambda name: object() if marked and name == "local_iterm2" else None,
    )


def test_unmarked_local_items_names_only_unmarked_tests_under_tests_local(tmp_path):
    local = tmp_path / "tests" / "local"
    local.mkdir(parents=True)
    items = [
        _item(local / "test_a.py", "tests/local/test_a.py::test_ok", True),
        _item(local / "test_b.py", "tests/local/test_b.py::test_bad", False),
        _item(tmp_path / "tests" / "unit" / "test_c.py", "tests/unit/test_c.py::test_c", False),
    ]
    assert unmarked_local_items(items, local) == ["tests/local/test_b.py::test_bad"]


def test_identity_is_frozen():
    identity = new_identity(CHECKOUT, "0123456789ab")
    with pytest.raises(AttributeError):
        identity.suite = "other"  # type: ignore[misc]
    assert isinstance(identity, Identity)
