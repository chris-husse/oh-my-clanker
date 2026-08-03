"""flat_store_branch / store_inverted — flat-store inversion detection."""

import json
from pathlib import Path

from omc.gitnexus import flat_store_branch, store_inverted


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
