import json

import pytest

from omc.notify import merge_claude_settings, wire_worktree
from omc.providers.registry import get_provider

OLD = "omc internal notify --provider claude"


@pytest.mark.parametrize("enabled,channel", [(True, "auto"), (False, "notifications_disabled")])
def test_native_channel_written_and_repeated_wiring_is_idempotent(tmp_path, enabled, channel):
    provider = get_provider("claude")
    assert wire_worktree(provider, tmp_path, enabled) == [".claude/settings.local.json"]
    path = tmp_path / ".claude/settings.local.json"
    assert json.loads(path.read_text()) == {"preferredNotifChannel": channel}
    assert wire_worktree(provider, tmp_path, enabled) == []


def test_merge_removes_exact_old_hooks_and_preserves_foreign_members():
    existing = {
        "theme": "dark",
        "hooks": {
            "Notification": [
                {
                    "matcher": "x",
                    "hooks": [
                        {"type": "command", "command": OLD},
                        {"type": "command", "command": OLD + " --extra"},
                        {"type": "prompt", "prompt": "keep"},
                    ],
                }
            ],
            "Stop": [{"hooks": [{"type": "command", "command": OLD}]}],
            "PreToolUse": [{"hooks": [{"type": "command", "command": OLD}]}],
        },
    }
    ours = '{"preferredNotifChannel":"auto"}'
    merged = merge_claude_settings(json.dumps(existing), ours)
    data = json.loads(merged)
    assert data["theme"] == "dark"
    assert data["preferredNotifChannel"] == "auto"
    assert data["hooks"]["Notification"] == [
        {
            "matcher": "x",
            "hooks": [
                {"type": "command", "command": OLD + " --extra"},
                {"type": "prompt", "prompt": "keep"},
            ],
        }
    ]
    assert data["hooks"]["Stop"] == []
    assert data["hooks"]["PreToolUse"] == existing["hooks"]["PreToolUse"]
    assert merge_claude_settings(merged, ours) == merged
    assert (
        json.loads(
            merge_claude_settings(merged, '{"preferredNotifChannel":"notifications_disabled"}')
        )["preferredNotifChannel"]
        == "notifications_disabled"
    )


@pytest.mark.parametrize(
    "bad",
    ["{", "[]", '{"hooks": []}', '{"hooks":{"Stop":{}}}', '{"hooks":{"Stop":[{"hooks":{}}]}}'],
)
def test_bad_settings_are_preserved(tmp_path, bad, capsys):
    path = tmp_path / ".claude/settings.local.json"
    path.parent.mkdir()
    path.write_text(bad)
    assert wire_worktree(get_provider("claude"), tmp_path, True) == []
    assert path.read_text() == bad
    assert "leaving it alone" in capsys.readouterr().err


def test_non_utf8_settings_are_preserved(tmp_path, capsys):
    path = tmp_path / ".claude/settings.local.json"
    path.parent.mkdir()
    path.write_bytes(b"\xff")
    assert wire_worktree(get_provider("claude"), tmp_path, True) == []
    assert path.read_bytes() == b"\xff"
    assert "could not write" in capsys.readouterr().err


def test_codex_has_no_worktree_file(tmp_path):
    assert wire_worktree(get_provider("codex"), tmp_path, False) == []
    assert list(tmp_path.iterdir()) == []


def test_settings_write_failure_warns_and_does_not_break_launch(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    target = tmp_path / ".claude/settings.local.json"
    target.parent.mkdir()
    target.write_text('{"preferredNotifChannel":"notifications_disabled"}\n')
    before = target.read_bytes()
    original = Path.write_text

    def fail_target(path, content, *args, **kwargs):
        if path.parent == target.parent:
            raise OSError("read only")
        return original(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_target)
    assert wire_worktree(get_provider("claude"), tmp_path, True) == []
    assert target.read_bytes() == before
    assert "could not write" in capsys.readouterr().err


def test_partial_settings_write_preserves_original_and_cleans_temp(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    target = tmp_path / ".claude/settings.local.json"
    target.parent.mkdir()
    original = (
        b'{"permissions":{"allow":["Read"]},"preferredNotifChannel":"notifications_disabled"}\n'
    )
    target.write_bytes(original)
    real_write = Path.write_text

    def partial_write(path, content, *args, **kwargs):
        if path.parent == target.parent:
            with path.open("w") as stream:
                stream.write(content[:10])
            raise OSError("disk full after partial write")
        return real_write(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", partial_write)
    assert wire_worktree(get_provider("claude"), tmp_path, True) == []
    assert target.read_bytes() == original
    assert list(target.parent.iterdir()) == [target]
    assert "could not write" in capsys.readouterr().err


def test_replace_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    target = tmp_path / ".claude/settings.local.json"
    target.parent.mkdir()
    original = b'{"permissions":{"allow":["Read"]}}\n'
    target.write_bytes(original)
    real_replace = Path.replace

    def fail_replace(path, destination):
        if destination == target:
            raise OSError("replace failed")
        return real_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_replace)
    assert wire_worktree(get_provider("claude"), tmp_path, True) == []
    assert target.read_bytes() == original
    assert list(target.parent.iterdir()) == [target]
    assert "could not write" in capsys.readouterr().err
