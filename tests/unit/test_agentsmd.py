from pathlib import Path

from omc.agentsmd import ensure_agents_chain
from omc.toolctx import ToolContext

INTERNAL = Path(".omc/internal/AGENTS.md")


def _ctx(tmp_path):
    return ToolContext.from_env({"HOME": str(tmp_path)})


def test_chain_created_from_nothing(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "created"
    internal = root / INTERNAL
    assert internal.is_file()
    text = internal.read_text()
    assert ".omc/config/AGENTS.md" in text  # fans out to the project layer
    assert "rebase-main" in text and "OMC_" in text
    assert (root / ".omc" / "config" / "AGENTS.md").is_file()  # starter seeded
    for name in ("AGENTS.md", "CLAUDE.md"):
        link = root / name
        assert link.is_symlink(), f"{name} must be a symlink"
        assert link.resolve() == internal.resolve()
    assert "AGENTS.md" in capsys.readouterr().err  # narrated


def test_correct_chain_is_silent_but_internal_regenerates(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    ensure_agents_chain(_ctx(tmp_path), root)
    capsys.readouterr()
    # omc owns the internal file: a stray edit does not survive
    (root / INTERNAL).write_text("tampered")
    project = root / ".omc" / "config" / "AGENTS.md"
    project.write_text("# my project rules\n")
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "ok"
    assert "tampered" not in (root / INTERNAL).read_text()
    assert project.read_text() == "# my project rules\n"  # NEVER overwritten
    assert capsys.readouterr().err == ""  # healthy chain is quiet


def test_regular_root_file_is_never_replaced(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "AGENTS.md").write_text("# handwritten\n")
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "blocked"
    assert not (root / "AGENTS.md").is_symlink()
    assert (root / "AGENTS.md").read_text() == "# handwritten\n"
    err = capsys.readouterr().err
    assert ".omc/config/AGENTS.md" in err  # migration steps named
    # CLAUDE.md was still not created behind the user's back in a blocked state
    assert not (root / "CLAUDE.md").exists()


def test_wrong_symlink_is_warned_not_touched(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "other.md").write_text("x")
    (root / "AGENTS.md").symlink_to("other.md")
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "blocked"
    assert (root / "AGENTS.md").resolve() == (root / "other.md").resolve()
