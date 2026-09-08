import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import stamp_version as sv  # noqa: E402


def test_major_minor_from_full_version():
    assert sv.major_minor('[project]\nversion = "0.1.47"\n') == "0.1"


def test_major_minor_from_two_part_version():
    assert sv.major_minor('[project]\nversion = "2.5"\n') == "2.5"


def test_major_minor_rejects_single_part():
    with pytest.raises(ValueError):
        sv.major_minor('[project]\nversion = "7"\n')


def test_stamp_pyproject_replaces_only_the_version_line():
    text = '[project]\nname = "omc"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    out = sv.stamp_pyproject(text, "0.1.9")
    assert 'version = "0.1.9"' in out
    assert 'name = "omc"' in out
    assert 'requires-python = ">=3.12"' in out


def test_stamp_json_preserves_inline_objects():
    text = '{\n  "name": "omc",\n  "version": "0.1.0",\n  "author": { "name": "X" }\n}\n'
    out = sv.stamp_json(text, "0.1.9", "plugin.json")
    assert '"version": "0.1.9"' in out
    # A json.dumps round-trip would reflow this inline object; a value-only
    # replacement must leave it byte-for-byte intact.
    assert '"author": { "name": "X" }' in out
    assert out == text.replace('"0.1.0"', '"0.1.9"')


def test_stamp_json_raises_when_no_version():
    with pytest.raises(ValueError):
        sv.stamp_json('{\n  "name": "omc"\n}\n', "0.1.9", "x.json")


def test_stamp_json_raises_when_multiple_versions():
    text = '{\n  "version": "0.1.0",\n  "nested": { "version": "9.9.9" }\n}\n'
    with pytest.raises(ValueError):
        sv.stamp_json(text, "0.1.9", "x.json")


def test_stamp_json_is_idempotent():
    text = '{\n  "version": "0.1.9"\n}\n'
    assert sv.stamp_json(text, "0.1.9", "x.json") == text


def test_stamp_writes_all_four_files(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "omc"\nversion = "0.3.0"\n')
    plugin = tmp_path / "plugin.json"
    plugin.write_text(
        '{\n  "name": "omc",\n  "version": "0.3.0",\n  "author": { "name": "X" }\n}\n'
    )
    marketplace = tmp_path / "marketplace.json"
    marketplace.write_text('{\n  "plugins": [\n    { "name": "omc", "version": "0.3.0" }\n  ]\n}\n')
    codex = tmp_path / "codex.json"
    codex.write_text('{\n  "name": "omc",\n  "version": "0.3.0"\n}\n')

    monkeypatch.setattr(sv, "PYPROJECT", pyproject)
    monkeypatch.setattr(sv, "JSON_FILES", (plugin, marketplace, codex))

    result = sv.stamp(42)

    assert result == "0.3.42"
    assert 'version = "0.3.42"' in pyproject.read_text()
    assert json.loads(plugin.read_text())["version"] == "0.3.42"
    assert json.loads(marketplace.read_text())["plugins"][0]["version"] == "0.3.42"
    assert json.loads(codex.read_text())["version"] == "0.3.42"
    # inline object preserved (no JSON reflow)
    assert '"author": { "name": "X" }' in plugin.read_text()


def test_stamp_lock_targets_omc_not_a_dependency():
    # uv.lock carries a version line per package; only omc's may be stamped.
    text = (
        '[[package]]\nname = "pytest"\nversion = "8.0.0"\n\n'
        '[[package]]\nname = "omc"\nversion = "0.3.0"\nsource = { editable = "." }\n'
    )
    out = sv.stamp_lock(text, "0.3.42")
    assert 'name = "omc"\nversion = "0.3.42"' in out
    assert 'name = "pytest"\nversion = "8.0.0"' in out  # dependency untouched
    assert 'source = { editable = "." }' in out


def test_stamp_lock_raises_when_omc_entry_is_missing():
    with pytest.raises(ValueError):
        sv.stamp_lock(
            '[[package]]\nname = "pytest"\nversion = "8.0.0"\n',
            "0.3.42",
        )


def test_stamp_lock_is_idempotent():
    text = '[[package]]\nname = "omc"\nversion = "0.3.42"\n'
    assert sv.stamp_lock(text, "0.3.42") == text


def test_stamp_writes_uv_lock_too(tmp_path, monkeypatch):
    # The regression this guards: uv.lock was left one version behind every
    # release, and `uv run` silently re-synced it into a spurious diff.
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "omc"\nversion = "0.3.0"\n')
    plugin = tmp_path / "plugin.json"
    plugin.write_text('{\n  "version": "0.3.0"\n}\n')
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "ruff"\nversion = "0.1.0"\n\n'
        '[[package]]\nname = "omc"\nversion = "0.3.0"\n'
    )

    monkeypatch.setattr(sv, "PYPROJECT", pyproject)
    monkeypatch.setattr(sv, "JSON_FILES", (plugin,))
    monkeypatch.setattr(sv, "LOCK", lock)

    assert sv.stamp(42) == "0.3.42"
    assert 'name = "omc"\nversion = "0.3.42"' in lock.read_text()
    assert 'name = "ruff"\nversion = "0.1.0"' in lock.read_text()
