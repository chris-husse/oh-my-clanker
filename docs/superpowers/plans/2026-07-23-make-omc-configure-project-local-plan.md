# Project-Local omc Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split omc's single global `~/.omc/config.json` into a global `~/.omc/config.yaml` (llm + notifications) and a committed per-project `.omc/config.yaml` (worktree section), with migration handled exclusively by `omc configure`.

**Architecture:** Split persistence, composed runtime view. Two on-disk schemas (`GlobalConfig`, `ProjectConfig`) serialize as YAML; a new resolution layer (`config/resolve.py`) composes them back into the existing `Config` runtime shape at the dispatch boundary, so every consumer of `cfg.llm` / `cfg.worktree` / `cfg.notifications` is untouched. The old JSON API stays alive until Task 5 so the suite is green after every task.

**Tech Stack:** Python 3.12, dataclasses, PyYAML (`safe_load`/`safe_dump` only), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-07-23-make-omc-configure-project-local-design.md`

## Global Constraints

- New dependency: `pyyaml>=6,<7` — the ONLY new dependency. Use `yaml.safe_load` / `yaml.safe_dump` exclusively; never `yaml.load`/`yaml.dump`.
- Strictness contract is preserved verbatim: missing file → `None`; unparseable / non-mapping / unknown keys / bad notification values → `ConfigError` naming the file. (`ConfigError` rc=1, `Refusal` rc=2.)
- Non-`configure` commands NEVER write config files (no migrate-on-read).
- `schema.py` must NOT gain `from __future__ import annotations` — `_hydrate`/`set_key` rely on `dataclasses.fields(...)[i].type` being real classes, not strings.
- The e2e `tests/e2e/conftest.py:33` `config.json` is **Docker's** auth config — never touch it.
- Configure tests MUST pin cwd (`monkeypatch.chdir`) — the pytest cwd is a real git repo; an unpinned test would write `.omc/config.yaml` into the developer's checkout.
- Run tests with `uv run pytest tests/unit -q` (worktree venv: run `uv sync` first if imports look stale).
- Commit after every task.

## File Structure

- `src/omc/config/schema.py` — add `GlobalConfig` (schema_version, llm, notifications) + `ProjectConfig` (schema_version, worktree); `Config` survives as the runtime composite / legacy-JSON hydration shape.
- `src/omc/config/store.py` — YAML load/save per schema, path helpers, `load_legacy`; old JSON `load`/`save`/`config_path` deleted in Task 5.
- `src/omc/config/resolve.py` (new) — `project_config(ctx)`, `load_effective(ctx)`.
- `src/omc/configure.py` — split walkthrough, `--set` routing, `--defaults` non-clobber, legacy migration + deletion.
- `src/omc/cli.py`, `src/omc/internal.py`, `src/omc/notify.py`, `src/omc/installer.py` — reader flips.
- `skills/finish/SKILL.md`, `skills/gitnexus-document/SKILL.md`, `skills/integrate/SKILL.md`, `README.md`, `.omc/config.yaml` (dogfood) — behavior layer + docs.

---

### Task 1: YAML store layer (additive)

**Model:** heavy coding tier

**Files:**
- Modify: `pyproject.toml:7` (dependencies)
- Modify: `src/omc/config/schema.py`
- Modify: `src/omc/config/store.py`
- Test: `tests/unit/test_config_store.py` (append new tests; existing tests keep passing)

**Interfaces:**
- Consumes: existing `_hydrate`, `set_key`, `validate_backend`, `Config`, `LLMConfig`, `NotificationsConfig`, `WorktreeConfig`, `ProviderConfig`.
- Produces (later tasks rely on these exact names):
  - `schema.GlobalConfig(schema_version: int = 1, llm: LLMConfig, notifications: NotificationsConfig)`
  - `schema.ProjectConfig(schema_version: int = 1, worktree: WorktreeConfig)`
  - `store.global_config_path(home: Path) -> Path` (= `home / "config.yaml"`)
  - `store.project_config_path(root: Path) -> Path` (= `root / ".omc" / "config.yaml"`)
  - `store.legacy_config_path(home: Path) -> Path` (= `home / "config.json"`)
  - `store.load_global(home: Path) -> GlobalConfig | None`, `store.save_global(home: Path, cfg: GlobalConfig) -> None`
  - `store.load_project(root: Path) -> ProjectConfig | None`, `store.save_project(root: Path, cfg: ProjectConfig) -> None`
  - `store.load_legacy(home: Path) -> tuple[GlobalConfig, ProjectConfig] | None`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` change:

```toml
dependencies = ["questionary>=2.0,<3", "pyyaml>=6,<7"]
```

Run: `uv lock && uv sync` (commits `uv.lock` too).

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_config_store.py` (add `from omc.config.schema import GlobalConfig, ProjectConfig` to the imports):

```python
# --- split YAML store ---


def test_load_global_missing_returns_none(tmp_path):
    assert store.load_global(tmp_path) is None


def test_global_round_trip_yaml(tmp_path):
    cfg = GlobalConfig()
    cfg.llm.default = "codex"
    cfg.llm.providers["codex"] = ProviderConfig(model="gpt-x")
    cfg.notifications.enabled = True
    cfg.notifications.backend = "file:///tmp/omc.log"
    store.save_global(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "schema_version" in text and "{" not in text  # YAML block style, not JSON
    loaded = store.load_global(tmp_path)
    assert loaded.llm.default == "codex"
    assert loaded.llm.providers["codex"].model == "gpt-x"
    assert loaded.notifications.enabled is True
    assert loaded.schema_version == 1


def test_global_has_no_worktree_key(tmp_path):
    (tmp_path / "config.yaml").write_text("schema_version: 1\nworktree:\n  base_branch: dev\n")
    with pytest.raises(ConfigError, match="worktree"):
        store.load_global(tmp_path)


def test_project_round_trip_yaml(tmp_path):
    cfg = ProjectConfig()
    cfg.worktree.branch_prefix = "wip/"
    cfg.worktree.base_branch = "develop"
    store.save_project(tmp_path, cfg)
    assert (tmp_path / ".omc" / "config.yaml").is_file()
    loaded = store.load_project(tmp_path)
    assert loaded.worktree.branch_prefix == "wip/"
    assert loaded.worktree.base_branch == "develop"


def test_project_missing_returns_none(tmp_path):
    assert store.load_project(tmp_path) is None


def test_project_rejects_global_keys(tmp_path):
    (tmp_path / ".omc").mkdir()
    (tmp_path / ".omc" / "config.yaml").write_text("llm:\n  default: claude\n")
    with pytest.raises(ConfigError, match="llm"):
        store.load_project(tmp_path)


def test_yaml_parse_error_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text("{nope")
    with pytest.raises(ConfigError, match="invalid YAML"):
        store.load_global(tmp_path)


def test_yaml_non_mapping_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="expected a mapping"):
        store.load_global(tmp_path)


def test_set_key_on_split_schemas():
    gcfg = GlobalConfig()
    store.set_key(gcfg, "llm.default", "opencode")
    assert gcfg.llm.default == "opencode"
    pcfg = ProjectConfig()
    store.set_key(pcfg, "worktree.base_branch", "master")
    assert pcfg.worktree.base_branch == "master"
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(gcfg, "worktree.base_branch", "master")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(pcfg, "llm.default", "claude")


# --- legacy combined config.json (read by `omc configure` migration only) ---


def test_load_legacy_missing_returns_none(tmp_path):
    assert store.load_legacy(tmp_path) is None


def test_load_legacy_splits_sections(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"schema_version": 1, "llm": {"default": "codex"},'
        ' "worktree": {"base_branch": "develop"},'
        ' "notifications": {"enabled": true, "backend": "macos"}}'
    )
    gcfg, pcfg = store.load_legacy(tmp_path)
    assert gcfg.llm.default == "codex"
    assert gcfg.notifications.enabled is True
    assert pcfg.worktree.base_branch == "develop"


def test_load_legacy_rejects_bad_json(tmp_path):
    (tmp_path / "config.json").write_text("{nope")
    with pytest.raises(ConfigError):
        store.load_legacy(tmp_path)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_store.py -q`
Expected: new tests FAIL with `AttributeError: ... no attribute 'load_global'` / `ImportError: cannot import name 'GlobalConfig'`; all pre-existing tests still PASS.

- [ ] **Step 4: Implement schema split**

Append to `src/omc/config/schema.py` (do NOT add `from __future__ import annotations` — `_hydrate` needs real classes in `fields(...).type`), and update `Config`'s role with a docstring:

```python
@dataclass
class GlobalConfig:
    """Persisted at ~/.omc/config.yaml — personal settings."""

    schema_version: int = 1
    llm: LLMConfig = field(default_factory=LLMConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


@dataclass
class ProjectConfig:
    """Persisted at <repo>/.omc/config.yaml (committed) — project settings."""

    schema_version: int = 1
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
```

And on the existing `Config` class add the docstring (fields unchanged):

```python
@dataclass
class Config:
    """Runtime composite of GlobalConfig + ProjectConfig; also the hydration
    shape of the legacy combined ~/.omc/config.json. Never persisted as one
    file anymore."""
```

- [ ] **Step 5: Implement the YAML store**

In `src/omc/config/store.py`: add `import yaml` (after `import json`), extend the schema import to include `GlobalConfig` and `ProjectConfig`, and add below `config_path` (leave the existing `config_path`/`load`/`save` untouched — they die in Task 5):

```python
def global_config_path(home: Path) -> Path:
    return home / "config.yaml"


def project_config_path(root: Path) -> Path:
    return root / ".omc" / "config.yaml"


def legacy_config_path(home: Path) -> Path:
    return home / "config.json"


def _load_yaml(path: Path, cls: type):
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config in {path}: expected a mapping")
    return _hydrate(cls, data, str(path))


def _save_yaml(path: Path, cfg) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(asdict(cfg), sort_keys=False))


def load_global(home: Path) -> GlobalConfig | None:
    return _load_yaml(global_config_path(home), GlobalConfig)


def save_global(home: Path, cfg: GlobalConfig) -> None:
    _save_yaml(global_config_path(home), cfg)


def load_project(root: Path) -> ProjectConfig | None:
    return _load_yaml(project_config_path(root), ProjectConfig)


def save_project(root: Path, cfg: ProjectConfig) -> None:
    _save_yaml(project_config_path(root), cfg)


def load_legacy(home: Path) -> tuple[GlobalConfig, ProjectConfig] | None:
    """The old combined ~/.omc/config.json. Read ONLY by `omc configure`,
    which migrates it into the split YAML files and deletes it."""
    path = legacy_config_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config in {path}: expected an object")
    combined = _hydrate(Config, data, str(path))
    return (
        GlobalConfig(llm=combined.llm, notifications=combined.notifications),
        ProjectConfig(worktree=combined.worktree),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_store.py -q`
Expected: ALL PASS.

- [ ] **Step 7: Full unit suite + lint, then commit**

Run: `uv run pytest tests/unit -q && uvx ruff check src tests`
Expected: PASS, no lint errors.

```bash
git add pyproject.toml uv.lock src/omc/config/schema.py src/omc/config/store.py tests/unit/test_config_store.py
git commit -m "feat: split config schema + YAML store layer (additive)"
```

---

### Task 2: Resolution layer (`config/resolve.py`)

**Model:** standard coding tier

**Files:**
- Create: `src/omc/config/resolve.py`
- Test: `tests/unit/test_resolve.py` (new)

**Interfaces:**
- Consumes: `store.load_global`, `store.load_project` (Task 1); `wtconfig.repo_root(ctx) -> str | None`; `ToolContext`.
- Produces:
  - `resolve.project_config(ctx: ToolContext) -> ProjectConfig` (never `None`; defaults outside a repo or when the file is absent)
  - `resolve.load_effective(ctx: ToolContext) -> Config | None` (`None` iff global config missing)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_resolve.py`:

```python
import subprocess

from omc.config import resolve, store
from omc.config.schema import GlobalConfig, ProjectConfig
from omc.toolctx import ToolContext


def _ctx(tmp_path, monkeypatch, cwd):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(cwd)
    return ToolContext.from_env(), home


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_load_effective_none_without_global(tmp_path, monkeypatch):
    ctx, _ = _ctx(tmp_path, monkeypatch, tmp_path)
    assert resolve.load_effective(ctx) is None


def test_load_effective_outside_repo_uses_worktree_defaults(tmp_path, monkeypatch):
    ctx, home = _ctx(tmp_path, monkeypatch, tmp_path)
    gcfg = GlobalConfig()
    gcfg.llm.default = "codex"
    store.save_global(home, gcfg)
    cfg = resolve.load_effective(ctx)
    assert cfg.llm.default == "codex"
    assert cfg.worktree.branch_prefix == "feature/"
    assert cfg.worktree.base_branch == "main"


def test_load_effective_composes_project_file(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    pcfg = ProjectConfig()
    pcfg.worktree.base_branch = "develop"
    store.save_project(repo, pcfg)
    ctx, home = _ctx(tmp_path, monkeypatch, repo)
    store.save_global(home, GlobalConfig())
    cfg = resolve.load_effective(ctx)
    assert cfg.worktree.base_branch == "develop"
    assert cfg.llm.default == "claude"


def test_project_config_defaults_in_repo_without_file(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    ctx, _ = _ctx(tmp_path, monkeypatch, repo)
    assert resolve.project_config(ctx).worktree.base_branch == "main"
```

Note pytest's cwd is a real git repo — every test here pins cwd via `_ctx`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolve.py -q`
Expected: FAIL with `ImportError: cannot import name 'resolve'`.

- [ ] **Step 3: Implement**

Create `src/omc/config/resolve.py`:

```python
"""Compose the persisted config files into the runtime view consumers use.

Global (~/.omc/config.yaml) is required by gated commands; the project file
(<repo>/.omc/config.yaml) is optional — absent file or no repo means
dataclass defaults, so un-integrated repos keep working.
"""

from __future__ import annotations

from pathlib import Path

from ..toolctx import ToolContext
from ..wtconfig import repo_root
from . import store
from .schema import Config, ProjectConfig


def project_config(ctx: ToolContext) -> ProjectConfig:
    root = repo_root(ctx)
    if root is None:
        return ProjectConfig()
    return store.load_project(Path(root)) or ProjectConfig()


def load_effective(ctx: ToolContext) -> Config | None:
    gcfg = store.load_global(ctx.home)
    if gcfg is None:
        return None
    return Config(
        llm=gcfg.llm,
        notifications=gcfg.notifications,
        worktree=project_config(ctx).worktree,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolve.py -q`
Expected: ALL PASS.

- [ ] **Step 5: Full unit suite + lint, then commit**

Run: `uv run pytest tests/unit -q && uvx ruff check src tests`

```bash
git add src/omc/config/resolve.py tests/unit/test_resolve.py
git commit -m "feat: config resolution layer composing global + project files"
```

---

### Task 3: `omc configure` rewrite (routing, non-clobber, migration)

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/configure.py`
- Modify: `src/omc/cli.py:26` (help string only)
- Test: `tests/unit/test_configure.py`

**Interfaces:**
- Consumes: Task 1 store API (`load_global`/`save_global`/`load_project`/`save_project`/`load_legacy`/path helpers), `GlobalConfig`, `ProjectConfig`, `wtconfig.repo_root`.
- Produces: `run_configure(ctx, *, defaults: bool, sets: list[str]) -> int` (signature unchanged; behavior per spec). Routing rule other tasks/docs rely on: dotted keys whose first segment is `worktree` go to the project file; everything else goes global.

- [ ] **Step 1: Update the test file**

Rewrite `tests/unit/test_configure.py` — the `_home` fixture MUST now pin cwd (pytest's cwd is this real repo; an unpinned `--defaults` run would create `.omc/config.yaml` in the developer's checkout):

```python
import json
import subprocess

from omc.agentsmd import distribution_agents_md
from omc.cli import main
from omc.config import store


def _home(tmp_path, monkeypatch):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # outside any git repo
    return home


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    return repo


def test_configure_defaults(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    assert main(["configure", "--defaults"]) == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "claude"
    out = capsys.readouterr().out
    assert "/plugin marketplace add" in out  # claude hint
    assert "codex plugin marketplace add" in out  # codex hint
    assert "opencode" in out  # opencode hint


def test_configure_set_global(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(
        [
            "configure",
            "--set",
            "llm.default=opencode",
            "--set",
            "llm.providers.opencode.model=anthropic/claude-sonnet-5",
        ]
    )
    assert rc == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "opencode"
    assert cfg.llm.providers["opencode"].model == "anthropic/claude-sonnet-5"


def test_configure_set_worktree_routes_to_project_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    rc = main(["configure", "--set", "worktree.base_branch=master"])
    assert rc == 0
    pcfg = store.load_project(repo)
    assert pcfg.worktree.base_branch == "master"
    assert store.load_global(home) is None  # global untouched by a pure worktree set


def test_configure_set_worktree_outside_repo_refused(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "worktree.base_branch=master"]) == 2
    assert "project config" in capsys.readouterr().err


def test_configure_defaults_and_set_combined(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(["configure", "--defaults", "--set", "llm.default=codex"])
    assert rc == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "codex"


def test_configure_defaults_seeds_project_file_when_absent(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--defaults"]) == 0
    pcfg = store.load_project(repo)
    assert pcfg.worktree.branch_prefix == "feature/"


def test_configure_defaults_never_clobbers_project_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    (repo / ".omc").mkdir()
    (repo / ".omc" / "config.yaml").write_text(
        "schema_version: 1\nworktree:\n  branch_prefix: wip/\n  base_branch: develop\n"
    )
    assert main(["configure", "--defaults"]) == 0
    pcfg = store.load_project(repo)
    assert pcfg.worktree.base_branch == "develop"  # committed team truth untouched


def test_configure_migrates_legacy_json(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    home.mkdir(parents=True)
    (home / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "llm": {"default": "codex", "providers": {"codex": {"model": "gpt-x"}}},
                "worktree": {"base_branch": "develop"},
                "notifications": {"enabled": True, "backend": "macos"},
            }
        )
    )
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--set", "llm.providers.codex.model=gpt-y"]) == 0
    cfg = store.load_global(home)
    assert cfg.llm.default == "codex"  # seeded from legacy
    assert cfg.llm.providers["codex"].model == "gpt-y"  # then --set applied
    assert cfg.notifications.enabled is True
    assert not (home / "config.json").exists()  # deleted after global YAML written
    assert "Migrated legacy" in capsys.readouterr().out
    pcfg = store.load_project(repo)
    assert pcfg.worktree.base_branch == "develop"  # worktree section carried into repo


def test_pure_worktree_set_keeps_legacy_json(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    home.mkdir(parents=True)
    (home / "config.json").write_text('{"schema_version": 1}')
    _repo(tmp_path, monkeypatch)
    assert main(["configure", "--set", "worktree.base_branch=master"]) == 0
    assert (home / "config.json").exists()  # global YAML not written -> no deletion


def test_configure_set_bad_key(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "nope=1"]) == 1
    assert "unknown config key" in capsys.readouterr().err


def test_configure_set_bad_format(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "no-equals-sign"]) == 2


def test_interactive_requires_tty(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["configure"]) == 2
    assert "TTY" in capsys.readouterr().err


def test_configure_in_repo_creates_agents_chain(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--defaults"]) == 0
    assert (repo / "AGENTS.md").is_symlink()
    assert (repo / "CLAUDE.md").is_symlink()
    assert (repo / "AGENTS.md").resolve() == distribution_agents_md().resolve()
    assert not (repo / ".omc" / "internal" / "AGENTS.md").exists()
    assert (repo / ".omc" / "config" / "AGENTS.md").is_file()


def test_configure_outside_repo_skips_chain(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    outside = tmp_path / "nowhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["configure", "--defaults"]) == 0
    assert not (outside / "AGENTS.md").exists()
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/unit/test_configure.py -q`
Expected: FAIL — `store.load_global(home)` returns `None` (configure still writes JSON), routing/migration tests fail.

- [ ] **Step 3: Rewrite `run_configure`**

Replace the body of `src/omc/configure.py` between the `_PLUGIN_HINTS` block and `_ensure_repo_chain` (imports change too — full new top-of-file shown):

```python
"""`omc configure` — pick your LLM (and worktree naming); print plugin install hints."""

from __future__ import annotations

import sys
from pathlib import Path

from .agentsmd import ensure_agents_chain
from .config import store
from .config.schema import GlobalConfig, ProjectConfig, ProviderConfig
from .errors import ConfigError, Refusal
from .providers.registry import get_provider, provider_names
from .toolctx import ToolContext
from .wtconfig import repo_root
```

(`_PLUGIN_HINTS` stays byte-identical.)

```python
def run_configure(ctx: ToolContext, *, defaults: bool, sets: list[str]) -> int:
    root_str = repo_root(ctx)
    root = Path(root_str) if root_str else None
    legacy = store.load_legacy(ctx.home)
    legacy_global, legacy_project = legacy if legacy else (None, None)

    if defaults or sets:
        # --defaults establishes the starting point (fresh GlobalConfig rather
        # than whatever's on disk); --set pairs, if any, are then applied on
        # top of it. Passing both together must not silently drop --set.
        gcfg = (
            GlobalConfig()
            if defaults
            else (store.load_global(ctx.home) or legacy_global or GlobalConfig())
        )
        pcfg = (
            (store.load_project(root) if root else None) or legacy_project or ProjectConfig()
        )
        write_global = defaults
        # --defaults seeds a missing project file but never clobbers an
        # existing one: it is committed team truth, not personal state.
        write_project = bool(
            defaults and root is not None and not store.project_config_path(root).exists()
        )
        for pair in sets:
            key, sep, value = pair.partition("=")
            if not sep:
                raise Refusal(f"--set expects KEY=VALUE, got {pair!r}")
            if key.split(".", 1)[0] == "worktree":
                if root is None:
                    raise Refusal(
                        "worktree.* is project config — run inside a git repository"
                    )
                store.set_key(pcfg, key, value)
                write_project = True
            else:
                store.set_key(gcfg, key, value)
                write_global = True
        # Migration must not lose the legacy worktree section: when this run
        # writes the global YAML (which deletes the JSON afterwards) and the
        # repo has no project file yet, seed it from the legacy content.
        if (
            write_global
            and legacy is not None
            and root is not None
            and not store.project_config_path(root).exists()
        ):
            write_project = True
        if write_global:
            store.save_global(ctx.home, gcfg)
            label = "Wrote defaults to" if defaults and not sets else "Updated"
            print(f"{label} {store.global_config_path(ctx.home)}")
        if write_project and root is not None:
            store.save_project(root, pcfg)
            print(f"Updated {store.project_config_path(root)}")
        _migrate_legacy(ctx, migrated=write_global)
        _ensure_repo_chain(ctx)
        print(_PLUGIN_HINTS)
        return 0

    if not sys.stdin.isatty():
        raise Refusal("interactive configure needs a TTY (use --defaults or --set KEY=VALUE)")
    gcfg = store.load_global(ctx.home) or legacy_global or GlobalConfig()
    _walkthrough_global(gcfg)
    pcfg = None
    if root is not None:
        pcfg = store.load_project(root) or legacy_project or ProjectConfig()
        _walkthrough_project(pcfg)
    store.save_global(ctx.home, gcfg)
    print(f"Saved {store.global_config_path(ctx.home)}")
    if root is not None and pcfg is not None:
        store.save_project(root, pcfg)
        print(f"Saved {store.project_config_path(root)}")
    else:
        print("(not inside a git repository — worktree.* settings are configured per-repo)")
    _migrate_legacy(ctx, migrated=True)
    _ensure_repo_chain(ctx)
    print(_PLUGIN_HINTS)
    return 0


def _migrate_legacy(ctx: ToolContext, *, migrated: bool) -> None:
    """Delete the legacy combined config.json — but only when this run wrote
    the global YAML (its content now lives there); a pure worktree.* update
    must leave it for a later global write to migrate."""
    path = store.legacy_config_path(ctx.home)
    if migrated and path.exists():
        path.unlink()
        print(
            f"Migrated legacy {path} → {store.global_config_path(ctx.home)} "
            "(worktree.* now lives in each repo's .omc/config.yaml)"
        )
```

- [ ] **Step 4: Split the walkthrough**

Replace `_walkthrough(cfg)` with two functions — the question blocks move verbatim, only the receiver changes (`# pragma: no cover - PTY-driven, E2E territory` stays on both):

```python
def _walkthrough_global(cfg: GlobalConfig) -> None:  # pragma: no cover - PTY-driven, E2E territory
    import questionary
    from questionary import Choice

    names = provider_names()
    selected = questionary.checkbox(
        "Which LLMs do you use?",
        choices=[Choice(n, checked=(n in cfg.llm.providers)) for n in names],
    ).ask()
    if not selected:
        selected = list(cfg.llm.providers) or ["claude"]
    cfg.llm.providers = {n: cfg.llm.providers.get(n, ProviderConfig()) for n in selected}

    for name in selected:
        pcfg = cfg.llm.providers[name]
        known = get_provider(name).models()
        if known:
            other = "Other (type a model id)…"
            default = pcfg.model if pcfg.model in known else known[0]
            picked = questionary.select(
                f"{name} model", choices=[*known, other], default=default
            ).ask()
            model = (
                questionary.text(f"{name} model id", default=pcfg.model).ask()
                if picked == other
                else picked
            )
        else:
            model = questionary.text(
                f"{name} model (blank = provider default)", default=pcfg.model
            ).ask()
        pcfg.model = model or ""

    if len(selected) == 1:
        cfg.llm.default = selected[0]
    else:
        cfg.llm.default = (
            questionary.select(
                "Default provider for `omc start`",
                choices=selected,
                default=cfg.llm.default if cfg.llm.default in selected else selected[0],
            ).ask()
            or selected[0]
        )

    enable = questionary.confirm(
        "Notify when a session needs attention (macOS notification / log file)?",
        default=cfg.notifications.enabled,
    ).ask()
    cfg.notifications.enabled = bool(enable)
    if enable:
        while True:
            backend = (
                questionary.text(
                    "Notification backend: 'macos' or file:///absolute/path.log",
                    default=cfg.notifications.backend,
                ).ask()
                or cfg.notifications.backend
            )
            try:
                cfg.notifications.backend = store.validate_backend(backend)
                break
            except ConfigError as exc:
                print(exc)


def _walkthrough_project(cfg: ProjectConfig) -> None:  # pragma: no cover - PTY-driven, E2E territory
    import questionary

    cfg.worktree.branch_prefix = (
        questionary.text("Branch prefix", default=cfg.worktree.branch_prefix).ask()
        or cfg.worktree.branch_prefix
    )
    cfg.worktree.base_branch = (
        questionary.text("Base branch", default=cfg.worktree.base_branch).ask()
        or cfg.worktree.base_branch
    )
```

- [ ] **Step 5: Update the help string**

`src/omc/cli.py:26`:

```python
    p_conf = sub.add_parser(
        "configure",
        help="Pick your LLM (~/.omc/config.yaml) and the repo's worktree naming (.omc/config.yaml)",
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_configure.py -q`
Expected: ALL PASS.

- [ ] **Step 7: Full unit suite + lint, then commit**

Run: `uv run pytest tests/unit -q && uvx ruff check src tests`
Expected: PASS (cli/internal/notify/installer still read the OLD JSON path and their tests still plant JSON — untouched until Task 4).

```bash
git add src/omc/configure.py src/omc/cli.py tests/unit/test_configure.py
git commit -m "feat: configure writes split YAML configs, migrates legacy config.json"
```

---

### Task 4: Flip the readers (cli, internal, notify, installer)

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/cli.py:76-81` (`_load_cfg_or_bail`)
- Modify: `src/omc/internal.py:37-38,109-110`
- Modify: `src/omc/notify.py:197` (+ its `Config` annotations)
- Modify: `src/omc/installer.py:68` (+ its `Config` annotations if any)
- Test: `tests/unit/test_cli.py`, `tests/unit/test_internal.py:173`, `tests/unit/test_notify.py:265`, `tests/unit/test_installer.py:75,105-107,153-155`

**Interfaces:**
- Consumes: `resolve.load_effective(ctx)`, `resolve.project_config(ctx)`, `store.load_global(home)`, `store.legacy_config_path(home)`.
- Produces: no new interfaces — behavioral parity per the spec's caller table. Consumers of the returned `Config` (start/watch/slug/probe/plugin) are NOT touched.

- [ ] **Step 1: Update the tests first**

- `tests/unit/test_internal.py:173`: in `_gitnexus_env`, DELETE the line `store.save(home, Config())  # base_branch defaults to "main"` — after the flip the base comes from `resolve.project_config(ctx)`, whose absent-file default IS `"main"`. Drop the `store`/`Config` imports if nothing else in the file uses them (grep first).

- `tests/unit/test_notify.py:265`: `(home / "config.json").write_text("{broken")` → `(home / "config.yaml").write_text("{broken")` (invalid YAML; the test asserts exit 0 tolerance — unchanged).
- `tests/unit/test_installer.py:75`: `(home / "config.json").write_text("{}")` → `(home / "config.yaml").write_text("schema_version: 1\n")`.
- `tests/unit/test_installer.py:105-107` and `:153-155`: `store.save(ctx.home, cfg)` → `store.save_global(ctx.home, cfg)` with `cfg = GlobalConfig()` instead of `Config()` (update the import).
- `tests/unit/test_cli.py`: plants no config today (verified — no `store.save`/`config.json` in the file); only ADD one new test for the legacy hint (match the file's existing import of `main`):

```python
def test_gate_hints_legacy_migration(tmp_path, monkeypatch, capsys):
    home = tmp_path / "omchome"
    monkeypatch.setenv("OMC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    home.mkdir(parents=True)
    (home / "config.json").write_text('{"schema_version": 1}')
    assert main(["start", "PROJ-1"]) == 2
    err = capsys.readouterr().err
    assert "legacy" in err and "config.json" in err
```

- [ ] **Step 2: Run tests to verify the updated ones fail**

Run: `uv run pytest tests/unit/test_cli.py tests/unit/test_internal.py tests/unit/test_notify.py tests/unit/test_installer.py -q`
Expected: the flipped fixtures FAIL (code still reads JSON).

- [ ] **Step 3: Flip the four call sites**

`src/omc/cli.py` — import `resolve` alongside `store` (`from .config import resolve, store`) and:

```python
def _load_cfg_or_bail(ctx: ToolContext):
    cfg = resolve.load_effective(ctx)
    if cfg is None:
        hint = _CONFIGURE_HINT
        if store.legacy_config_path(ctx.home).exists():
            hint += " (found legacy ~/.omc/config.json — `omc configure` migrates it)"
        print(f"error: omc is not configured — {hint}.", file=sys.stderr)
        return None
    return cfg
```

`src/omc/internal.py` — add `from .config import resolve` (keep `store` only if still used elsewhere in the file):

```python
# line 37-38, in _rebase_main:
    base = base_arg or resolve.project_config(ctx).worktree.base_branch

# line 109-110, in the gitnexus proxy:
    base = resolve.project_config(ctx).worktree.base_branch
```

(`project_config` never returns `None` — the `if cfg else "main"` dance disappears; the default IS "main".)

`src/omc/notify.py:197`: `cfg = store.load(ctx.home)` → `cfg = store.load_global(ctx.home)`; change `Config` annotations on functions receiving this object to `GlobalConfig` (import from `.config.schema`).

`src/omc/installer.py:68`: `cfg = store.load(ctx.home)` → `cfg = store.load_global(ctx.home)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: ALL PASS.

- [ ] **Step 5: Lint, then commit**

Run: `uvx ruff check src tests`

```bash
git add src/omc/cli.py src/omc/internal.py src/omc/notify.py src/omc/installer.py tests/unit
git commit -m "feat: readers load split YAML config via resolution layer"
```

---

### Task 5: Retire the JSON API; behavior layer + docs + dogfood

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/config/store.py` (delete `config_path`, `load`, `save`)
- Modify: `tests/unit/test_config_store.py` (rework remaining old-API tests onto the split API)
- Modify: `skills/finish/SKILL.md:13-15`, `skills/gitnexus-document/SKILL.md:16-17`, `skills/integrate/SKILL.md:44-46`
- Modify: `README.md:104`
- Create: `.omc/config.yaml` (this repo, committed)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: `store.load`/`store.save`/`store.config_path` NO LONGER EXIST — `load_legacy` and `legacy_config_path` are the only remaining JSON touchpoints.

- [ ] **Step 1: Delete the old API and fix the last tests**

Remove `config_path`, `load`, `save` from `src/omc/config/store.py`. Run `uvx ruff check src` — it must come back clean (any remaining caller is a missed flip; fix it, don't resurrect the API).

In `tests/unit/test_config_store.py`, convert the pre-existing old-API tests to the split API, preserving each test's intent (validation semantics live in `_hydrate` and are schema-independent):

- `test_load_missing_returns_none` → delete (superseded by `test_load_global_missing_returns_none`).
- `test_round_trip` → delete (superseded by the two YAML round-trip tests).
- `test_unknown_key_rejected`: plant `config.yaml` with `schema_version: 1\nbogus: true\n`, call `store.load_global`.
- `test_bad_json_rejected` → delete (superseded by `test_yaml_parse_error_rejected`).
- `test_set_key`: build on `GlobalConfig`+`ProjectConfig` (already covered by `test_set_key_on_split_schemas` — delete).
- `test_set_key_provider_model`, `test_set_key_rejects_unknown_and_sections`, `test_set_key_notifications_*`: replace `Config()` with `GlobalConfig()` (same assertions; `schema_version` reject stays).
- `test_section_must_be_object`: plant `config.yaml` with `llm: oops`, call `load_global`.
- `test_provider_entry_must_be_object`: plant `config.yaml` with `llm:\n  providers:\n    claude: 5\n`, call `load_global`.
- `test_notifications_defaults` / `test_notifications_round_trip`: use `GlobalConfig` + `save_global`/`load_global`.
- `test_notifications_missing_key_defaults`: plant `config.yaml` with `schema_version: 1\n`, call `load_global`.
- `test_hydrate_rejects_bad_notification_values`: plant equivalent YAML (`notifications:\n  enabled: "true"\n`, then `notifications:\n  backend: slack\n`), call `load_global`.

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest tests/unit -q && uvx ruff check src tests`
Expected: ALL PASS, clean lint.

- [ ] **Step 3: Update the three skills**

`skills/finish/SKILL.md` — the base-branch sentence becomes:

```
- cwd is a git repo, on a **feature branch**: not detached HEAD, not the base
  branch. Determine the base from the project's omc config
  (`worktree.base_branch` in the repo's `.omc/config.yaml`) when readable;
  otherwise the repo's default branch
  (`git remote show origin`). Not on a feature branch → explain and stop.
```

`skills/gitnexus-document/SKILL.md` — the provider sentence becomes:

```
Determine the provider: omc's configured default (`llm.default` in
`~/.omc/config.yaml`; if unreadable, ask rather than guess). gitnexus's wiki
```

`skills/integrate/SKILL.md` — the re-set instruction becomes:

```
   - Chain missing/stale → re-run `omc configure`: read the CURRENT default
     from `~/.omc/config.yaml` and re-set it
```

- [ ] **Step 4: README + dogfood config**

`README.md:104`:

```markdown
| `omc configure` | Pick your LLM (global `~/.omc/config.yaml`) and the repo's worktree naming (committed `.omc/config.yaml`) |
```

Create `.omc/config.yaml` at the repo root:

```yaml
schema_version: 1
worktree:
  branch_prefix: feature/
  base_branch: main
```

- [ ] **Step 5: Final full suite, lint, commit**

Run: `uv run pytest tests/unit -q && uvx ruff check src tests`
Expected: ALL PASS.

```bash
git add src/omc/config/store.py tests/unit/test_config_store.py skills/finish/SKILL.md skills/gitnexus-document/SKILL.md skills/integrate/SKILL.md README.md .omc/config.yaml
git commit -m "feat: retire combined config.json API; update skills, README, dogfood config"
```

---

### Task 6: Whole-feature verification

**Model:** top tier

- [ ] **Step 1: Full test suite**

Run: `uv run pytest tests/unit -q && uvx ruff check src tests`
Expected: ALL PASS, clean.

- [ ] **Step 2: Live smoke against the real CLI**

```bash
cd "$(mktemp -d)" && git init -q smoke && cd smoke
env OMC_HOME="$(mktemp -d)/omchome" uv run --project /Users/chriphus/OpenSource-Projects/oh-my-clanker.feature-make-omc-configure-project-local \
  omc configure --defaults --set llm.default=claude --set worktree.base_branch=main
```

Expected: prints `Updated <omchome>/config.yaml` and `Updated <smoke>/.omc/config.yaml`, plugin hints; both files exist with the set values (`cat` them).

- [ ] **Step 3: Spec conformance read-back**

Re-read `docs/superpowers/specs/2026-07-23-make-omc-configure-project-local-design.md` section by section and check each requirement against the diff (`git diff origin/main...HEAD`). Any gap → fix before declaring done.
