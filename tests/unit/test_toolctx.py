import sys

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
