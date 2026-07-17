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

    synced = mirror_snapshot(primary, wt)

    assert synced == [".gitnexus", ".omc/docs"]
    assert (wt / ".gitnexus" / "graph.db").read_text() == "db"
    assert not (wt / ".gitnexus" / "stale.db").exists()
    assert (wt / ".omc" / "docs" / "page.md").read_text() == "docs"
    assert not (wt / ".env").exists()


def test_mirror_snapshot_skips_missing_sources(tmp_path):
    primary = tmp_path / "primary"
    wt = tmp_path / "wt"
    (primary / ".gitnexus").mkdir(parents=True)
    wt.mkdir()
    assert mirror_snapshot(primary, wt) == [".gitnexus"]  # no .omc/docs -> skipped


def test_mirror_snapshot_refuses_same_root(tmp_path):
    root = tmp_path / "r"
    (root / ".gitnexus").mkdir(parents=True)
    with pytest.raises(OmcError, match="same"):
        mirror_snapshot(root, root)
