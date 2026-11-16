import stat

import pytest

from omc.fsutil import create_exclusive, read_json_object, write_atomic


def test_write_atomic_replaces_with_mode_0644_and_makes_parents(tmp_path):
    target = tmp_path / "a" / "b" / "f.txt"
    write_atomic(target, "one\n")
    write_atomic(target, "two\n")
    assert target.read_text() == "two\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert sorted(p.name for p in target.parent.iterdir()) == ["f.txt"]


def test_write_atomic_without_parents_makes_no_directories(tmp_path):
    target = tmp_path / "gone" / "omc" / "run.json"
    with pytest.raises(FileNotFoundError):
        write_atomic(target, "x\n", parents=False)
    assert not (tmp_path / "gone").exists()


def test_create_exclusive_never_replaces_an_existing_file(tmp_path):
    target = tmp_path / "a" / "id"
    create_exclusive(target, "first\n")
    create_exclusive(target, "second\n")  # the loser's FileExistsError is swallowed
    assert target.read_text() == "first\n"
    assert sorted(p.name for p in target.parent.iterdir()) == ["id"]  # no temp left behind


def test_read_json_object_is_none_unless_the_file_holds_an_object(tmp_path):
    path = tmp_path / "run.json"
    assert read_json_object(path) is None  # missing
    for text in ("not json", "[1]", ""):
        path.write_text(text)
        assert read_json_object(path) is None
    path.write_text('{"run": "a"}\n')
    assert read_json_object(path) == {"run": "a"}
