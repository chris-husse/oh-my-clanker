"""Source fixtures and stateful Codex CLI simulator for plugin unit tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from omc.toolctx import ToolContext


def _source(root: Path, marker: str) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".codex-plugin").mkdir()
    (root / "skills/start").mkdir(parents=True)
    (root / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "name": "oh-my-clanker",
                "plugins": [{"name": "omc", "version": "1.0", "source": "./"}],
            }
        )
    )
    (root / ".codex-plugin/plugin.json").write_text(
        json.dumps(
            {
                "name": "omc",
                "version": "1.0",
                "skills": "./skills/",
            }
        )
    )
    (root / "skills/start/SKILL.md").write_text(marker)
    return root


class CodexState:
    def __init__(self, tmp_path: Path, old: Path | None = None):
        self.home = tmp_path / "codex"
        self.home.mkdir()
        self.ctx = ToolContext.from_env({"HOME": str(tmp_path), "CODEX_HOME": str(self.home)})
        self.current = old
        self.installed = old is not None
        self.payload = self.home / "plugins/cache/oh-my-clanker/omc/1.0"
        if old is not None:
            self._copy(old)
        self.calls: list[list[str]] = []
        self.fail: list[str] = []
        self.fail_initial_list = False
        self.reported_root: Path | None = None
        self.installed_flag: object = True
        self.enabled_flag: object = True
        self.plugin_list_override: object | None = None
        self.plugin_list_raw: str | None = None
        self.add_installed_path: str | None = None
        self.other_marketplace = {
            "name": "other",
            "root": "/other",
            "marketplaceSource": {
                "sourceType": "local",
                "source": "/other",
            },
        }
        self.other_plugin = {"pluginId": "other@other", "installed": True, "enabled": True}
        self.ctx.run = self.run

    def _copy(self, source: Path):
        if self.payload.exists():
            shutil.rmtree(self.payload)
        shutil.copytree(source, self.payload)

    def _registration(self):
        if self.current is None:
            return []
        source = str(self.current.resolve())
        return [
            {
                "name": "oh-my-clanker",
                "root": str(self.reported_root) if self.reported_root is not None else source,
                "marketplaceSource": {
                    "sourceType": "local",
                    "source": source,
                },
            }
        ]

    def _installed(self):
        entries = [self.other_plugin]
        if self.installed and self.current is not None:
            entries.append(
                {
                    "pluginId": "omc@oh-my-clanker",
                    "name": "omc",
                    "marketplaceName": "oh-my-clanker",
                    "version": "1.0",
                    "installed": self.installed_flag,
                    "enabled": self.enabled_flag,
                    "source": {"source": "local", "path": str(self.current.resolve())},
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": str(self.current.resolve()),
                    },
                }
            )
        return entries

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        action = " ".join(argv[1:4])
        if any(action.startswith(failed) for failed in self.fail):
            return subprocess.CompletedProcess(argv, 1, "", "synthetic CLI failure")
        if argv[:4] == ["codex", "plugin", "marketplace", "list"]:
            if self.fail_initial_list:
                self.fail_initial_list = False
                return subprocess.CompletedProcess(
                    argv, 1, "", "oh-my-clanker at old source: stale marketplace manifest"
                )
            data = {"marketplaces": [self.other_marketplace, *self._registration()]}
        elif argv[:4] == ["codex", "plugin", "marketplace", "remove"]:
            self.current = None
            self.installed = False
            data = {"marketplaceName": "oh-my-clanker", "installedRoot": None}
        elif argv[:4] == ["codex", "plugin", "marketplace", "add"]:
            self.current = Path(argv[4])
            data = {
                "marketplaceName": "oh-my-clanker",
                "installedRoot": str(self.current),
                "alreadyAdded": False,
            }
        elif argv[:3] == ["codex", "plugin", "list"]:
            data = (
                self.plugin_list_override
                if self.plugin_list_override is not None
                else {"installed": self._installed(), "available": []}
            )
            if self.plugin_list_raw is not None:
                return subprocess.CompletedProcess(argv, 0, self.plugin_list_raw, "")
        elif argv[:3] == ["codex", "plugin", "add"]:
            assert self.current is not None
            self.installed = True
            self._copy(self.reported_root or self.current)
            data = {
                "pluginId": "omc@oh-my-clanker",
                "name": "omc",
                "marketplaceName": "oh-my-clanker",
                "version": "1.0",
                "installedPath": self.add_installed_path or str(self.payload),
                "authPolicy": "ON_INSTALL",
            }
        else:
            raise AssertionError(f"unexpected argv: {argv}")
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
