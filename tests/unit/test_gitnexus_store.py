"""flat_store_branch / store_inverted — flat-store inversion detection."""

import json
from pathlib import Path
from types import SimpleNamespace

from omc.gitnexus import child_death, describe_child_failure, flat_store_branch, store_inverted


def _seed_meta(root: Path, payload) -> None:
    d = root / ".gitnexus"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(payload, encoding="utf-8")


def test_absent_store_is_none_and_not_inverted(tmp_path):
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False


def test_unparseable_meta_is_none_and_not_inverted(tmp_path):
    _seed_meta(tmp_path, "{not json")
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False


def test_missing_or_empty_branch_stamp_is_none(tmp_path):
    _seed_meta(tmp_path, json.dumps({"lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) is None
    _seed_meta(tmp_path, json.dumps({"branch": "", "lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False


def test_owner_equals_base_is_not_inverted(tmp_path):
    _seed_meta(tmp_path, json.dumps({"branch": "main", "lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) == "main"
    assert store_inverted(tmp_path, "main") is False


def test_owner_differs_from_base_is_inverted(tmp_path):
    _seed_meta(tmp_path, json.dumps({"branch": "feature/omc-v1", "lastCommit": "abc"}))
    assert flat_store_branch(tmp_path) == "feature/omc-v1"
    assert store_inverted(tmp_path, "main") is True


def test_non_dict_meta_is_none(tmp_path):
    _seed_meta(tmp_path, json.dumps(["not", "a", "dict"]))
    assert flat_store_branch(tmp_path) is None
    assert store_inverted(tmp_path, "main") is False


def test_child_death_names_signals_and_exits():
    assert child_death(-11) == "killed by SIGSEGV (signal 11)"
    assert child_death(-9) == "killed by SIGKILL (signal 9)"
    assert child_death(3) == "exit 3"
    assert child_death(0) == "exit 0"
    assert child_death(-99) == "killed by signal 99"  # no such Signals member


def test_describe_failure_labels_both_streams_redacted():
    cp = SimpleNamespace(returncode=1, stdout="  banner\n", stderr="tok@host boom")
    desc = describe_child_failure(cp, redact=lambda s: s.replace("tok@", "[REDACTED]@"))
    assert desc.startswith("exit 1 — ")
    assert "stderr: [REDACTED]@host boom" in desc
    assert "stdout: banner" in desc
    assert desc.index("stderr:") < desc.index("stdout:")


def test_describe_failure_signal_death_banner_only():
    cp = SimpleNamespace(returncode=-11, stdout="\n  GitNexus Wiki Generator\n", stderr="")
    assert (
        describe_child_failure(cp)
        == "killed by SIGSEGV (signal 11) — stdout: GitNexus Wiki Generator"
    )


def test_describe_failure_silent_child_and_caps():
    assert describe_child_failure(SimpleNamespace(returncode=-9, stdout="", stderr=None)) == (
        "killed by SIGKILL (signal 9)"
    )
    long = describe_child_failure(SimpleNamespace(returncode=2, stdout="x" * 500, stderr=""))
    assert long == f"exit 2 — stdout: {'x' * 400}"
