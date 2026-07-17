import pytest

from omc.config.schema import Config
from omc.errors import OmcError
from omc.probe import require_tools, run_probes
from omc.toolctx import ToolContext

from ._stubs import make_stub, stub_env


def test_run_probes_parallel_mixed(tmp_path):
    bindir = tmp_path / "bin"
    make_stub(bindir, "git", stdout="git version 2.99")
    ctx = ToolContext.from_env(stub_env(bindir))
    results = run_probes(
        ctx,
        [
            ("git", ["git", "--version"], "install git"),
            ("wt", ["wt", "--version"], "cargo install worktrunk"),
        ],
    )
    by_name = {r.name: r for r in results}
    assert by_name["git"].present and "2.99" in by_name["git"].detail
    assert not by_name["wt"].present and by_name["wt"].hint == "cargo install worktrunk"


def test_require_tools_all_present(tmp_path):
    bindir = tmp_path / "bin"
    for name in ("git", "wt", "claude"):
        make_stub(bindir, name, stdout=f"{name} 1.0")
    ctx = ToolContext.from_env(stub_env(bindir))
    require_tools(ctx, Config())  # must not raise


def test_require_tools_lists_all_misses(tmp_path):
    bindir = tmp_path / "bin"
    make_stub(bindir, "git", stdout="git 2.99")
    ctx = ToolContext.from_env(stub_env(bindir))
    with pytest.raises(OmcError) as exc:
        require_tools(ctx, Config())
    msg = str(exc.value)
    assert "wt" in msg and "worktrunk" in msg
    assert "claude" in msg and "npm install -g @anthropic-ai/claude-code" in msg
    assert "  git:" not in msg  # present tools are not listed as missing


def test_make_stub_reproduces_shell_metacharacters(tmp_path):
    bindir = tmp_path / "bin"
    payload = 'OMC_SLUG {"ok": true, "slug": "a-b"} `id` $(echo NOPE) $HOME'
    make_stub(bindir, "meta", stdout=payload)
    ctx = ToolContext.from_env(stub_env(bindir))
    cp = ctx.run(["meta"])
    assert cp.stdout.strip() == payload
