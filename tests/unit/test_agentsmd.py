import pytest

from omc import agentsmd
from omc.errors import OmcError
from omc.toolctx import ToolContext


def _ctx(tmp_path, **overrides):
    return ToolContext.from_env({"HOME": str(tmp_path), **overrides})


def _target(tmp_path, name="claude"):
    return tmp_path / (".claude/CLAUDE.md" if name == "claude" else ".codex/AGENTS.md")


def _section():
    return (
        agentsmd.BEGIN_MARKER
        + b"\n"
        + agentsmd.distribution_agents_md().read_bytes()
        + agentsmd.END_MARKER
        + b"\n"
    )


def test_distribution_layer_has_global_scope_and_project_pointer():
    target = agentsmd.distribution_agents_md()
    body = target.read_text()
    assert "This section is installed into your global instructions by omc" in body
    assert "contains an `.omc/` directory" in body
    assert "ignore everything in this section" in body
    assert "when the repository you are working in contains `.omc/config/AGENTS.md`" in " ".join(
        body.split()
    )
    assert "it takes precedence over this layer" in body
    assert "rebase-main" in body and "omc internal models" in body
    assert "OMC_MODELS" in body and "Orchestrator" in body


def test_absent_global_file_created_with_exact_inline_body(tmp_path):
    target = _target(tmp_path)
    assert agentsmd.ensure_global_section(_ctx(tmp_path), "claude") == "created"
    assert target.read_bytes() == _section()


@pytest.mark.parametrize(
    "original,separator",
    [
        (b"user", b"\n\n"),
        (b"user\n", b"\n"),
        (b"user\n\n", b""),
        (b"user\n\n\n", b""),
        (b"", b""),
        (b"\xff\r\n", b"\n"),
    ],
)
def test_append_preserves_foreign_bytes_with_one_separator(tmp_path, original, separator):
    target = _target(tmp_path)
    target.parent.mkdir()
    target.write_bytes(original)
    assert agentsmd.ensure_global_section(_ctx(tmp_path), "claude") == "created"
    assert target.read_bytes() == original + separator + _section()


def test_existing_section_replaced_in_place_preserves_surroundings_and_mode(tmp_path):
    target = _target(tmp_path)
    target.parent.mkdir()
    before = b"\xff pref\r\n\r\n"
    after = b"\r\nuser suffix\xfe\n"
    target.write_bytes(before + agentsmd.BEGIN_MARKER + b"\nold\n" + agentsmd.END_MARKER + after)
    target.chmod(0o600)
    assert agentsmd.ensure_global_section(_ctx(tmp_path), "claude") == "updated"
    assert target.read_bytes() == before + _section() + after
    assert target.stat().st_mode & 0o777 == 0o600


def test_current_section_does_not_change_bytes_or_mtime(tmp_path):
    target = _target(tmp_path)
    agentsmd.ensure_global_section(_ctx(tmp_path), "claude")
    before, mtime = target.read_bytes(), target.stat().st_mtime_ns
    assert agentsmd.ensure_global_section(_ctx(tmp_path), "claude") == "current"
    assert (target.read_bytes(), target.stat().st_mtime_ns) == (before, mtime)


@pytest.mark.parametrize(
    "body",
    [
        b"BEGIN",
        b"END",
        b"BEGIN\nBEGIN\nEND",
        b"BEGIN\nEND\nEND",
        b"END\nBEGIN",
    ],
)
def test_malformed_markers_raise_and_leave_bytes_untouched(tmp_path, body):
    target = _target(tmp_path)
    target.parent.mkdir()
    original = body.replace(b"BEGIN", agentsmd.BEGIN_MARKER).replace(b"END", agentsmd.END_MARKER)
    target.write_bytes(original)
    with pytest.raises(OmcError, match=r"CLAUDE.md.*marker"):
        agentsmd.ensure_global_section(_ctx(tmp_path), "claude")
    assert target.read_bytes() == original
    with pytest.raises(OmcError, match=r"CLAUDE.md.*marker"):
        agentsmd.remove_global_section(_ctx(tmp_path), "claude")
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "name,override,filename",
    [
        ("claude", "CLAUDE_CONFIG_DIR", "CLAUDE.md"),
        ("codex", "CODEX_HOME", "AGENTS.md"),
    ],
)
def test_provider_override_uses_context_environment(tmp_path, name, override, filename):
    custom = tmp_path / "custom"
    agentsmd.ensure_global_section(_ctx(tmp_path, **{override: str(custom)}), name)
    assert (custom / filename).read_bytes() == _section()
    assert not _target(tmp_path, name).exists()


def test_global_symlink_refused_without_changing_link_or_target(tmp_path):
    target = _target(tmp_path)
    target.parent.mkdir()
    personal = tmp_path / "personal.md"
    personal.write_bytes(b"mine\n")
    target.symlink_to(personal)
    with pytest.raises(OmcError, match="symlink"):
        agentsmd.ensure_global_section(_ctx(tmp_path), "claude")
    assert target.is_symlink() and target.resolve() == personal
    assert personal.read_bytes() == b"mine\n"
    with pytest.raises(OmcError, match="symlink"):
        agentsmd.remove_global_section(_ctx(tmp_path), "claude")
    assert target.is_symlink() and personal.read_bytes() == b"mine\n"


def test_project_seed_only_if_absent_and_ignores_root_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"project root\n")
    (root / ".gitignore").write_bytes(b"custom\n")
    agentsmd.seed_project_agents_md(root)
    seeded = root / ".omc/config/AGENTS.md"
    assert "root AGENTS.md/CLAUDE.md symlinks" not in seeded.read_text()
    seeded.write_bytes(b"personal project rules\n")
    agentsmd.seed_project_agents_md(root)
    assert seeded.read_bytes() == b"personal project rules\n"
    assert (root / "AGENTS.md").read_bytes() == b"project root\n"
    assert (root / ".gitignore").read_bytes() == b"custom\n"


@pytest.mark.parametrize(
    "prefix,suffix,expected",
    [
        (b"user", b"", b"user"),
        (b"user\n\n", b"", b"user\n"),
        (b"", b"\n\nuser", b"user"),
        (b"before\n\n", b"\n\nafter", b"before\n\nafter"),
    ],
)
def test_remove_owned_span_and_one_adjacent_blank_separator(tmp_path, prefix, suffix, expected):
    target = _target(tmp_path)
    target.parent.mkdir()
    target.write_bytes(prefix + _section() + suffix)
    note = agentsmd.remove_global_section(_ctx(tmp_path), "claude")
    assert note and "CLAUDE.md" in note
    assert target.read_bytes() == expected


def test_configure_uninstall_round_trip_preserves_user_final_newline(tmp_path):
    target = _target(tmp_path)
    target.parent.mkdir()
    original = b"# My instructions\nKeep this line.\n"
    target.write_bytes(original)
    agentsmd.ensure_global_section(_ctx(tmp_path), "claude")
    assert agentsmd.remove_global_section(_ctx(tmp_path), "claude")
    assert target.read_bytes() == original


def test_configure_uninstall_normalizes_unterminated_user_text(tmp_path):
    target = _target(tmp_path)
    target.parent.mkdir()
    target.write_bytes(b"user")
    agentsmd.ensure_global_section(_ctx(tmp_path), "claude")
    assert agentsmd.remove_global_section(_ctx(tmp_path), "claude")
    assert target.read_bytes() == b"user\n"


def test_remove_section_only_deletes_file(tmp_path):
    agentsmd.ensure_global_section(_ctx(tmp_path), "claude")
    assert agentsmd.remove_global_section(_ctx(tmp_path), "claude")
    assert not _target(tmp_path).exists()


def test_remove_deletes_file_when_only_whitespace_surrounds_section(tmp_path):
    target = _target(tmp_path)
    target.parent.mkdir()
    target.write_bytes(b" \n" + _section() + b"\t\n")
    assert agentsmd.remove_global_section(_ctx(tmp_path), "claude")
    assert not target.exists()


def test_remove_foreign_only_or_absent_file_leaves_it_alone(tmp_path):
    target = _target(tmp_path)
    assert agentsmd.remove_global_section(_ctx(tmp_path), "claude") is None
    target.parent.mkdir()
    target.write_bytes(b"mine\n")
    assert agentsmd.remove_global_section(_ctx(tmp_path), "claude") is None
    assert target.read_bytes() == b"mine\n"


def test_atomic_write_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    target = _target(tmp_path)
    target.parent.mkdir()
    target.write_bytes(b"foreign instructions\n")

    def refuse_replace(source, destination):
        raise OSError("rename failed")

    monkeypatch.setattr(agentsmd.os, "replace", refuse_replace)
    with pytest.raises(OmcError, match=r"CLAUDE.md.*rename failed"):
        agentsmd.ensure_global_section(_ctx(tmp_path), "claude")
    assert target.read_bytes() == b"foreign instructions\n"
    assert list(target.parent.iterdir()) == [target]
