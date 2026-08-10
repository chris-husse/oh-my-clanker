import os
import shutil

import pytest

from omc.errors import OmcError
from omc.mirror import mirror_dir, mirror_snapshot


def test_mirror_dir_copies_nested_and_deletes_extraneous(tmp_path):
    src = tmp_path / "src"
    (src / "deep").mkdir(parents=True)
    (src / "a.md").write_text("A")
    (src / "deep" / "b.md").write_text("B")
    dst = tmp_path / "dst"
    (dst / "stale").mkdir(parents=True)
    (dst / "stale" / "old.md").write_text("OLD")
    (dst / "extraneous.md").write_text("X")

    mirror_dir(src, dst)

    assert (dst / "a.md").read_text() == "A"
    assert (dst / "deep" / "b.md").read_text() == "B"
    assert not (dst / "extraneous.md").exists()  # rsync --delete semantics
    assert not (dst / "stale").exists()


def test_mirror_snapshot_syncs_known_dirs_only(tmp_path):
    primary = tmp_path / "primary"
    wt = tmp_path / "wt"
    (primary / ".gitnexus").mkdir(parents=True)
    (primary / ".gitnexus" / "graph.db").write_text("db")
    (primary / ".omc" / "docs").mkdir(parents=True)
    (primary / ".omc" / "docs" / "page.md").write_text("docs")
    (primary / ".env").write_text("SECRET")  # NOT part of the snapshot mirror
    wt.mkdir()
    (wt / ".gitnexus").mkdir()
    (wt / ".gitnexus" / "stale.db").write_text("stale")

    result = mirror_snapshot(primary, wt)

    assert result.synced == [".gitnexus", ".omc/docs"]
    assert result.shared == []
    assert (wt / ".gitnexus" / "graph.db").read_text() == "db"
    assert not (wt / ".gitnexus" / "stale.db").exists()
    assert (wt / ".omc" / "docs" / "page.md").read_text() == "docs"
    assert not (wt / ".env").exists()


def test_mirror_snapshot_skips_missing_sources(tmp_path):
    primary = tmp_path / "primary"
    wt = tmp_path / "wt"
    (primary / ".gitnexus").mkdir(parents=True)
    wt.mkdir()
    result = mirror_snapshot(primary, wt)
    assert result.synced == [".gitnexus"]  # no .omc/docs -> skipped entirely
    assert result.shared == []


def test_mirror_snapshot_refuses_same_root(tmp_path):
    root = tmp_path / "r"
    (root / ".gitnexus").mkdir(parents=True)
    with pytest.raises(OmcError, match="same"):
        mirror_snapshot(root, root)


def test_clear_docs_mirror_removes_and_reports(tmp_path):
    from omc.mirror import DOCS_MIRROR_REL, clear_docs_mirror

    target = tmp_path / DOCS_MIRROR_REL
    target.mkdir(parents=True)
    (target / "page.md").write_text("stale")
    assert clear_docs_mirror(tmp_path) is True
    assert not target.exists()
    # parent dirs (.omc/docs/gitnexus) are left alone
    assert target.parent.is_dir()


def test_clear_docs_mirror_absent_is_noop(tmp_path):
    from omc.mirror import clear_docs_mirror

    assert clear_docs_mirror(tmp_path) is False


def test_mirror_dir_noop_when_dst_aliases_src(tmp_path):
    # The incident: primary/.omc and wt/.omc are symlinks to one shared tree;
    # syncing docs onto itself must touch nothing and return False.
    shared = tmp_path / "shared-omc"
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "page.md").write_text("KNOWLEDGE")
    (tmp_path / "primary").mkdir()
    (tmp_path / "wt").mkdir()
    os.symlink(shared, tmp_path / "primary" / ".omc")
    os.symlink(shared, tmp_path / "wt" / ".omc")

    src = tmp_path / "primary" / ".omc" / "docs"
    dst = tmp_path / "wt" / ".omc" / "docs"
    assert mirror_dir(src, dst) is False

    assert (shared / "docs" / "page.md").read_text() == "KNOWLEDGE"


def test_mirror_dir_refuses_nested_paths(tmp_path):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    with pytest.raises(OmcError, match="nest"):
        mirror_dir(parent, child)
    with pytest.raises(OmcError, match="nest"):
        mirror_dir(child, parent)


def test_mirror_dir_updates_in_place(tmp_path):
    # No wipe-and-recreate: the dst root and unchanged files survive by inode.
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "same.md").write_text("same")
    (src / "changed.md").write_text("new content")
    (src / "added.md").write_text("added")
    shutil.copy2(src / "same.md", dst / "same.md")  # identical size+mtime
    (dst / "changed.md").write_text("old")
    (dst / "excess.md").write_text("x")
    root_ino = dst.stat().st_ino
    same_ino = (dst / "same.md").stat().st_ino
    same_mtime = (dst / "same.md").stat().st_mtime_ns

    assert mirror_dir(src, dst) is True

    assert dst.stat().st_ino == root_ino
    assert (dst / "same.md").stat().st_ino == same_ino
    assert (dst / "same.md").stat().st_mtime_ns == same_mtime
    assert (dst / "changed.md").read_text() == "new content"
    assert (dst / "added.md").read_text() == "added"
    assert not (dst / "excess.md").exists()


def test_mirror_dir_replaces_type_conflicts(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "dir-name").mkdir(parents=True)
    (src / "dir-name" / "f.md").write_text("F")
    (src / "file-name").write_text("file")
    dst.mkdir()
    (dst / "dir-name").write_text("was a file")
    (dst / "file-name").mkdir()
    (dst / "file-name" / "junk").write_text("j")

    assert mirror_dir(src, dst) is True

    assert (dst / "dir-name" / "f.md").read_text() == "F"
    assert (dst / "file-name").read_text() == "file"


def test_mirror_dir_replaces_dst_symlink_without_following(tmp_path):
    # dst has a symlink where src has a real dir: the LINK is removed,
    # its target's contents are never touched.
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "f.md").write_text("F")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.md").write_text("KEEP")
    dst = tmp_path / "dst"
    dst.mkdir()
    os.symlink(outside, dst / "docs")

    assert mirror_dir(src, dst) is True

    assert not (dst / "docs").is_symlink()
    assert (dst / "docs" / "f.md").read_text() == "F"
    assert (outside / "keep.md").read_text() == "KEEP"


def test_mirror_dir_recreates_src_symlinks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.md").write_text("real")
    os.symlink("real.md", src / "link.md")  # relative target preserved verbatim
    dst = tmp_path / "dst"

    assert mirror_dir(src, dst) is True

    assert (dst / "link.md").is_symlink()
    assert os.readlink(dst / "link.md") == "real.md"


def test_mirror_dir_case_only_rename_never_leaves_dst_empty(tmp_path):
    # Case-insensitive filesystems (macOS APFS default) treat Page.md and
    # page.md as one file: the copy writes through the old name, and a naive
    # leftover pass then deletes the just-synced content.
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "Page.md").write_text("new")
    (dst / "page.md").write_text("old")

    assert mirror_dir(src, dst) is True

    contents = {p.read_text() for p in dst.iterdir() if p.is_file()}
    assert "new" in contents  # the synced content survives the leftover pass


def test_mirror_snapshot_reports_shared_omc_docs(tmp_path):
    # Incident regression end-to-end: shared .omc via symlinks in BOTH roots;
    # .gitnexus is a real per-checkout dir and still syncs.
    shared = tmp_path / "shared-omc"
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "page.md").write_text("docs")
    primary = tmp_path / "primary"
    wt = tmp_path / "wt"
    (primary / ".gitnexus").mkdir(parents=True)
    (primary / ".gitnexus" / "graph.db").write_text("db")
    wt.mkdir()
    os.symlink(shared, primary / ".omc")
    os.symlink(shared, wt / ".omc")

    result = mirror_snapshot(primary, wt)

    assert result.synced == [".gitnexus"]
    assert result.shared == [".omc/docs"]
    assert (shared / "docs" / "page.md").read_text() == "docs"
    assert (wt / ".omc").is_symlink()  # the symlink is preserved, never materialized
