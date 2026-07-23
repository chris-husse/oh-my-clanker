import argparse
import json as _json
import sys

from omc import notify
from omc.config import store
from omc.config.schema import Config, GlobalConfig
from omc.providers.registry import get_provider
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env


def _ctx(tmp_path, **env):
    bindir = tmp_path / "bin"
    make_stub(bindir, "osascript")
    return ToolContext.from_env(stub_env(bindir, **env))


def _file_cfg(path):
    cfg = GlobalConfig()
    cfg.notifications.enabled = True
    cfg.notifications.backend = f"file://{path}"
    return cfg


def test_sink_argv():
    assert notify.sink_argv("claude") == ["omc", "internal", "notify", "--provider", "claude"]


def test_payload_from_claude():
    ev, body = notify.payload_from_claude(
        '{"hook_event_name": "Notification", "message": "Claude needs your permission"}'
    )
    assert (ev, body) == ("Notification", "Claude needs your permission")
    assert notify.payload_from_claude('{"hook_event_name": "Stop"}') == ("Stop", "turn complete")
    # malformed payloads degrade, never raise
    assert notify.payload_from_claude("{nope") == ("unknown", notify.GENERIC_BODY)
    assert notify.payload_from_claude("") == ("unknown", notify.GENERIC_BODY)
    assert notify.payload_from_claude("[1]") == ("unknown", notify.GENERIC_BODY)


def test_payload_from_codex():
    ev, body = notify.payload_from_codex('{"type": "agent-turn-complete"}')
    assert (ev, body) == ("agent-turn-complete", "turn complete")
    assert notify.payload_from_codex(None) == ("unknown", notify.GENERIC_BODY)
    assert notify.payload_from_codex("junk") == ("unknown", notify.GENERIC_BODY)


def test_file_backend_appends_formatted_line(tmp_path):
    log = tmp_path / "n.log"
    ctx = _ctx(tmp_path, OMC_SLUG="proj-1-fix")
    notify.deliver(
        _file_cfg(log),
        ctx=ctx,
        provider="claude",
        event="Notification",
        body="needs permission",
        cwd=str(tmp_path),
    )
    notify.deliver(
        _file_cfg(log),
        ctx=ctx,
        provider="codex",
        event="agent-turn-complete",
        body="turn complete",
        cwd=str(tmp_path),
    )
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    ts, slug, provider, event, body = lines[0].split("\t")
    assert slug == "proj-1-fix" and provider == "claude"
    assert event == "Notification" and body == "needs permission"
    assert "T" in ts  # ISO-8601-ish timestamp
    assert lines[1].split("\t")[2:] == ["codex", "agent-turn-complete", "turn complete"]


def test_file_backend_escapes_control_chars(tmp_path):
    log = tmp_path / "n.log"
    ctx = _ctx(tmp_path, OMC_SLUG="s")
    notify.deliver(
        _file_cfg(log),
        ctx=ctx,
        provider="claude",
        event="Notification",
        body="line1\nline2\tcol",
        cwd=str(tmp_path),
    )
    (line,) = log.read_text().splitlines()
    assert line.split("\t")[4] == "line1 line2 col"  # body stays ONE column


def test_file_backend_slug_falls_back_to_cwd_basename(tmp_path):
    log = tmp_path / "n.log"
    ctx = _ctx(tmp_path)  # no OMC_SLUG in env
    wt = tmp_path / "repo.feature-x"
    wt.mkdir()
    notify.deliver(
        _file_cfg(log),
        ctx=ctx,
        provider="claude",
        event="e",
        body="b",
        cwd=str(wt),
    )
    assert log.read_text().split("\t")[1] == "repo.feature-x"


def test_file_backend_failure_is_silent_no_raise(tmp_path, capsys):
    cfg = _file_cfg(tmp_path / "missing-dir" / "n.log")  # parent doesn't exist
    notify.deliver(
        cfg,
        ctx=_ctx(tmp_path),
        provider="claude",
        event="e",
        body="b",
        cwd=str(tmp_path),
    )  # must not raise
    assert "cannot append" in capsys.readouterr().err


def test_macos_backend_runs_osascript_on_darwin_only(tmp_path, monkeypatch):
    calls = []

    class FakeCtx:
        env = {"OMC_SLUG": "s-1"}

        def run(self, argv, **kwargs):
            calls.append(list(argv))

    cfg = Config()
    cfg.notifications.enabled = True  # backend stays "macos"
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(
        cfg,
        ctx=FakeCtx(),
        provider="claude",
        event="e",
        body='say "hi" \\ there',
        cwd=str(tmp_path),
    )
    (argv,) = calls
    assert argv[0] == "osascript" and argv[1] == "-e"
    # body/title are escaped AppleScript string literals — quotes/backslashes are data
    assert argv[2] == ('display notification "say \\"hi\\" \\\\ there" with title "omc: s-1"')
    monkeypatch.setattr(sys, "platform", "linux")
    notify.deliver(cfg, ctx=FakeCtx(), provider="claude", event="e", body="b", cwd=str(tmp_path))
    assert len(calls) == 1  # non-darwin: no-op


def test_macos_backend_oserror_is_swallowed(tmp_path, monkeypatch):
    class BrokenCtx:
        env = {}

        def run(self, argv, **kwargs):
            raise OSError("no osascript")

    cfg = Config()
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(
        cfg, ctx=BrokenCtx(), provider="claude", event="e", body="b", cwd=str(tmp_path)
    )  # must not raise


def test_macos_backend_timeout_is_swallowed(tmp_path, monkeypatch):
    import subprocess as sp

    class HangCtx:
        env = {}

        def run(self, argv, **kwargs):
            assert kwargs.get("timeout") == 10  # RED: no timeout passed today
            raise sp.TimeoutExpired(argv, 10)

    cfg = Config()
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(
        cfg, ctx=HangCtx(), provider="claude", event="e", body="b", cwd=str(tmp_path)
    )  # must not raise


def test_macos_backend_cleans_control_chars(tmp_path, monkeypatch):
    calls = []

    class FakeCtx:
        env = {"OMC_SLUG": "s-1"}

        def run(self, argv, **kwargs):
            calls.append(list(argv))

    cfg = Config()
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.deliver(
        cfg, ctx=FakeCtx(), provider="claude", event="e", body="line1\nline2", cwd=str(tmp_path)
    )
    (argv,) = calls
    assert argv[2] == 'display notification "line1 line2" with title "omc: s-1"'


def _notify_args(provider, payload=None, event="", message=""):
    return argparse.Namespace(provider=provider, payload=payload, event=event, message=message)


def _saved_cfg(home, *, enabled, log):
    cfg = GlobalConfig()
    cfg.notifications.enabled = enabled
    cfg.notifications.backend = f"file://{log}"
    store.save_global(home, cfg)


def test_run_notify_disabled_is_silent_kill_switch(tmp_path):
    home = tmp_path / "home"
    log = tmp_path / "n.log"
    _saved_cfg(home, enabled=False, log=log)
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path)})
    rc = notify.run_notify(ctx, _notify_args("codex", payload='{"type":"agent-turn-complete"}'))
    assert rc == 0
    assert not log.exists()  # disabled config silences even wired worktrees


def test_run_notify_codex_and_opencode_paths(tmp_path):
    home = tmp_path / "home"
    log = tmp_path / "n.log"
    _saved_cfg(home, enabled=True, log=log)
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path), "OMC_SLUG": "s-9"})
    assert (
        notify.run_notify(
            ctx, _notify_args("codex", payload=_json.dumps({"type": "agent-turn-complete"}))
        )
        == 0
    )
    assert (
        notify.run_notify(
            ctx, _notify_args("opencode", event="session.idle", message="session ready")
        )
        == 0
    )
    lines = [ln.split("\t") for ln in log.read_text().splitlines()]
    assert [ln[2:] for ln in lines] == [
        ["codex", "agent-turn-complete", "turn complete"],
        ["opencode", "session.idle", "session ready"],
    ]
    assert all(ln[1] == "s-9" for ln in lines)


def test_run_notify_claude_reads_stdin(tmp_path, monkeypatch):
    import io

    home = tmp_path / "home"
    log = tmp_path / "n.log"
    _saved_cfg(home, enabled=True, log=log)
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path), "OMC_SLUG": "s"})
    stdin = io.StringIO('{"hook_event_name": "Notification", "message": "needs permission"}')
    stdin.isatty = lambda: False
    monkeypatch.setattr(sys, "stdin", stdin)
    assert notify.run_notify(ctx, _notify_args("claude")) == 0
    assert log.read_text().split("\t")[3:] == ["Notification", "needs permission\n"]


def test_run_notify_survives_broken_or_missing_config(tmp_path):
    home = tmp_path / "home"
    ctx = ToolContext.from_env({"OMC_HOME": str(home), "HOME": str(tmp_path)})
    assert notify.run_notify(ctx, _notify_args("claude")) == 0  # no config at all
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("{broken")
    assert notify.run_notify(ctx, _notify_args("claude")) == 0  # ConfigError swallowed


def _wire(tmp_path, provider_name="claude"):
    return notify.wire_worktree(get_provider(provider_name), tmp_path)


def test_wire_worktree_writes_fresh_files(tmp_path):
    written = _wire(tmp_path)
    assert written == [".claude/settings.local.json"]
    settings = _json.loads((tmp_path / ".claude/settings.local.json").read_text())
    assert "Notification" in settings["hooks"] and "Stop" in settings["hooks"]
    assert _wire(tmp_path, "opencode") == [".opencode/plugin/omc-notify.js"]
    assert _wire(tmp_path, "codex") == []  # argv-wired, no files


def test_wire_worktree_merges_existing_claude_settings(tmp_path):
    target = tmp_path / ".claude" / "settings.local.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        _json.dumps(
            {
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {"Notification": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]},
            }
        )
    )
    assert _wire(tmp_path) == [".claude/settings.local.json"]
    merged = _json.loads(target.read_text())
    assert merged["permissions"] == {"allow": ["Bash(ls:*)"]}  # foreign keys survive
    cmds = [h["command"] for g in merged["hooks"]["Notification"] for h in g["hooks"]]
    assert cmds == ["mine.sh", "omc internal notify --provider claude"]
    assert [h["command"] for g in merged["hooks"]["Stop"] for h in g["hooks"]] == [
        "omc internal notify --provider claude"
    ]


def test_wire_worktree_is_idempotent(tmp_path):
    _wire(tmp_path)
    first = (tmp_path / ".claude/settings.local.json").read_text()
    assert _wire(tmp_path) == []  # nothing to change on the second run
    assert (tmp_path / ".claude/settings.local.json").read_text() == first


def test_wire_worktree_leaves_corrupt_settings_alone(tmp_path, capsys):
    target = tmp_path / ".claude" / "settings.local.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json")
    assert _wire(tmp_path) == []
    assert target.read_text() == "{not json"  # untouched
    assert "leaving it alone" in capsys.readouterr().err


def test_wire_worktree_leaves_foreign_plugin_alone(tmp_path, capsys):
    target = tmp_path / ".opencode" / "plugin" / "omc-notify.js"
    target.parent.mkdir(parents=True)
    target.write_text("// the user's own plugin\n")
    assert _wire(tmp_path, "opencode") == []
    assert target.read_text() == "// the user's own plugin\n"
    assert "leaving it alone" in capsys.readouterr().err


def test_wire_worktree_upgrades_own_stale_plugin(tmp_path):
    target = tmp_path / ".opencode" / "plugin" / "omc-notify.js"
    target.parent.mkdir(parents=True)
    target.write_text("// generated by omc — idle notifications (OLD template)\nold body\n")
    assert _wire(tmp_path, "opencode") == [".opencode/plugin/omc-notify.js"]
    assert "session.idle" in target.read_text()  # upgraded to current template


def test_wire_worktree_survives_non_utf8_settings(tmp_path, capsys):
    target = tmp_path / ".claude" / "settings.local.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff\xfe{not utf8")
    assert _wire(tmp_path) == []  # must not raise
    assert target.read_bytes() == b"\xff\xfe{not utf8"  # untouched
    assert "could not write" in capsys.readouterr().err
