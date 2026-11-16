import os

from omc.mirror import copy_missing


def test_copy_missing_adds_only_absent_entries_and_counts_them(tmp_path):
    src = tmp_path / "src"
    (src / "deep").mkdir(parents=True)
    (src / "deep" / "new.md").write_text("NEW")
    (src / "kept.md").write_text("SRC")
    (src / "link").symlink_to("kept.md")
    (src / "blocked").mkdir()
    (src / "blocked" / "inner.md").write_text("INNER")
    (src / "dangling").write_text("SRC")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "kept.md").write_text("DST")
    (dst / "extra.md").write_text("EXTRA")
    (dst / "blocked").write_text("a file, not a dir")
    (dst / "dangling").symlink_to("nowhere")

    assert copy_missing(src, dst) == 2  # deep/new.md + link

    assert (dst / "deep" / "new.md").read_text() == "NEW"
    assert os.readlink(dst / "link") == "kept.md"
    assert (dst / "kept.md").read_text() == "DST"  # never overwritten
    assert (dst / "extra.md").read_text() == "EXTRA"  # never deleted
    assert (dst / "blocked").read_text() == "a file, not a dir"  # not descended
    assert os.readlink(dst / "dangling") == "nowhere"  # lexists: a dangling link counts


def test_copy_missing_recreates_a_symlinked_root_without_following_it(tmp_path):
    target = tmp_path / "shared"
    target.mkdir()
    (target / "x.md").write_text("X")
    src = tmp_path / "src"
    src.symlink_to(target)
    dst = tmp_path / "dst"

    assert copy_missing(src, dst) == 1
    assert os.readlink(dst) == str(target)
