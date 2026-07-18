from pathlib import Path

from omc.agentsmd import chain_healthy, distribution_agents_md, ensure_agents_chain
from omc.toolctx import ToolContext

V1_INTERNAL = Path(".omc/internal/AGENTS.md")


def _ctx(tmp_path):
    return ToolContext.from_env({"HOME": str(tmp_path)})


def test_distribution_agents_md_resolves_and_carries_the_layer():
    target = distribution_agents_md()
    assert target.is_file()
    text = target.read_text()
    assert ".omc/config/AGENTS.md" in text  # fans out to the project layer
    assert "rebase-main" in text and "OMC_" in text
    assert "subagent" in text.lower() and "efficient" in text  # model doctrine
    assert "omc update" in text  # header explains how the file updates


def test_chain_created_from_nothing(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "created"
    target = distribution_agents_md().resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        link = root / name
        assert link.is_symlink(), f"{name} must be a symlink"
        assert link.resolve() == target  # absolute link into the install
    assert not (root / V1_INTERNAL).exists()  # v1 layer is never created
    assert (root / ".omc" / "config" / "AGENTS.md").is_file()  # starter seeded
    gitignore = (root / ".gitignore").read_text()
    assert "/AGENTS.md" in gitignore and "/CLAUDE.md" in gitignore
    assert "AGENTS.md" in capsys.readouterr().err  # narrated
    assert chain_healthy(root)


def test_correct_chain_is_silent_and_idempotent(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    ensure_agents_chain(_ctx(tmp_path), root)
    project = root / ".omc" / "config" / "AGENTS.md"
    project.write_text("# my project rules\n")
    before = (root / ".gitignore").read_text()
    capsys.readouterr()
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "ok"
    assert project.read_text() == "# my project rules\n"  # NEVER overwritten
    assert (root / ".gitignore").read_text() == before  # no duplicate entries
    assert capsys.readouterr().err == ""  # healthy chain is quiet


def test_v1_chain_migrates_to_v2(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    internal = root / V1_INTERNAL
    internal.parent.mkdir(parents=True)
    internal.write_text("# omc behavior layer (generated)\n")
    (root / ".omc" / "config").mkdir(parents=True)
    (root / ".omc" / "config" / "AGENTS.md").write_text("# mine\n")
    for name in ("AGENTS.md", "CLAUDE.md"):
        (root / name).symlink_to(V1_INTERNAL)  # relative v1 links
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "created"
    target = distribution_agents_md().resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / name).resolve() == target
    assert not internal.exists()  # v1 file retired
    assert not internal.parent.exists()  # empty .omc/internal removed
    assert (root / ".omc" / "config" / "AGENTS.md").read_text() == "# mine\n"


def test_dangling_v2_link_is_repaired_not_blocked(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    gone = tmp_path / "old-venv" / "omc" / "distribution" / "AGENTS.md"
    for name in ("AGENTS.md", "CLAUDE.md"):
        (root / name).symlink_to(gone)  # previous install location, now deleted
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "created"
    target = distribution_agents_md().resolve()
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / name).resolve() == target


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
    assert not (root / "CLAUDE.md").exists()  # nothing half-created
    assert not (root / ".gitignore").exists()  # blocked mutates NOTHING


def test_foreign_symlink_is_warned_not_touched(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "other.md").write_text("x")
    (root / "AGENTS.md").symlink_to("other.md")
    status = ensure_agents_chain(_ctx(tmp_path), root)
    assert status == "blocked"
    assert (root / "AGENTS.md").resolve() == (root / "other.md").resolve()


def test_chain_healthy_is_a_cheap_read_only_probe(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert not chain_healthy(root)
    ensure_agents_chain(_ctx(tmp_path), root)
    assert chain_healthy(root)
    (root / "AGENTS.md").unlink()
    assert not chain_healthy(root)
