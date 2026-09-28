import subprocess
import sys

from omc.toolctx import ToolContext


def test_module_argv_uses_current_interpreter():
    from omc.terminal_title import terminal_title_argv

    assert terminal_title_argv() == [sys.executable, "-m", "omc.terminal_title"]


def test_portable_title_is_written(capsys, tmp_path):
    from omc.terminal_title import run_title

    ctx = ToolContext(home=tmp_path, env={})
    assert run_title(ctx, "feature/title") == 0
    assert capsys.readouterr().out == "\033]0;feature/title\007"


def test_control_characters_are_rejected_without_terminal_write(capsys, tmp_path):
    from omc.terminal_title import run_title

    ctx = ToolContext(home=tmp_path, env={})
    assert run_title(ctx, "bad\007title") == 1
    result = capsys.readouterr()
    assert result.out == ""
    assert "invalid title" in result.err


def test_c1_control_character_is_rejected_without_terminal_write(capsys, tmp_path):
    from omc.terminal_title import run_title

    ctx = ToolContext(home=tmp_path, env={})
    assert run_title(ctx, "bad\x9dtitle") == 1
    assert capsys.readouterr().out == ""


def test_iterm_adapter_invokes_exact_worker_and_falls_back(capsys, monkeypatch, tmp_path):
    from omc.terminal_title import run_title

    session_id = "38B11221-B7E1-4F36-8A3B-50D549172632"
    ctx = ToolContext(
        home=tmp_path, env={"TERM_PROGRAM": "iTerm.app", "ITERM_SESSION_ID": f"w0t1p0:{session_id}"}
    )
    calls = []

    def fake_run(argv, *, timeout, cwd=None, extra_env=None):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, 7, "secret-stdout", "secret-auth-value")

    monkeypatch.setattr(ctx, "run_bounded", fake_run)
    assert run_title(ctx, "feature/name") == 1
    assert calls == [
        (
            [
                sys.executable,
                "-m",
                "omc.iterm2_title",
                "--session-id",
                session_id,
                "--",
                "feature/name",
            ],
            5,
        )
    ]
    result = capsys.readouterr()
    assert result.out == "\033]0;feature/name\007"
    assert "cannot pin" in result.err
    assert "secret" not in result.err


def test_iterm_adapter_success_emits_no_fallback_or_warning(capsys, monkeypatch, tmp_path):
    from omc.terminal_title import run_title

    ctx = ToolContext(
        home=tmp_path,
        env={
            "TERM_PROGRAM": "iTerm.app",
            "ITERM_SESSION_ID": "38B11221-B7E1-4F36-8A3B-50D549172632",
        },
    )
    monkeypatch.setattr(
        ctx,
        "run_bounded",
        lambda argv, *, timeout: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert run_title(ctx, "feature/name") == 0
    assert capsys.readouterr() == ("", "")


def test_malformed_session_id_never_starts_worker(capsys, monkeypatch, tmp_path):
    from omc.terminal_title import run_title

    ctx = ToolContext(home=tmp_path, env={"TERM_PROGRAM": "iTerm.app", "ITERM_SESSION_ID": "bad"})
    monkeypatch.setattr(
        ctx, "run_bounded", lambda *a, **k: (_ for _ in ()).throw(AssertionError("worker started"))
    )
    assert run_title(ctx, "feature/name") == 1
    assert capsys.readouterr().out == "\033]0;feature/name\007"


def test_malformed_iterm_prefix_never_starts_worker(capsys, monkeypatch, tmp_path):
    from omc.terminal_title import run_title

    ctx = ToolContext(
        home=tmp_path,
        env={
            "TERM_PROGRAM": "iTerm.app",
            "ITERM_SESSION_ID": "unrelated:38B11221-B7E1-4F36-8A3B-50D549172632",
        },
    )
    calls = []
    monkeypatch.setattr(ctx, "run_bounded", lambda *a, **k: calls.append(a))
    assert run_title(ctx, "feature/name") == 1
    assert calls == []
    assert capsys.readouterr().out == "\033]0;feature/name\007"


def test_worker_timeout_falls_back(capsys, monkeypatch, tmp_path):
    from omc.terminal_title import run_title

    ctx = ToolContext(
        home=tmp_path,
        env={
            "TERM_PROGRAM": "iTerm.app",
            "ITERM_SESSION_ID": "38B11221-B7E1-4F36-8A3B-50D549172632",
        },
    )
    monkeypatch.setattr(ctx, "run_bounded", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert run_title(ctx, "feature/name") == 1
    result = capsys.readouterr()
    assert result.out == "\033]0;feature/name\007"
    assert "cannot pin" in result.err
