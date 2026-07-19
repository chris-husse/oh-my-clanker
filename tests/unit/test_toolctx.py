import sys

import pytest

from omc.toolctx import ToolContext, tool_version


def test_from_env_defaults(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    assert ctx.home == tmp_path / ".omc"
    assert ctx.git_bin == "git" and ctx.wt_bin == "wt" and ctx.uv_bin == "uv"


def test_from_env_overrides(tmp_path):
    env = {
        "OMC_HOME": str(tmp_path / "h"),
        "OMC_GIT_BIN": "/x/git",
        "OMC_WT_BIN": "/x/wt",
        "OMC_UV_BIN": "/x/uv",
        "UV_TOOL_DIR": "/x/uvt",
    }
    ctx = ToolContext.from_env(env)
    assert ctx.home == tmp_path / "h"
    assert (ctx.git_bin, ctx.wt_bin, ctx.uv_bin) == ("/x/git", "/x/wt", "/x/uv")
    assert ctx.uv_env == {"UV_TOOL_DIR": "/x/uvt"}
    assert ctx.child_env()["UV_TOOL_DIR"] == "/x/uvt"


def test_run_captures_and_detaches_stdin(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    cp = ctx.run([sys.executable, "-c", "print('hi')"])
    assert cp.returncode == 0 and cp.stdout.strip() == "hi"


def test_run_extra_env(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    cp = ctx.run(
        [sys.executable, "-c", "import os; print(os.environ.get('X_OMC', ''))"],
        extra_env={"X_OMC": "1"},
    )
    assert cp.stdout.strip() == "1"


def test_tool_version_hit_and_miss(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    ok, detail = tool_version(ctx, [sys.executable, "--version"])
    assert ok and "Python" in detail
    ok, detail = tool_version(ctx, ["/nonexistent-omc-bin", "--version"])
    assert not ok and "not found" in detail


def test_stream_delivers_whole_lines_from_both_streams(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    code = (
        "import sys\n"
        "print('out-one')\n"
        "print('err-one', file=sys.stderr)\n"
        "sys.stdout.write('out-')\n"  # partial write, completed next
        "sys.stdout.write('two\\n')\n"
        "sys.exit(7)\n"
    )
    lines: list[str] = []
    rc = ctx.stream([sys.executable, "-u", "-c", code], on_line=lines.append)
    assert rc == 7
    assert "out-one" in lines and "err-one" in lines and "out-two" in lines
    # per-stream order preserved: out-one before out-two
    assert lines.index("out-one") < lines.index("out-two")
    # lines are whole — the partial write never surfaced alone
    assert "out-" not in lines


def test_stream_stdin_is_devnull(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    lines: list[str] = []
    # reading stdin with DEVNULL sees EOF immediately instead of hanging
    code = "import sys; sys.stdout.write(sys.stdin.read())"
    rc = ctx.stream([sys.executable, "-c", code], on_line=lines.append)
    assert rc == 0
    assert lines == []


def test_stream_extra_env_reaches_child(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    lines: list[str] = []
    code = "import os; print('v=' + os.environ.get('OMC_STREAM_TEST', ''))"
    rc = ctx.stream(
        [sys.executable, "-c", code],
        on_line=lines.append,
        extra_env={"OMC_STREAM_TEST": "42"},
    )
    assert rc == 0
    assert lines == ["v=42"]


def test_stream_serializes_on_line_calls(tmp_path):
    """Concurrent stdout+stderr chatter never interleaves INSIDE a callback."""
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    code = (
        "import sys\n"
        "for i in range(50):\n"
        "    print(f'out-{i}')\n"
        "    print(f'err-{i}', file=sys.stderr)\n"
    )
    seen: list[str] = []
    in_callback = {"depth": 0, "max": 0}

    def on_line(line):
        in_callback["depth"] += 1
        in_callback["max"] = max(in_callback["max"], in_callback["depth"])
        seen.append(line)
        in_callback["depth"] -= 1

    rc = ctx.stream([sys.executable, "-u", "-c", code], on_line=on_line)
    assert rc == 0
    assert len(seen) == 100
    assert in_callback["max"] == 1  # never reentered concurrently


def test_stream_callback_exception_propagates(tmp_path):
    ctx = ToolContext.from_env({"HOME": str(tmp_path)})
    code = "print('one'); print('two'); print('three')"

    def boom(line):
        raise RuntimeError(f"callback failed on {line}")

    with pytest.raises(RuntimeError, match="callback failed on one"):
        ctx.stream([sys.executable, "-c", code], on_line=boom)
