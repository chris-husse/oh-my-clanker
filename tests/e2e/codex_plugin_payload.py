"""Validate Codex's installed plugin payload, not marketplace source files."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REQUIRED = {
    "omc@oh-my-clanker": ("oh-my-clanker", "omc", "start"),
    "superpowers@superpowers-marketplace": (
        "superpowers-marketplace",
        "superpowers",
        "brainstorming",
    ),
}
OMC_SKILLS = ("start", "plan", "implement")
STATE_NAME = "omc-e2e-plugin-state.json"


def validate_payloads(
    listing: dict, additions: dict, codex_home: Path, repo_manifest_path: Path
) -> dict[str, str]:
    """Return verified versions or raise a safe, actionable ValueError."""
    home = Path(codex_home).resolve()
    cache = home / "plugins" / "cache"
    repo_manifest_path = Path(repo_manifest_path)
    try:
        source_manifest = json.loads(repo_manifest_path.read_text())
        installed = {entry["pluginId"]: entry for entry in listing["installed"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Codex plugin or repository metadata is invalid") from exc

    versions = {}
    for plugin_id, (marketplace, name, skill) in REQUIRED.items():
        entry = installed.get(plugin_id)
        added = additions.get(plugin_id)
        if not entry or not entry.get("installed") or not entry.get("enabled"):
            raise ValueError(f"Codex plugin {plugin_id} is not installed and enabled")
        if not added or added.get("pluginId") != plugin_id:
            raise ValueError(f"Codex plugin {plugin_id} has no recorded installation")
        version = entry.get("version")
        if not isinstance(version, str) or not version or added.get("version") != version:
            raise ValueError(f"Codex plugin {plugin_id} has stale installation metadata")
        raw_path = added.get("installedPath")
        if not isinstance(raw_path, str):
            raise ValueError(f"Codex plugin {plugin_id} has no installed payload path")
        expected = cache / marketplace / name / version
        try:
            actual = Path(raw_path).resolve(strict=True)
            expected_resolved = expected.resolve(strict=True)
            actual.relative_to(cache)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Codex plugin {plugin_id} installed payload is missing or outside cache"
            ) from exc
        if actual != expected_resolved or not actual.is_dir():
            raise ValueError(f"Codex plugin {plugin_id} installed payload path is wrong")
        skill_file = actual / "skills" / skill / "SKILL.md"
        if not skill_file.is_file():
            raise ValueError(f"Codex plugin {plugin_id} installed payload lacks {skill} skill")
        if name == "omc":
            if version != source_manifest.get("version"):
                raise ValueError("Codex OMC installed payload is stale relative to /repo")
            try:
                payload_manifest = json.loads(
                    (actual / ".codex-plugin" / "plugin.json").read_text()
                )
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "Codex OMC installed payload manifest is missing or invalid"
                ) from exc
            if payload_manifest.get("name") != "omc" or payload_manifest.get("version") != version:
                raise ValueError("Codex OMC installed payload manifest is stale or wrong")
            repo_root = repo_manifest_path.parent.parent
            for required_skill in OMC_SKILLS:
                current = repo_root / "skills" / required_skill / "SKILL.md"
                payload = actual / "skills" / required_skill / "SKILL.md"
                try:
                    if current.read_bytes() != payload.read_bytes():
                        raise ValueError(f"Codex OMC installed {required_skill} skill is stale")
                except OSError as exc:
                    raise ValueError(
                        f"Codex OMC installed payload lacks {required_skill} skill"
                    ) from exc
        versions[plugin_id] = version
    return versions


def main(argv: list[str]) -> int:
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    repo_manifest = Path("/repo/.codex-plugin/plugin.json")
    state_path = home / STATE_NAME
    try:
        if argv[0] == "record" and len(argv) == 4:
            additions = {
                plugin["pluginId"]: plugin
                for plugin in (json.loads(Path(path).read_text()) for path in argv[1:3])
            }
            listing = json.loads(Path(argv[3]).read_text())
            validate_payloads(listing, additions, home, repo_manifest)
            state_path.write_text(json.dumps(additions))
            state_path.chmod(0o600)
        elif argv[0] == "verify" and len(argv) == 1:
            additions = json.loads(state_path.read_text())
            listing = json.loads(os.environ["OMC_E2E_CODEX_PLUGIN_LIST"])
            validate_payloads(listing, additions, home, repo_manifest)
        else:
            raise ValueError(
                "usage: codex_plugin_payload record OMC_JSON SUPER_JSON LIST_JSON | verify"
            )
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(f"Codex plugin setup incomplete: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
