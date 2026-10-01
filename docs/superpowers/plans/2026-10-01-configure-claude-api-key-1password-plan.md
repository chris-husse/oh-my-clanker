# Documentation LLM Backend (CLI or validated API key) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `omc configure` choose how GitNexus documentation is generated — the provider CLI (today, default) or the provider's HTTP API with a stored, masked, validated API key — and which model, proving every choice with a real call before saving.

**Architecture:** A `docs` section on `LLMConfig` (provider, backend) plus a new 0600 `secrets.yaml`; one resolver `docs_llm_for(cfg) -> DocsRun` feeds both wiki call sites (argv + child env); a new `ToolContext.http_get` is omc's only network primitive; `src/omc/docsllm.py` holds the configure-time probes and the key redactor. GitNexus is unchanged — it already reads `GITNEXUS_API_KEY` and routes `--provider custom` to its OpenAI-compatible client, which Anthropic's endpoint accepts.

**Tech Stack:** Python 3 stdlib (`dataclasses`, `urllib.request`, `tempfile`, `os.fchmod`), PyYAML (`safe_load`/`safe_dump`), questionary (`password`, `select`), pytest; shell stubs on a restricted `PATH`; Docker E2E via testcontainers.

**Spec:** `docs/superpowers/specs/2026-10-01-configure-claude-api-key-1password-design.md` — read it before any task; section numbers below (§) refer to it.

## Global Constraints

- No new runtime dependency (`pyproject.toml` `dependencies` stays `questionary`, `pyyaml`, `filelock`, `iterm2`): HTTP is `urllib.request`.
- `ToolContext` is the only subprocess/env/**network** boundary: nothing outside `src/omc/toolctx.py` imports `subprocess` or `urllib`.
- Red → green for every change: write the test, run it, watch it fail for the expected reason, then implement. Never `pytest.skip`/`skipif`.
- Stubs run on a restricted `PATH` (`tests/unit/_stubs.py`): shell builtins or absolute paths (`/bin/cat`, `/usr/bin/env`, `/usr/bin/git`) only.
- Exit codes: 0 ok, 1 `OmcError`/`ConfigError`, 2 `Refusal`. Narration on stderr with `→ ✓ · ✗`; `Updated …` lines on stdout.
- The key never appears in argv, stdout, stderr, exception text, or test output. Redact **before** truncating.
- Quick gate after every task: `just check` (unit tests). World build: `just build` (`ruff format --check`, `ruff check`, `uv build`); ruff line length 100, rules `E F I UP B`.
- Never run `omc install`, `uv tool install`, or `gitnexus index`. The E2E (Task 11) is written here but **not run** in the per-task loop — it is token-gated and belongs to `/omc:verify` / finish.
- `schema.py` keeps concrete dataclass field types and **no** `from __future__ import annotations` (`_hydrate` dispatches on `is_dataclass(f.type)`).
- Settled by the user, do not reopen: separate secrets file; no 1Password; unconditional validation (no `--no-validate`); GET-based API proof; key to GitNexus via `GITNEXUS_API_KEY` env; no GitNexus change; no run-time fallback; default model family `sonnet` on both backends.

## Review Focus

1. A hand-edited `secrets.yaml` that is not valid YAML: the error must name the file and **not** quote the parse error (PyYAML echoes the offending line, which may be the key). Test pinned in Task 3.
2. GitNexus stderr longer than 400 characters that contains the key: the failure tail must be redacted before truncation so a key split by `[:400]` cannot leak its prefix. Test pinned in Task 5.
3. A stored key with the backend still `cli`: the key must not reach the child env and the argv must be byte-identical to today. Test pinned in Task 5.
4. A models list whose entries lack `created_at` or carry non-dict entries: family resolution and the picker must sort/skip without raising. Test pinned in Task 7.
5. `--set llm.providers.claude.api_key=` with an empty value, or a value with a trailing newline pasted from a clipboard: refused with `ConfigError`, nothing written. Test pinned in Task 8 (empty) and Task 3 (whitespace).

---

### Task 1: Provider adapter — `api_base_url`, `api_model_family`, `auth_status_argv`

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/providers/base.py` (after `docs_model_default`, before `plugin_update_argvs`)
- Modify: `src/omc/providers/claude.py` (after `docs_model_default`)
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Provider.api_base_url() -> str` (`""` = no API backend; claude `"https://api.anthropic.com/v1/"`), `Provider.api_model_family(alias: str) -> str` (`""` = not a family alias; claude maps `fable`/`opus`/`sonnet` to `claude-fable-`/`claude-opus-`/`claude-sonnet-`), `Provider.auth_status_argv() -> list[str]` (`[]` = this CLI has no login check; claude `["claude", "auth", "status"]`), and the module constant `claude.API_BASE_URL`. `codex.py` is **not** modified (inherits all three defaults).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_providers.py`:

```python
def test_api_backend_adapter_defaults_and_claude_values():
    claude, codex = get_provider("claude"), get_provider("codex")
    # Trailing slash is deliberate: GitNexus strips it; omc joins without "//".
    assert claude.api_base_url() == "https://api.anthropic.com/v1/"
    assert codex.api_base_url() == ""  # no API documentation backend → configure refuses `api`
    assert claude.auth_status_argv() == ["claude", "auth", "status"]
    assert codex.auth_status_argv() == []


@pytest.mark.parametrize(
    ("alias", "prefix"),
    [
        ("fable", "claude-fable-"),
        ("opus", "claude-opus-"),
        ("sonnet", "claude-sonnet-"),
        ("claude-sonnet-5-5", ""),  # a full id is not a family alias
        ("haiku", ""),  # never a family: the cheap tier is never used (tier policy)
        ("", ""),
    ],
)
def test_claude_api_model_family(alias, prefix):
    assert get_provider("claude").api_model_family(alias) == prefix
    assert get_provider("codex").api_model_family(alias) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_providers.py -q -k "api_backend or api_model_family"`
Expected: FAIL with `AttributeError: 'ClaudeProvider' object has no attribute 'api_base_url'`

- [ ] **Step 3: Implement the base defaults**

In `src/omc/providers/base.py`, after `docs_model_default` and before `plugin_update_argvs`:

```python
    def api_base_url(self) -> str:
        """HTTP API base URL for the `api` documentation backend, WITH a trailing
        slash. "" = this provider has no API backend: configure refuses
        `llm.docs.backend=api` for it (spec 2026-10-01 §2.1/§5)."""
        return ""

    def api_model_family(self, alias: str) -> str:
        """Model-id prefix for a CLI family alias ("sonnet" -> "claude-sonnet-").
        "" = not a family alias, treat the value as a full model id. Full ids are
        resolved from the provider's LIVE model list at configure time — there is
        deliberately no static alias→id table anywhere, so nothing rots."""
        return ""

    def auth_status_argv(self) -> list[str]:
        """Argv that reports the CLI's login state as JSON with a `loggedIn`
        boolean; [] = no such command (the headless model probe is the only
        login check). Pure like everything here."""
        return []
```

- [ ] **Step 4: Implement the claude overrides**

In `src/omc/providers/claude.py`, add the module constant after `from .base import Provider`:

```python
# Anthropic's OpenAI-compatible endpoint (docs read 2026-10-01): /chat/completions
# under this base accepts `Authorization: Bearer <key>`, `max_completion_tokens`,
# system messages (hoisted) and streaming — exactly what GitNexus's llm-client
# sends for `--provider custom`. Trailing slash kept: GitNexus strips it itself.
API_BASE_URL = "https://api.anthropic.com/v1/"

# CLI family aliases -> API id prefixes. The newest id with the prefix is picked
# from the live /v1/models list at configure time (docsllm.resolve_model).
_API_FAMILIES = {"fable": "claude-fable-", "opus": "claude-opus-", "sonnet": "claude-sonnet-"}
```

and inside `ClaudeProvider`, after `docs_model_default`:

```python
    def api_base_url(self) -> str:
        return API_BASE_URL

    def api_model_family(self, alias):
        return _API_FAMILIES.get(alias, "")

    def auth_status_argv(self):
        # `claude auth status` prints a JSON object with loggedIn/authMethod/
        # apiProvider (verified live on 2.1.286, 2026-10-01).
        return ["claude", "auth", "status"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_providers.py -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/omc/providers/base.py src/omc/providers/claude.py tests/unit/test_providers.py
git commit -m "feat(providers): pure api_base_url, api_model_family and auth_status_argv adapters"
```

---

### Task 2: `DocsConfig` on `LLMConfig` — load, set and save validation

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/config/schema.py`
- Modify: `src/omc/config/store.py` (`_validate_llm_providers` → public `validate_llm`; new `_validate_docs`; `set_key` `docs` branch; `_hydrate` type checks)
- Test: `tests/unit/test_config_store.py`

**Interfaces:**
- Consumes: Task 1 `get_provider(name).api_base_url()`.
- Produces: `DocsConfig(provider: str = "", backend: str = "cli")`, `LLMConfig.docs`, `store.DOCS_BACKENDS = ("cli", "api")`, `store.validate_llm(llm: LLMConfig) -> None` (public; runs provider checks **and** the docs consistency check; called by `save_global` and `_hydrate`), `store.set_key` accepting `llm.docs.provider` / `llm.docs.backend`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config_store.py`:

```python
def test_docs_defaults_and_round_trip(tmp_path):
    cfg = GlobalConfig()
    assert cfg.llm.docs.provider == "" and cfg.llm.docs.backend == "cli"
    cfg.llm.docs.backend = "api"
    store.save_global(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "docs:" in text and "backend: api" in text
    assert store.load_global(tmp_path).llm.docs.backend == "api"


def test_config_without_docs_section_loads_with_defaults(tmp_path):
    # Every existing config.yaml predates `docs:` — omitted fields take defaults.
    (tmp_path / "config.yaml").write_text("schema_version: 1\nllm:\n  default: claude\n")
    cfg = store.load_global(tmp_path)
    assert cfg.llm.docs.backend == "cli" and cfg.llm.docs.provider == ""


def test_set_key_docs_leaves_and_blank_provider():
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.docs.backend", "api")
    store.set_key(cfg, "llm.docs.provider", "claude")
    assert (cfg.llm.docs.backend, cfg.llm.docs.provider) == ("api", "claude")
    store.set_key(cfg, "llm.docs.provider", "")  # blank = follow llm.default
    assert cfg.llm.docs.provider == ""
    with pytest.raises(ConfigError, match="llm.docs.backend"):
        store.set_key(cfg, "llm.docs.backend", "http")
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.set_key(cfg, "llm.docs.provider", "retired")
    with pytest.raises(ConfigError, match="unknown config key: docs.nope"):
        store.set_key(cfg, "llm.docs.nope", "x")
    with pytest.raises(ConfigError, match="unknown config key: docs.backend.x"):
        store.set_key(cfg, "llm.docs.backend.x", "api")
    with pytest.raises(ConfigError, match="llm.docs is a section"):
        store.set_key(cfg, "llm.docs", "api")  # bare section keeps the generic message


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("llm:\n  docs:\n    backend: http\n", "llm.docs.backend"),
        ("llm:\n  docs:\n    backend: 1\n", "llm.docs.backend"),
        ("llm:\n  docs:\n    provider: [a]\n", "llm.docs.provider"),
        ("llm:\n  docs:\n    provider: retired\n", "retired.*claude.*codex"),
        ("llm:\n  default: codex\n  docs:\n    backend: api\n", "not supported yet"),
        ("llm:\n  docs:\n    provider: codex\n    backend: api\n", "not supported yet"),
    ],
)
def test_load_rejects_bad_docs_values(tmp_path, body, match):
    (tmp_path / "config.yaml").write_text(body)
    with pytest.raises(ConfigError, match=match):
        store.load_global(tmp_path)


def test_api_for_codex_refused_on_save_regardless_of_set_order(tmp_path):
    # --set order is arbitrary: backend=api then default=codex passes every
    # set-time check; the SAVE path must refuse what the next load would reject.
    cfg = GlobalConfig()
    store.set_key(cfg, "llm.docs.backend", "api")
    store.set_key(cfg, "llm.providers.codex.model", "o-x")
    store.set_key(cfg, "llm.default", "codex")
    with pytest.raises(ConfigError, match="codex: API documentation backend not supported yet"):
        store.save_global(tmp_path, cfg)
    assert not (tmp_path / "config.yaml").exists()
    with pytest.raises(ConfigError, match="codex: API documentation backend"):
        store.validate_llm(cfg.llm)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_store.py -q -k "docs or codex_refused"`
Expected: FAIL with `AttributeError: 'LLMConfig' object has no attribute 'docs'` for the round-trip/set/save tests. Note: `test_load_rejects_bad_docs_values` already raises `ConfigError` today because `docs` is an unknown key — its `match=` strings (below) are what make it genuinely red.

- [ ] **Step 3: Add `DocsConfig` to the schema**

In `src/omc/config/schema.py`, before `LLMConfig`:

```python
@dataclass
class DocsConfig:
    """How documentation (GitNexus wiki) is generated — spec 2026-10-01 §2.1.
    provider: which configured provider documents; blank = llm.default.
    backend: "cli" (the provider CLI, today's path) or "api" (the provider's
    HTTP API with the key stored in secrets.yaml)."""

    provider: str = ""
    backend: str = "cli"
```

and in `LLMConfig`, between `default` and `providers`:

```python
    docs: DocsConfig = field(default_factory=DocsConfig)
```

- [ ] **Step 4: Validation in the store**

In `src/omc/config/store.py`:

1. Change the registry import to `from ..providers.registry import get_provider, provider_names` (do not add `DocsConfig` to the schema imports — nothing references it and ruff `F401` would fail `just build`).
2. Add after `_validate_provider`:

```python
DOCS_BACKENDS = ("cli", "api")


def _validate_docs_leaf(name: str, value: object) -> str:
    """Type + closed-set checks for one llm.docs leaf; shared by load and set."""
    if not isinstance(value, str):
        raise ConfigError(f"invalid llm.docs.{name} {value!r}: expected a string")
    if name == "backend" and value not in DOCS_BACKENDS:
        raise ConfigError(f"invalid llm.docs.backend {value!r}: use 'cli' or 'api'")
    if name == "provider" and value:  # blank = follow llm.default
        _validate_provider(value, "llm.docs.provider")
    return value


def validate_llm(cfg: LLMConfig) -> None:
    """Provider names + the docs cross-field check. Runs on LOAD (_hydrate),
    SET-then-SAVE (save_global) — the save path matters because `--set` pairs
    apply in arbitrary order, so `backend=api` then `default=codex` passes every
    per-leaf check and must be refused before it reaches disk."""
    _validate_provider(cfg.default, "llm.default")
    for name in cfg.providers:
        _validate_provider(name, "llm.providers")
    _validate_docs_leaf("provider", cfg.docs.provider)
    _validate_docs_leaf("backend", cfg.docs.backend)
    if cfg.docs.backend == "api":
        name = cfg.docs.provider or cfg.default
        if not get_provider(name).api_base_url():
            raise ConfigError(f"{name}: API documentation backend not supported yet, use cli")
```

3. Delete `_validate_llm_providers` and replace its two callers (`save_global`, the `cls is LLMConfig` block in `_hydrate`) with `validate_llm`.
4. In `set_key`, add a branch directly after the `providers` branch:

```python
    if isinstance(cfg, LLMConfig) and head == "docs" and tail:  # bare `llm.docs` → generic "is a section"
        leaf, _, rest = tail.partition(".")
        if rest or leaf not in ("provider", "backend"):
            raise ConfigError(f"unknown config key: docs.{tail}")
        setattr(cfg.docs, leaf, _validate_docs_leaf(leaf, value))
        # The backend/provider consistency check is the save path's job
        # (validate_llm): --set order is arbitrary, see that docstring.
        return
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_store.py tests/unit/test_configure.py -q`
Expected: all PASS (existing `test_configure` tests still green: no probe exists yet)

- [ ] **Step 6: Commit**

```bash
git add src/omc/config/schema.py src/omc/config/store.py tests/unit/test_config_store.py
git commit -m "feat(config): llm.docs section (provider, backend) validated on load, set and save"
```

---

### Task 3: `SecretsConfig` — `secrets.yaml` at mode 0600, atomic, strict

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/config/schema.py`
- Modify: `src/omc/config/store.py`
- Test: `tests/unit/test_config_store.py`

**Interfaces:**
- Produces: `SecretsConfig(schema_version: int = 1, api_keys: dict[str, str] = field(default_factory=dict, repr=False))`; `store.secrets_path(home) -> Path` (`home / "secrets.yaml"`); `store.validate_api_key(value: object, location: str) -> str` (raises `ConfigError` **without** echoing the value); `store.load_secrets(home) -> SecretsConfig` (missing → empty); `store.save_secrets(home, cfg) -> None` (atomic, 0600); `store.set_api_key(cfg: SecretsConfig, name: str, value: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Add `import stat as _stat` to the top-of-file imports and `SecretsConfig` to the existing `from omc.config.schema import …` line (imports appended mid-file trip ruff `E402`). Then append to `tests/unit/test_config_store.py`:

```python
_KEY = "sk-ant-api03-EXAMPLEKEYabcdefghijklmnop1234"


def test_secrets_round_trip_mode_0600_and_missing_is_empty(tmp_path):
    assert store.load_secrets(tmp_path).api_keys == {}  # missing file, never an error
    store.save_secrets(tmp_path, SecretsConfig(api_keys={"claude": _KEY}))
    path = tmp_path / "secrets.yaml"
    assert _stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load_secrets(tmp_path).api_keys == {"claude": _KEY}
    assert not list(tmp_path.glob("secrets.yaml.*"))  # no temp file left behind


def test_save_secrets_creates_home_and_tightens_a_0644_file(tmp_path):
    home = tmp_path / "fresh-home"  # a first-ever --set api_key= may run before any save
    store.save_secrets(home, SecretsConfig(api_keys={"claude": _KEY}))
    assert (home / "secrets.yaml").exists()
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "secrets.yaml").write_text("schema_version: 1\napi_keys: {}\n")
    (loose / "secrets.yaml").chmod(0o644)
    store.save_secrets(loose, SecretsConfig(api_keys={"claude": _KEY}))
    assert _stat.S_IMODE((loose / "secrets.yaml").stat().st_mode) == 0o600


def test_secrets_repr_never_shows_the_key():
    cfg = SecretsConfig(api_keys={"claude": _KEY})
    assert _KEY not in repr(cfg) and _KEY not in str(cfg)


@pytest.mark.parametrize(
    "body",
    [
        "schema_version: 1\nbogus: 1\n",
        "api_keys: [a]\n",
        "api_keys:\n  claude: 31\n",  # YAML reads 0x1F as an int
        "api_keys:\n  retired: sk-x\n",
        "api_keys:\n  claude: ''\n",
        "api_keys:\n  claude: 'sk-x y'\n",
        "api_keys:\n  claude: 'op://Employee/item/password'\n",
        "- not a mapping\n",
    ],
)
def test_load_secrets_rejects_bad_files(tmp_path, body):
    (tmp_path / "secrets.yaml").write_text(body)
    with pytest.raises(ConfigError, match="secrets.yaml"):
        store.load_secrets(tmp_path)


def test_load_secrets_yaml_error_names_file_but_not_contents(tmp_path):
    # PyYAML quotes the offending line in its message — that line may BE the key.
    (tmp_path / "secrets.yaml").write_text(f"api_keys:\n  claude: {_KEY}\n  : bad\n\tx")
    with pytest.raises(ConfigError) as exc:
        store.load_secrets(tmp_path)
    assert "secrets.yaml" in str(exc.value) and _KEY not in str(exc.value)


@pytest.mark.parametrize(
    "value", ["", "sk x", "sk\tx", "sk-x\n", "op://Employee/item/password", "sk-é", 7]
)
def test_validate_api_key_rejects_without_echoing(value):
    with pytest.raises(ConfigError) as exc:
        store.validate_api_key(value, "llm.providers.claude.api_key")
    assert "llm.providers.claude.api_key" in str(exc.value)
    assert str(value) not in str(exc.value) or value == ""
    if isinstance(value, str) and value.startswith("op://"):
        assert "paste the key itself" in str(exc.value)


def test_set_api_key_validates_provider_and_value():
    cfg = SecretsConfig()
    store.set_api_key(cfg, "claude", _KEY)
    assert cfg.api_keys == {"claude": _KEY}
    with pytest.raises(ConfigError, match="retired.*claude.*codex"):
        store.set_api_key(cfg, "retired", _KEY)
    with pytest.raises(ConfigError):
        store.set_api_key(cfg, "claude", "")
    assert cfg.api_keys == {"claude": _KEY}  # failed sets never mutate


def test_save_secrets_rejects_bad_key_before_writing(tmp_path):
    with pytest.raises(ConfigError):
        store.save_secrets(tmp_path, SecretsConfig(api_keys={"claude": "op://x/y/z"}))
    assert not (tmp_path / "secrets.yaml").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_store.py -q -k "secrets or api_key"`
Expected: FAIL with `ImportError: cannot import name 'SecretsConfig'`

- [ ] **Step 3: Add `SecretsConfig` to the schema**

In `src/omc/config/schema.py`, **above `Config`** (Task 4 adds a `Config.secrets` field typed as this class, and `_hydrate` needs the real class object):

```python
@dataclass
class SecretsConfig:
    """Persisted at <home>/secrets.yaml, mode 0600 — API keys per provider
    (spec 2026-10-01 §2.2). repr=False: a traceback, print(cfg) or a pytest
    assertion diff must never show a key."""

    schema_version: int = 1
    api_keys: dict[str, str] = field(default_factory=dict, repr=False)
```

- [ ] **Step 4: Store functions**

In `src/omc/config/store.py`, add `import contextlib`, `import os`, `import tempfile` to the imports and `SecretsConfig` to the schema import list. Then add after `legacy_config_path`:

```python
def secrets_path(home: Path) -> Path:
    return home / "secrets.yaml"
```

and after `validate_worktree_value`:

```python
def validate_api_key(value: object, location: str) -> str:
    """Key rules shared by load, set and save. The message NEVER contains the
    value: it may be a real key with a typo in it."""
    if not isinstance(value, str) or not value:
        raise ConfigError(f"invalid {location}: expected a non-empty string")
    if value.startswith("op://"):
        raise ConfigError(
            f"invalid {location}: paste the key itself — omc does not resolve 1Password references"
        )
    if any(c.isspace() or not c.isprintable() or ord(c) > 126 for c in value):
        raise ConfigError(f"invalid {location}: must be printable ASCII without whitespace")
    return value


def set_api_key(cfg: SecretsConfig, name: str, value: str) -> None:
    _validate_provider(name, "llm.providers")
    cfg.api_keys[name] = validate_api_key(value, f"llm.providers.{name}.api_key")


def load_secrets(home: Path) -> SecretsConfig:
    """Missing file → empty. Strict otherwise. Plain leaves are hydrated TYPED
    here (unlike _hydrate) because a hand-edited `claude: 0x1F` reads as int."""
    path = secrets_path(home)
    if not path.exists():
        return SecretsConfig()
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        # Deliberately NOT `{exc}`: PyYAML quotes a snippet of the offending line,
        # which for a short key is the whole key. The chained __cause__ still holds
        # it; that is acceptable only because cli.main prints str(exc), never the
        # traceback — keep it that way.
        raise ConfigError(f"invalid YAML in {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config in {path}: expected a mapping")
    unknown = set(data) - {"schema_version", "api_keys"}
    if unknown:
        raise ConfigError(f"unknown config key(s) {sorted(unknown)} in {path}")
    keys = data.get("api_keys", {})
    if not isinstance(keys, dict):
        raise ConfigError(f"invalid value for 'api_keys' in {path}: expected a mapping")
    out: dict[str, str] = {}
    for name, value in keys.items():
        _validate_provider(name, f"api_keys in {path}")
        out[name] = validate_api_key(value, f"api_keys.{name} in {path}")
    return SecretsConfig(schema_version=data.get("schema_version", 1), api_keys=out)


def save_secrets(home: Path, cfg: SecretsConfig) -> None:
    """Atomic 0600 write (the awscreds._store pattern): mkstemp in the
    destination dir, explicit fchmod, write, os.replace. The inode swap is why a
    pre-existing 0644 file ends 0600. The PARENT is not chmodded — <home> hosts
    the managed GitNexus clone and manifests."""
    for name, value in cfg.api_keys.items():
        _validate_provider(name, "api_keys")
        validate_api_key(value, f"api_keys.{name}")
    path = secrets_path(home)
    home.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=home, prefix="secrets.yaml.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(yaml.safe_dump(asdict(cfg), sort_keys=False))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_store.py -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/omc/config/schema.py src/omc/config/store.py tests/unit/test_config_store.py
git commit -m "feat(config): secrets.yaml (0600, atomic, strict) holding per-provider API keys"
```

---

### Task 4: Compose secrets into the runtime `Config`; legacy loader rejects `secrets`

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/config/schema.py` (`Config`)
- Modify: `src/omc/config/store.py` (`load_legacy`)
- Modify: `src/omc/config/resolve.py` (`load_effective`)
- Test: `tests/unit/test_resolve.py`, `tests/unit/test_configure.py`

**Interfaces:**
- Produces: `Config.secrets: SecretsConfig` (`field(default_factory=SecretsConfig, repr=False)`); `resolve.load_effective` fills it from `store.load_secrets(ctx.home)`; `store.load_legacy` raises `ConfigError` on a `secrets` key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_resolve.py`:

```python
from omc.config.schema import SecretsConfig
from omc.errors import ConfigError


def test_load_effective_composes_secrets_and_defaults_to_empty(tmp_path, monkeypatch):
    ctx, home = _ctx(tmp_path, monkeypatch, tmp_path)
    store.save_global(home, GlobalConfig())
    assert resolve.load_effective(ctx).secrets.api_keys == {}
    store.save_secrets(home, SecretsConfig(api_keys={"claude": "sk-test-1234567890"}))
    cfg = resolve.load_effective(ctx)
    assert cfg.secrets.api_keys == {"claude": "sk-test-1234567890"}
    assert "sk-test-1234567890" not in repr(cfg)  # repr=False on Config.secrets too


def test_load_effective_malformed_secrets_is_a_config_error(tmp_path, monkeypatch):
    # Same strict stance as config.yaml: every gated command fails loud, rc 1.
    ctx, home = _ctx(tmp_path, monkeypatch, tmp_path)
    store.save_global(home, GlobalConfig())
    (home / "secrets.yaml").write_text("api_keys:\n  claude: 31\n")
    with pytest.raises(ConfigError, match="secrets.yaml"):
        resolve.load_effective(ctx)
```

(add `import pytest` at the top of the file). Append to `tests/unit/test_configure.py`:

```python
def test_legacy_json_with_secrets_key_is_rejected_not_dropped(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(
        json.dumps({"llm": {"default": "claude"}, "secrets": {"api_keys": {"claude": "sk-x"}}})
    )
    assert main(["configure", "--set", "llm.default=claude"]) == 1
    err = capsys.readouterr().err
    assert "secrets" in err and "config.json" in err
    assert not (home / "secrets.yaml").exists()  # migration never writes the secrets file
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolve.py tests/unit/test_configure.py -q -k "secrets"`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'secrets'` and the legacy test exiting 0

- [ ] **Step 3: Implement**

`src/omc/config/schema.py`, in `Config` (after `notifications`):

```python
    # Composed from <home>/secrets.yaml by resolve.load_effective; never persisted
    # as part of this composite. repr=False: see SecretsConfig.
    secrets: SecretsConfig = field(default_factory=SecretsConfig, repr=False)
```

`src/omc/config/store.py`, in `load_legacy`, after the `isinstance(data, dict)` check:

```python
    if "secrets" in data:
        # Config is also the legacy hydration shape; a secrets key would hydrate
        # and then be silently dropped by the split below. Refuse instead.
        raise ConfigError(f"unexpected 'secrets' in {path}: API keys live in secrets.yaml")
```

`src/omc/config/resolve.py`, `load_effective`:

```python
    return Config(
        llm=gcfg.llm,
        notifications=gcfg.notifications,
        worktree=project_config(ctx).worktree,
        secrets=store.load_secrets(ctx.home),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolve.py tests/unit/test_configure.py tests/unit/test_config_store.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/omc/config/schema.py src/omc/config/store.py src/omc/config/resolve.py tests/unit/test_resolve.py tests/unit/test_configure.py
git commit -m "feat(config): compose secrets.yaml into the runtime Config; legacy json rejects secrets"
```

---

### Task 5: `docs_llm_for` → `DocsRun`; migrate both wiki call sites (cli byte-identical)

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/providers/registry.py` (replace `docs_model_for`)
- Modify: `src/omc/gitnexus.py` (`_run_wiki`)
- Modify: `src/omc/dependency.py` (`run_document`)
- Test: `tests/unit/test_docs_model.py` (rewrite), `tests/unit/test_dependency.py`, `tests/unit/test_gitnexus_refresh.py`

**Interfaces:**
- Consumes: `Config.secrets` (Task 4), `Provider.api_base_url`/`api_model_family` (Task 1).
- Produces: `registry.GITNEXUS_API_KEY_ENV = "GITNEXUS_API_KEY"`; `registry.DocsRun` (frozen dataclass: `provider: str`, `backend: str`, `model: str`, `wiki_args: tuple[str, ...]`, `extra_env: dict[str, str]` (repr=False), `label: str`, method `redact(text: str) -> str`); `registry.docs_llm_for(cfg: Config) -> DocsRun` raising `ConfigError` for `api` without key or with a blank/alias model. `docs_model_for` is **removed**.

- [ ] **Step 1: Rewrite `tests/unit/test_docs_model.py`**

Replace the whole file with:

```python
"""Documentation run resolution (spec 2026-10-01 §4): one helper feeds both
wiki call sites. cli = today's argv byte for byte; api = GitNexus's custom
provider with the key ONLY in the child env."""

import pytest

from omc.config.schema import (
    Config,
    DocsConfig,
    LLMConfig,
    ProviderConfig,
    SecretsConfig,
)
from omc.config.store import set_key
from omc.errors import ConfigError
from omc.providers.registry import GITNEXUS_API_KEY_ENV, docs_llm_for, get_provider

KEY = "sk-ant-test-0123456789abcdef"


def _cfg(*, backend="cli", docs_provider="", default="claude", key=KEY, **provider_kwargs):
    return Config(
        llm=LLMConfig(
            default=default,
            docs=DocsConfig(provider=docs_provider, backend=backend),
            providers={default: ProviderConfig(**provider_kwargs)},
        ),
        secrets=SecretsConfig(api_keys={"claude": key} if key else {}),
    )


def test_provider_docs_defaults():
    assert get_provider("claude").docs_model_default() == "sonnet"
    assert get_provider("codex").docs_model_default() == ""


def test_cli_backend_is_byte_identical_to_today_and_ignores_the_key():
    run = docs_llm_for(_cfg(model="claude-fable-5"))  # session model must never leak
    assert run.wiki_args == ("wiki", "--provider", "claude", "--model", "sonnet")
    assert run.extra_env == {} and run.backend == "cli" and run.label == "claude cli (sonnet)"
    assert run.redact(f"x {KEY} y") == f"x {KEY} y"  # no-op on cli: nothing to hide
    run = docs_llm_for(_cfg(docs_model="opus"))
    assert run.wiki_args == ("wiki", "--provider", "claude", "--model", "opus")


def test_cli_backend_codex_without_model_passes_no_model_flag():
    cfg = Config(llm=LLMConfig(default="codex", providers={}))
    run = docs_llm_for(cfg)
    assert run.wiki_args == ("wiki", "--provider", "codex") and run.label == "codex cli"


def test_docs_provider_overrides_default():
    cfg = Config(
        llm=LLMConfig(
            default="codex",
            docs=DocsConfig(provider="claude"),
            providers={"codex": ProviderConfig(), "claude": ProviderConfig(docs_model="opus")},
        )
    )
    assert docs_llm_for(cfg).wiki_args == ("wiki", "--provider", "claude", "--model", "opus")


def test_api_backend_argv_env_label_and_redact():
    run = docs_llm_for(_cfg(backend="api", docs_model="claude-sonnet-5-5"))
    assert run.wiki_args == (
        "wiki",
        "--provider",
        "custom",
        "--base-url",
        "https://api.anthropic.com/v1/",
        "--model",
        "claude-sonnet-5-5",
    )
    assert run.extra_env == {GITNEXUS_API_KEY_ENV: KEY}
    assert KEY not in " ".join(run.wiki_args)
    assert run.label == "claude api (claude-sonnet-5-5)"
    assert run.redact(f"LLM API error: {KEY} rejected") == "LLM API error: ****** rejected"
    assert KEY not in repr(run)


@pytest.mark.parametrize("docs_model", ["", "sonnet", "opus"])
def test_api_backend_requires_a_full_model_id(docs_model):
    with pytest.raises(ConfigError, match="run omc configure"):
        docs_llm_for(_cfg(backend="api", docs_model=docs_model))


def test_api_backend_requires_a_stored_key():
    with pytest.raises(ConfigError, match="no key is stored.*run omc configure"):
        docs_llm_for(_cfg(backend="api", docs_model="claude-sonnet-5-5", key=""))


def test_set_key_accepts_docs_model():
    cfg = LLMConfig()
    set_key(cfg, "providers.claude.docs_model", "claude-opus-4-8")
    assert cfg.providers["claude"].docs_model == "claude-opus-4-8"
    with pytest.raises(ConfigError):
        set_key(cfg, "providers.claude.nope", "x")
```

- [ ] **Step 2: Add call-site tests**

Append to `tests/unit/test_dependency.py`:

```python
def _api_config(ctx, *, model="claude-sonnet-5-5", key="sk-ant-test-0123456789abcdef"):
    from omc.config import store
    from omc.config.schema import DocsConfig, GlobalConfig, LLMConfig, ProviderConfig, SecretsConfig

    store.save_global(
        ctx.home,
        GlobalConfig(
            llm=LLMConfig(
                default="claude",
                docs=DocsConfig(backend="api"),
                providers={"claude": ProviderConfig(docs_model=model)},
            )
        ),
    )
    if key:
        store.save_secrets(ctx.home, SecretsConfig(api_keys={"claude": key}))
    return key


def _env_echoing_node(tmp_path, nodecalls, *, rc=0, stderr=""):
    node = tmp_path / "bin" / "node"
    err = f'echo "{stderr}" >&2\n' if stderr else ""
    node.write_text(
        f'#!/bin/sh\necho "$@" >> "{nodecalls}"\n'
        f'echo "KEY=$GITNEXUS_API_KEY" >> "{nodecalls}"\npwd >> "{nodecalls}"\n{err}exit {rc}\n'
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)


def test_document_api_backend_key_only_in_child_env(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    key = _api_config(ctx)
    _env_echoing_node(tmp_path, nodecalls)
    assert run_document(ctx, "github.com/foo/bar") == 0
    log = nodecalls.read_text()
    argv_line, key_line = log.splitlines()[0], log.splitlines()[1]
    assert argv_line.endswith(
        "wiki --provider custom --base-url https://api.anthropic.com/v1/ --model claude-sonnet-5-5"
    )
    assert key_line == f"KEY={key}"  # reached the child env ...
    assert key not in argv_line  # ... never the argv
    out = capsys.readouterr()
    assert key not in out.out + out.err
    assert "· via claude api (claude-sonnet-5-5)" in out.err


def test_document_cli_backend_with_a_stored_key_passes_no_key(tmp_path, capsys):
    from omc.config import store
    from omc.config.schema import SecretsConfig

    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)  # default config: backend cli
    store.save_secrets(ctx.home, SecretsConfig(api_keys={"claude": "sk-ant-test-0123456789abcdef"}))
    _env_echoing_node(tmp_path, nodecalls)
    assert run_document(ctx, "github.com/foo/bar") == 0
    lines = nodecalls.read_text().splitlines()
    assert lines[0].endswith("wiki --provider claude --model sonnet") and lines[1] == "KEY="


def test_document_api_failure_tail_is_redacted_before_truncation(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    key = _api_config(ctx)
    # 370 chars of noise, then the key spans offsets 384-412: a naive [:400] would
    # cut it in half and leak `sk-ant-test-0123`; redaction must happen first,
    # and the redacted line (395 chars) keeps its ****** inside the cut.
    _env_echoing_node(tmp_path, nodecalls, rc=1, stderr="x" * 370 + f"LLM API error {key} boom")
    assert run_document(ctx, "github.com/foo/bar") == 1
    err = capsys.readouterr().err
    assert key not in err and key[:12] not in err and "******" in err


def test_document_api_without_key_is_a_clean_error(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    _api_config(ctx, key="")
    assert run_document(ctx, "github.com/foo/bar") == 1
    err = capsys.readouterr().err
    assert "error:" in err and "run omc configure" in err
    assert not nodecalls.exists()  # resolution fails before any gitnexus call
```

Append to `tests/unit/test_gitnexus_refresh.py`:

```python
def _api_cfg(key="sk-ant-test-0123456789abcdef", model="claude-sonnet-5-5"):
    from omc.config.schema import DocsConfig, LLMConfig, ProviderConfig, SecretsConfig

    return Config(
        llm=LLMConfig(
            default="claude",
            docs=DocsConfig(backend="api"),
            providers={"claude": ProviderConfig(docs_model=model)},
        ),
        secrets=SecretsConfig(api_keys={"claude": key} if key else {}),
    )


def test_wiki_api_backend_passes_key_in_env_only(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))  # wiki-unknown
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    node.write_text(node.read_text().replace('echo "$@" >>', 'echo "KEY=$GITNEXUS_API_KEY $@" >>'))
    said = []
    v = refresh_knowledge(
        ctx, _api_cfg(), str(repo), "main", say=said.append, documentation=True, reset=False
    )
    recorded = calls.read_text()
    assert (
        "wiki --provider custom --base-url https://api.anthropic.com/v1/ --model claude-sonnet-5-5"
        in recorded
    )
    assert "KEY=sk-ant-test-0123456789abcdef " in recorded  # the wiki line's env
    assert v.fresh
    assert "→ regenerating documentation via claude api (claude-sonnet-5-5)" in said
    assert not any("sk-ant-test" in s for s in said)


def test_wiki_api_without_key_narrates_and_does_not_crash(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # index fresh, no wiki
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    said = []
    v = refresh_knowledge(
        ctx, _api_cfg(key=""), str(repo), "main", say=said.append, documentation=True, reset=False
    )
    assert not v.fresh
    assert any(s.startswith("✗ documentation: ") and "run omc configure" in s for s in said)
    assert "wiki" not in (calls.read_text() if calls.exists() else "")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_docs_model.py tests/unit/test_dependency.py tests/unit/test_gitnexus_refresh.py -q`
Expected: FAIL with `ImportError: cannot import name 'docs_llm_for'` (and the call-site tests failing on argv/env)

- [ ] **Step 4: Implement `DocsRun` and `docs_llm_for`**

Replace `docs_model_for` in `src/omc/providers/registry.py` with:

```python
from dataclasses import dataclass, field

from ..errors import ConfigError

# The env var GitNexus's llm-client reads first (ahead of OPENAI_API_KEY and its
# saved config). Env, never --api-key: the flag is visible in `ps` for the whole
# run and GitNexus persists it to ~/.gitnexus/config.json; the env key is not.
GITNEXUS_API_KEY_ENV = "GITNEXUS_API_KEY"
_MASK = "******"


@dataclass(frozen=True)
class DocsRun:
    """One resolved documentation run (spec 2026-10-01 §4), consumed by both
    wiki call sites so they cannot drift."""

    provider: str
    backend: str
    model: str
    wiki_args: tuple[str, ...]
    extra_env: dict[str, str] = field(repr=False)
    label: str = ""

    def redact(self, text: str) -> str:
        """Exact-key replacement; no-op on cli. Callers redact BEFORE truncating."""
        key = self.extra_env.get(GITNEXUS_API_KEY_ENV)
        return text.replace(key, _MASK) if key else text


def docs_llm_for(cfg) -> DocsRun:
    """Provider = llm.docs.provider or llm.default. Model = that provider's
    docs_model or its docs default (standard-coding-tier floor); the SESSION
    model is deliberately never consulted. cli → today's argv byte for byte.
    api → GitNexus's `custom` provider against the adapter's base URL with the
    stored key in the child env; the model must already be a full id (configure
    writes it), so this does no alias mapping."""
    name = cfg.llm.docs.provider or cfg.llm.default
    provider = get_provider(name)
    pcfg = cfg.llm.providers.get(name)
    model = (pcfg.docs_model if pcfg else "") or provider.docs_model_default()
    if cfg.llm.docs.backend != "api":
        args = ("wiki", "--provider", name, *(("--model", model) if model else ()))
        label = f"{name} cli ({model})" if model else f"{name} cli"
        return DocsRun(name, "cli", model, args, {}, label)
    key = cfg.secrets.api_keys.get(name, "")
    if not key:
        raise ConfigError(
            f"api documentation backend configured for {name} but no key is stored — "
            "run omc configure"
        )
    # `not api_base_url()` is belt-and-braces: validate_llm already refuses
    # backend=api for such a provider on load, set and save.
    if not model or provider.api_model_family(model) or not provider.api_base_url():
        raise ConfigError(
            f"api documentation backend needs a full model id for {name} (got {model!r}) — "
            "run omc configure"
        )
    args = (
        "wiki",
        "--provider",
        "custom",
        "--base-url",
        provider.api_base_url(),
        "--model",
        model,
    )
    return DocsRun(name, "api", model, args, {GITNEXUS_API_KEY_ENV: key}, f"{name} api ({model})")
```

- [ ] **Step 5: Migrate `gitnexus._run_wiki`**

Add `from .errors import OmcError` to the imports of `src/omc/gitnexus.py` and replace `_run_wiki` with:

```python
def _run_wiki(ctx: ToolContext, cfg: Config, root: Path, say) -> bool:
    from .providers.registry import docs_llm_for

    try:
        run = docs_llm_for(cfg)
    except OmcError as exc:
        # ConfigError (no key / alias model) or get_provider's plain OmcError —
        # reachable only by hand-edited files. Return False like the stall path:
        # `omc watch`'s loop has no OmcError guard and must never crash.
        say(f"✗ documentation: {exc}")
        return False
    say(f"→ regenerating documentation via {run.label}")
    tracker = PageCountTracker(root / ".gitnexus" / "wiki")
    cp, stalled = ctx.run_supervised(
        gitnexus_argv(ctx, *run.wiki_args),
        cwd=str(root),
        heartbeat=tracker.beat,
        stall_after=_WIKI_STALL_SECONDS,
        poll=_WIKI_POLL_SECONDS,
        extra_env=run.extra_env,  # the key reaches exactly this node child
    )
    if stalled:
        say(f"✗ wiki stalled — no progress for {int(_WIKI_STALL_SECONDS)}s; killed")
        return False
    if cp.returncode != 0:
        # redact BEFORE truncating: a key cut in half still leaks its prefix
        say(f"✗ wiki failed: {run.redact((cp.stderr or cp.stdout or '').strip())[:400]}")
    return True  # the recomputed verdict, not the exit code, decides
```

- [ ] **Step 6: Migrate `dependency.run_document`**

In `src/omc/dependency.py`: add `from .config import resolve` next to the existing `from .config import store` import (`OmcError` is already imported) and `from .providers.registry import docs_llm_for` at module level (remove the function-local `docs_model_for` import; no cycle — `dependency.py` already imports `config.store`, which imports the registry). Replace the block from `cfg = store.load_global(ctx.home)` through `wiki_args += ["--model", docs_model]` with:

```python
    cfg = resolve.load_effective(ctx)  # may itself raise ConfigError (malformed
    # config.yaml / secrets.yaml): run_internal's OmcError boundary prints it as
    # `error: …` rc 1, exactly as store.load_global did — do not widen the try.
    if cfg is None:
        print("error: omc is not configured — run `omc configure` first.", file=sys.stderr)
        return 1
    try:
        run = docs_llm_for(cfg)
    except OmcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"· via {run.label}", file=sys.stderr, flush=True)
```

then change the `run_supervised` call to `gitnexus_argv(ctx, *run.wiki_args)` and add `extra_env=run.extra_env,` to it, and change the failure line to:

```python
        # Exact-key redaction FIRST, the userinfo heuristic second (a key containing
        # '@' would otherwise be mangled by _redact and escape run.redact), and
        # truncation last.
        print(
            "error: gitnexus wiki failed: "
            f"{_redact(run.redact((cp.stderr or cp.stdout or '').strip()))[:400]}",
            file=sys.stderr,
        )
```

Note: `resolve.load_effective` also runs `git rev-parse --show-toplevel` in the caller's cwd for the (unused) worktree part. Verified while planning: `wtconfig.repo_root` returns `None` on a non-zero exit **or empty stdout**, so the test's fake git (which prints nothing for `rev-parse`) yields `ProjectConfig()` defaults — no change needed there.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_docs_model.py tests/unit/test_dependency.py tests/unit/test_gitnexus_refresh.py tests/unit/test_watch.py tests/unit/test_internal.py -q`
Expected: all PASS (existing `"--model sonnet" in recorded` assertions still green)

- [ ] **Step 8: Grep for stragglers and commit**

Run: `grep -rn docs_model_for src tests skills` — expected: no output. (`.omc/docs/gitnexus/docs/*.md` still mention `docs_model_for`; those pages are generated by `/omc:document` and are never hand-edited — the mirror lags until the next documentation run.)

```bash
git add src/omc/providers/registry.py src/omc/gitnexus.py src/omc/dependency.py tests/unit/test_docs_model.py tests/unit/test_dependency.py tests/unit/test_gitnexus_refresh.py
git commit -m "feat(docs): docs_llm_for resolves one DocsRun for both wiki call sites (cli unchanged, api via env key)"
```

---

### Task 6: `ToolContext.http_get` — omc's single network primitive

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/toolctx.py` (new method after `stream`)
- Test: `tests/unit/test_toolctx_http.py` (new)

**Interfaces:**
- Produces: `ToolContext.http_get(url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 30.0) -> tuple[int, str]` — returns `(status, body)`; HTTP 4xx/5xx return their status and body; transport failures return `(0, <error text>)`; never raises; proxies from `self.env` (`http_proxy`/`https_proxy`, any case), never `os.environ`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_toolctx_http.py`:

```python
"""ToolContext.http_get — the ONLY network call in omc (spec 2026-10-01 §3.2).
A real in-process loopback HTTP server — deliberately the ONLY socket in
tests/unit (no precedent before this file): this is the one place the transport
itself is under test; everything above it fakes http_get. Hermetic: 127.0.0.1,
no DNS, no external network."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from omc.toolctx import ToolContext


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.startswith("/v1/models?"):
            body = json.dumps({"data": [{"id": "m1"}], "has_more": False}).encode()
            code = 200 if self.headers.get("x-api-key") == "good" else 401
        elif self.path == "/v1/models/m1":
            body, code = b'{"id":"m1"}', 200
        else:
            body, code = b'{"error":{"message":"nope"}}', 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the test log
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _ctx(**env):
    return ToolContext(home=None, env={"HOME": "/nonexistent", **env})  # type: ignore[arg-type]


def test_http_get_returns_status_and_body_for_2xx_and_4xx(server):
    ctx = _ctx()
    status, body = ctx.http_get(f"{server}/v1/models?limit=1000", headers={"x-api-key": "good"})
    assert status == 200 and json.loads(body)["data"][0]["id"] == "m1"
    status, body = ctx.http_get(f"{server}/v1/models?limit=1000", headers={"x-api-key": "bad"})
    assert status == 401  # HTTPError is a RESULT here, never an exception
    status, body = ctx.http_get(f"{server}/v1/models/zzz")
    assert status == 404 and json.loads(body)["error"]["message"] == "nope"


def test_http_get_transport_failure_is_status_zero():
    status, body = _ctx().http_get("http://127.0.0.1:9/v1/models", timeout=2)
    assert status == 0 and body  # connection refused: text, not a traceback


def test_http_get_takes_proxies_from_ctx_env_not_os_environ(server, monkeypatch):
    # urllib's BYPASS check (no_proxy) still reads os.environ / system settings;
    # clear every proxy var so the host cannot make 127.0.0.1 bypass the proxy.
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    # A dead proxy in ctx.env must be honoured (→ transport failure) ...
    status, _ = _ctx(http_proxy="http://127.0.0.1:9").http_get(f"{server}/v1/models/m1", timeout=2)
    assert status == 0
    # ... and a dead proxy ONLY in os.environ must be ignored.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    status, _ = _ctx().http_get(f"{server}/v1/models/m1", timeout=2)
    assert status == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_toolctx_http.py -q`
Expected: FAIL with `AttributeError: 'ToolContext' object has no attribute 'http_get'`

- [ ] **Step 3: Implement**

In `src/omc/toolctx.py`, add `import urllib.error` and `import urllib.request` to the imports, and add after `stream`:

```python
    def http_get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, str]:
        """GET ``url``; returns ``(status, body)``. omc's ONLY network primitive —
        it lives here so the subprocess/env/network boundary stays single and
        tests fake one seam (like ``run``/``run_bounded``).

        Never raises: an HTTP error status is a RESULT (``(401, body)``), and a
        transport failure (refused, DNS, timeout) is ``(0, <error text>)``.
        The proxy MAP comes only from ``self.env`` (``http_proxy``/``https_proxy``,
        any case), never ``os.environ`` — with an explicit map urllib adds no
        system proxies. Its bypass check (``no_proxy``/macOS exclusions) is
        urllib's own and still consults the process environment; in production
        ``self.env`` is a copy of ``os.environ`` so the two agree. The caller
        owns redaction: this layer never sees what a header means.
        """
        proxies = {
            scheme: self.env[k]
            for scheme in ("http", "https")
            for k in (f"{scheme}_proxy", f"{scheme.upper()}_PROXY")
            if self.env.get(k)
        }
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
        try:
            with opener.open(req, timeout=timeout) as resp:  # noqa: S310 - https/http only, caller-built URL
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return 0, str(exc)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_toolctx_http.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/omc/toolctx.py tests/unit/test_toolctx_http.py
git commit -m "feat(toolctx): http_get — the single network primitive, proxies from ctx.env, never raises"
```

---

### Task 7: `docsllm` probes + key redaction; `make_claude_stub` learns `auth status` and the probe prompt

**Model:** heavy coding tier

**Files:**
- Create: `src/omc/docsllm.py`
- Modify: `tests/unit/_stubs.py` (`make_claude_stub`)
- Test: `tests/unit/test_docsllm.py` (new)

**Interfaces:**
- Consumes: `ToolContext.http_get` (Task 6), `ToolContext.run_bounded`, Task 1 adapter methods.
- Produces (all in `src/omc/docsllm.py`):
  - constants `ANTHROPIC_VERSION = "2023-06-01"`, `MODELS_PAGE_LIMIT = 1000`, `HEADLESS_PROBE_PROMPT = "Reply with exactly OK."`, `HEADLESS_PROBE_TIMEOUT = 120.0`, `MASK = "******"`
  - `class ProbeFailed(OmcError)` (rc 1; message already redacted)
  - `mask_key(key: str) -> str` (`******` + last 4; `********` under 8 chars)
  - `redact(text: str, key: str) -> str`
  - `@dataclass(frozen=True) class ModelInfo: id: str; created_at: str`
  - `api_connection_probe(ctx, provider: str, key: str) -> tuple[bool, str, list[ModelInfo]]`
  - `model_choices(models: list[ModelInfo]) -> list[str]` (claude-* only, Haiku dropped, newest first)
  - `resolve_model(provider: str, model: str, models: list[ModelInfo]) -> str` (alias → newest id with the family prefix; full id passes through; raises `ProbeFailed` when the family has no model)
  - `api_model_probe(ctx, provider: str, key: str, model_id: str) -> tuple[bool, str]`
  - `cli_connection_probe(ctx, provider: str) -> tuple[bool, str]`
  - `cli_model_probe(ctx, provider: str, model: str) -> tuple[bool, str]`
  - `@dataclass(frozen=True) class DocsSelection: provider: str; backend: str; model: str; key: str`
  - `validate_key_only(ctx, provider: str, key: str, *, say) -> None` (connection probe only; raises `ProbeFailed`)
  - `validate_selection(ctx, sel: DocsSelection, *, say) -> str` (both probes; returns the model to persist — the resolved full id on `api`, `sel.model` unchanged on `cli`; raises `ProbeFailed`)
- `make_claude_stub` gains kwargs `auth_logged_in: bool = True`, `headless_reply: str = "OK"`, `headless_rc: int = 0`; it answers `auth status` with JSON and `-p "Reply with exactly OK."` with `headless_reply`/`headless_rc`; every other argv is unchanged (existing slug/verdict tests keep their fallthrough).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_docsllm.py`:

```python
"""Configure-time documentation probes (spec 2026-10-01 §3.2). http_get is
faked at the ToolContext seam; the claude CLI is a PATH stub."""

import inspect
import json

import pytest

from omc import docsllm
from omc.docsllm import DocsSelection, ModelInfo, ProbeFailed
from omc.toolctx import ToolContext

from ._stubs import make_claude_stub, stub_env

KEY = "sk-ant-api03-SECRETKEYabcdefghijklmnop1234"
MODELS = {
    "data": [
        {"id": "claude-sonnet-5", "created_at": "2026-02-01T00:00:00Z"},
        {"id": "claude-haiku-4-5", "created_at": "2026-05-01T00:00:00Z"},
        {"id": "claude-sonnet-5-5", "created_at": "2026-06-01T00:00:00Z"},
        {"id": "claude-opus-5-5", "created_at": "2026-04-01T00:00:00Z"},
        {"id": "claude-fable-5-1", "created_at": "2026-08-01T00:00:00Z"},
        {"id": "legacy-thing", "created_at": "2026-09-01T00:00:00Z"},
    ],
    "has_more": True,
}


def _ctx(tmp_path, responses, **stub_kwargs):
    """ctx whose http_get answers from `responses` {url-suffix: (status, body)} and
    records every call; claude is a PATH stub."""
    bindir = tmp_path / "bin"
    calls = make_claude_stub(bindir, **stub_kwargs)
    ctx = ToolContext.from_env(stub_env(bindir))
    seen = []

    def fake_http_get(url, *, headers=None, timeout=30.0):
        seen.append((url, dict(headers or {})))
        for suffix, (status, body) in responses.items():
            if url.endswith(suffix):
                return status, body if isinstance(body, str) else json.dumps(body)
        return 404, '{"error":{"message":"no route"}}'

    ctx.http_get = fake_http_get  # type: ignore[method-assign]
    return ctx, seen, calls


def test_mask_and_redact():
    assert docsllm.mask_key(KEY) == "******1234"
    assert docsllm.mask_key("short") == "********"
    assert docsllm.redact(f"a {KEY} b {KEY}", KEY) == "a ****** b ******"
    assert docsllm.redact("untouched", "") == "untouched"


def test_api_connection_probe_success_sends_headers_and_lists_models(tmp_path):
    ctx, seen, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (200, MODELS)})
    ok, detail, models = docsllm.api_connection_probe(ctx, "claude", KEY)
    assert ok and detail == "key accepted (6 models)"
    assert [m.id for m in models][:2] == ["claude-sonnet-5", "claude-haiku-4-5"]
    url, headers = seen[0]
    assert url == "https://api.anthropic.com/v1/models?limit=1000"  # no "//"
    assert headers == {"x-api-key": KEY, "anthropic-version": "2023-06-01"}


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": {"message": "invalid x-api-key"}}, "key rejected by api.anthropic.com (HTTP 401)"),
        (403, {"error": {"message": "forbidden"}}, "key rejected by api.anthropic.com (HTTP 403)"),
        (
            400,
            {"error": {"message": "anthropic-workspace-id header is required"}},
            "api.anthropic.com answered HTTP 400: anthropic-workspace-id header is required",
        ),
        (0, f"<urlopen error> {KEY}", "could not reach api.anthropic.com: <urlopen error> ******"),
        (200, "not json", "api.anthropic.com returned an unexpected model list"),
    ],
)
def test_api_connection_probe_failures_are_redacted(tmp_path, status, body, expected):
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (status, body)})
    ok, detail, models = docsllm.api_connection_probe(ctx, "claude", KEY)
    assert not ok and detail == expected and models == [] and KEY not in detail


def test_error_bodies_are_redacted_before_truncation(tmp_path):
    # A non-JSON error body echoing the key many times: the 200-char cut must
    # never expose even an 8-char slice of it (spec §3.4 redact-before-truncate).
    body = f"<{KEY}>" * 60
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (400, body), "/v1/models/m": (500, body)})
    _, detail, _ = docsllm.api_connection_probe(ctx, "claude", KEY)
    assert "******" in detail and not any(KEY[i : i + 8] in detail for i in range(len(KEY) - 7))
    _, detail = docsllm.api_model_probe(ctx, "claude", KEY, "m")
    assert "******" in detail and not any(KEY[i : i + 8] in detail for i in range(len(KEY) - 7))


def test_model_choices_filters_haiku_and_foreign_sorts_newest_first():
    models = [ModelInfo(m["id"], m["created_at"]) for m in MODELS["data"]]
    assert docsllm.model_choices(models) == [
        "claude-fable-5-1",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-sonnet-5",
    ]


def test_model_choices_tolerates_missing_created_at():
    models = [ModelInfo("claude-sonnet-5-5", ""), ModelInfo("claude-opus-5-5", "2026-04-01")]
    assert docsllm.model_choices(models) == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_resolve_model_alias_to_newest_family_member_full_id_passes_through():
    models = [ModelInfo(m["id"], m["created_at"]) for m in MODELS["data"]]
    assert docsllm.resolve_model("claude", "sonnet", models) == "claude-sonnet-5-5"
    assert docsllm.resolve_model("claude", "opus", models) == "claude-opus-5-5"
    assert docsllm.resolve_model("claude", "claude-sonnet-5", models) == "claude-sonnet-5"
    with pytest.raises(ProbeFailed, match="no sonnet models"):
        docsllm.resolve_model("claude", "sonnet", [ModelInfo("claude-opus-5-5", "x")])


def test_api_model_probe_hits_the_per_id_endpoint(tmp_path):
    ctx, seen, _ = _ctx(tmp_path, {"/v1/models/claude-sonnet-5-5": (200, {"id": "claude-sonnet-5-5"})})
    assert docsllm.api_model_probe(ctx, "claude", KEY, "claude-sonnet-5-5") == (
        True,
        "claude-sonnet-5-5 works",
    )
    assert seen[0][0] == "https://api.anthropic.com/v1/models/claude-sonnet-5-5"
    ok, detail = docsllm.api_model_probe(ctx, "claude", KEY, "claude-nope")
    assert not ok and detail == "model 'claude-nope' is not available to this key"


def test_cli_connection_probe_logged_in_and_not(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    assert docsllm.cli_connection_probe(ctx, "claude") == (True, "claude is logged in")
    assert "auth status" in calls.read_text()
    ctx, _, _ = _ctx(tmp_path / "b", {}, auth_logged_in=False)
    ok, detail = docsllm.cli_connection_probe(ctx, "claude")
    assert not ok and detail == "claude is not logged in — run claude auth login"
    assert "Not logged in" not in detail  # the E2E harness fails on that exact casing
    assert docsllm.cli_connection_probe(ctx, "codex") == (True, "no login check for codex")


def test_cli_model_probe_runs_one_headless_call(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    assert docsllm.cli_model_probe(ctx, "claude", "opus") == (True, "opus works")
    assert "-p Reply with exactly OK. --output-format text --model opus" in calls.read_text()
    ctx, _, _ = _ctx(tmp_path / "b", {}, headless_rc=1)
    ok, detail = docsllm.cli_model_probe(ctx, "claude", "opus")
    assert not ok and detail.startswith("claude rejected model 'opus'")
    ctx, _, _ = _ctx(tmp_path / "c", {}, headless_reply="")
    ok, detail = docsllm.cli_model_probe(ctx, "claude", "opus")
    assert not ok and detail == "claude returned no output for model 'opus'"


def test_cli_model_probe_timeout_and_missing_cli(tmp_path, monkeypatch):
    ctx, _, _ = _ctx(tmp_path, {})

    def boom(argv, *, timeout, cwd=None, extra_env=None):
        raise TimeoutError("command timed out after 120s")

    monkeypatch.setattr(ctx, "run_bounded", boom)
    assert docsllm.cli_model_probe(ctx, "claude", "opus") == (
        False,
        "claude did not answer within 120s",
    )
    ctx2 = ToolContext.from_env(stub_env(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    ok, detail = docsllm.cli_model_probe(ctx2, "claude", "opus")
    assert not ok and detail == "claude CLI not found on PATH"


def test_validate_selection_api_resolves_and_narrates(tmp_path):
    ctx, seen, _ = _ctx(
        tmp_path,
        {
            "/v1/models?limit=1000": (200, MODELS),
            "/v1/models/claude-sonnet-5-5": (200, {"id": "claude-sonnet-5-5"}),
        },
    )
    said = []
    model = docsllm.validate_selection(
        ctx, DocsSelection("claude", "api", "", KEY), say=said.append
    )
    assert model == "claude-sonnet-5-5"  # blank docs_model → default family sonnet → newest
    assert said == [
        "→ checking the key against api.anthropic.com",
        "✓ key accepted (6 models)",
        "→ validating claude-sonnet-5-5 via api",
        "✓ claude-sonnet-5-5 works",
    ]
    assert KEY not in "".join(said)


def test_validate_selection_api_failure_raises_redacted(tmp_path):
    ctx, _, _ = _ctx(tmp_path, {"/v1/models?limit=1000": (401, {"error": {"message": "x"}})})
    said = []
    with pytest.raises(ProbeFailed, match=r"key rejected by api.anthropic.com \(HTTP 401\)"):
        docsllm.validate_selection(ctx, DocsSelection("claude", "api", "", KEY), say=said.append)
    assert said[-1] == "✗ key rejected by api.anthropic.com (HTTP 401)"


def test_validate_selection_cli_keeps_alias_and_runs_both_probes(tmp_path):
    ctx, _, calls = _ctx(tmp_path, {})
    said = []
    assert docsllm.validate_selection(ctx, DocsSelection("claude", "cli", "opus", ""), say=said.append) == "opus"
    log = calls.read_text()
    assert "auth status" in log and "--model opus" in log
    assert said == [
        "→ checking claude login",
        "✓ claude is logged in",
        "→ validating opus via cli",
        "✓ opus works",
    ]


def test_validate_key_only_runs_the_connection_probe_alone(tmp_path):
    ctx, seen, calls = _ctx(tmp_path, {"/v1/models?limit=1000": (200, MODELS)})
    docsllm.validate_key_only(ctx, "claude", KEY, say=lambda m: None)
    assert len(seen) == 1 and not calls.exists()
    ctx, _, _ = _ctx(tmp_path / "b", {"/v1/models?limit=1000": (403, "")})
    with pytest.raises(ProbeFailed):
        docsllm.validate_key_only(ctx, "claude", KEY, say=lambda m: None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_docsllm.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'docsllm' from 'omc'` (the first import line; the module does not exist yet)

- [ ] **Step 3: Extend `make_claude_stub`**

In `tests/unit/_stubs.py`, add the kwargs `auth_logged_in: bool = True, headless_reply: str = "OK", headless_rc: int = 0` to `make_claude_stub`'s signature (after `available_omc`), extend the docstring with "`auth status` answers JSON with ``loggedIn``; `-p "Reply with exactly OK."` (the docsllm probe prompt) answers ``headless_reply``/``headless_rc``; other `-p` prompts keep the generic fallthrough", and insert into the script string right after the `--version` branch:

```python
if args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": {auth_logged_in!r}, "authMethod": "claude.ai", "apiProvider": "firstParty"}}))
    sys.exit(0)
if args[:2] == ["-p", {probe_prompt!r}]:
    sys.stdout.write({headless_reply!r} + "\\n"); sys.exit({headless_rc})
```

and define `probe_prompt = "Reply with exactly OK."` as a local in `make_claude_stub` with a comment `# == omc.docsllm.HEADLESS_PROBE_PROMPT (kept literal: tests/_stubs must not import omc at write time)`. Add a test to `tests/unit/test_docsllm.py` pinning the two apart:

```python
def test_stub_prompt_matches_the_probe_constant():
    # _stubs keeps the prompt as a literal on purpose (it never imports omc);
    # this pins the two together.
    assert docsllm.HEADLESS_PROBE_PROMPT in inspect.getsource(make_claude_stub)
```

- [ ] **Step 4: Implement `src/omc/docsllm.py`**

```python
"""Configure-time validation of the documentation LLM backend (spec
2026-10-01 §3.2). Two probes per backend, each returning ``(ok, detail)`` like
``toolctx.tool_version`` — never raising from the subprocess/HTTP layer. All
I/O goes through ToolContext; this module does no subprocess or network calls
itself. It also OWNS key redaction: ToolContext never knows the key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from .errors import OmcError
from .providers.registry import get_provider
from .toolctx import ToolContext

ANTHROPIC_VERSION = "2023-06-01"
MODELS_PAGE_LIMIT = 1000  # the list endpoint is paginated; ask for the maximum page
HEADLESS_PROBE_PROMPT = "Reply with exactly OK."
HEADLESS_PROBE_TIMEOUT = 120.0
AUTH_STATUS_TIMEOUT = 30.0
MASK = "******"


class ProbeFailed(OmcError):
    """A documentation probe failed. The message is ALREADY redacted — cli.main
    prints it verbatim as `error: …` (rc 1)."""


def mask_key(key: str) -> str:
    """`******` + the last four characters; all asterisks under eight."""
    return f"{MASK}{key[-4:]}" if len(key) >= 8 else "********"


def redact(text: str, key: str) -> str:
    """Exact-substring replacement — sufficient given the key rules (printable
    ASCII, no whitespace). Callers redact BEFORE truncating."""
    return text.replace(key, MASK) if key else text


@dataclass(frozen=True)
class ModelInfo:
    id: str
    created_at: str


@dataclass(frozen=True)
class DocsSelection:
    provider: str
    backend: str  # "cli" | "api"
    model: str  # cli: alias or id or ""; api: alias or full id or ""
    key: str  # "" on cli


Say = Callable[[str], object]


def _base(provider: str) -> str:
    return get_provider(provider).api_base_url()


def _host(base: str) -> str:
    return base.split("/")[2]


def _models_url(base: str) -> str:
    return base.rstrip("/") + "/models"  # never "//models": GitNexus-style join


def _headers(key: str) -> dict[str, str]:
    return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}


def _error_message(body: str, key: str) -> str:
    """Anthropic's error.message, else the raw body — REDACTED before the cut."""
    body = redact(body, key)
    try:
        return str(json.loads(body)["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return body.strip()[:200]


def api_connection_probe(
    ctx: ToolContext, provider: str, key: str
) -> tuple[bool, str, list[ModelInfo]]:
    """GET /models proves the key and yields the picker's list."""
    base = _base(provider)
    host = _host(base)
    status, body = ctx.http_get(f"{_models_url(base)}?limit={MODELS_PAGE_LIMIT}", headers=_headers(key))
    if status in (401, 403):
        return False, f"key rejected by {host} (HTTP {status})", []
    if status == 0:
        return False, redact(f"could not reach {host}: {body}", key), []
    if not 200 <= status < 300:
        return False, f"{host} answered HTTP {status}: {_error_message(body, key)}", []
    try:
        data = json.loads(body)["data"]
        models = [
            ModelInfo(str(m["id"]), str(m.get("created_at", "")))
            for m in data
            if isinstance(m, dict) and "id" in m
        ]
    except (ValueError, KeyError, TypeError):
        return False, f"{host} returned an unexpected model list", []
    return True, f"key accepted ({len(models)} models)", models


def _newest_first(models: list[ModelInfo]) -> list[ModelInfo]:
    return sorted(models, key=lambda m: m.created_at, reverse=True)


def model_choices(models: list[ModelInfo]) -> list[str]:
    """Picker entries: claude-* only, Haiku dropped (the cheap tier is never
    used — AGENTS.md tier policy), newest first."""
    picked = [m for m in models if m.id.startswith("claude-") and "haiku" not in m.id]
    return [m.id for m in _newest_first(picked)]


def resolve_model(provider: str, model: str, models: list[ModelInfo]) -> str:
    """A family alias → the newest fetched id with that family's prefix (so the
    persisted docs_model is a full id and nothing rots); a full id passes through."""
    prefix = get_provider(provider).api_model_family(model)
    if not prefix:
        return model
    family = [m for m in models if m.id.startswith(prefix) and "haiku" not in m.id]
    if not family:
        raise ProbeFailed(f"no {model} models available to this key")
    return _newest_first(family)[0].id


def api_model_probe(ctx: ToolContext, provider: str, key: str, model_id: str) -> tuple[bool, str]:
    """Always the per-id GET, never list membership (pagination-proof)."""
    base = _base(provider)
    status, body = ctx.http_get(f"{_models_url(base)}/{model_id}", headers=_headers(key))
    if 200 <= status < 300:
        return True, f"{model_id} works"
    if status == 404:
        return False, f"model {model_id!r} is not available to this key"
    if status == 0:
        return False, redact(f"could not reach {_host(base)}: {body}", key)
    return False, f"{_host(base)} answered HTTP {status}: {_error_message(body, key)}"


def cli_connection_probe(ctx: ToolContext, provider: str) -> tuple[bool, str]:
    """`claude auth status` → JSON with loggedIn. Lowercase "not logged in" on
    purpose: the E2E harness fails any output containing `Not logged in`."""
    argv = get_provider(provider).auth_status_argv()
    if not argv:
        return True, f"no login check for {provider}"  # codex: the model probe is the check
    try:
        cp = ctx.run_bounded(argv, timeout=AUTH_STATUS_TIMEOUT)
    except FileNotFoundError:
        return False, f"{provider} CLI not found on PATH"
    except OSError as exc:  # run_bounded's TimeoutError is an OSError subclass
        return False, f"{provider} auth status failed: {exc}"
    try:
        status = json.loads(cp.stdout or "")
    except ValueError:
        status = None
    if cp.returncode != 0 or not isinstance(status, dict) or status.get("loggedIn") is not True:
        return False, f"{provider} is not logged in — run {provider} auth login"
    return True, f"{provider} is logged in"


def cli_model_probe(ctx: ToolContext, provider: str, model: str) -> tuple[bool, str]:
    """One headless turn on the user's login. run_bounded, not run: `claude -p`
    spawns MCP grandchildren that a plain subprocess timeout would orphan."""
    p = get_provider(provider)
    argv = p.headless_argv(HEADLESS_PROBE_PROMPT, model=model)
    shown = model or "default model"
    try:
        # cwd=ctx.home: run the probe OUTSIDE whatever project the user typed
        # `omc configure` in, so Claude loads no project MCP servers/hooks.
        cp = ctx.run_bounded(
            argv, timeout=HEADLESS_PROBE_TIMEOUT, cwd=str(ctx.home), extra_env=p.title_env()
        )
    except FileNotFoundError:
        return False, f"{provider} CLI not found on PATH"
    except TimeoutError:
        return False, f"{provider} did not answer within {int(HEADLESS_PROBE_TIMEOUT)}s"
    except OSError as exc:
        return False, f"{provider} failed to launch: {exc}"
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "").strip()[:200]
        return False, f"{provider} rejected model {shown!r}: {tail}"
    if not (cp.stdout or "").strip():
        return False, f"{provider} returned no output for model {shown!r}"
    return True, f"{shown} works"


def _step(say: Say, ok: bool, detail: str) -> None:
    say(("✓ " if ok else "✗ ") + detail)
    if not ok:
        raise ProbeFailed(detail)


def validate_key_only(ctx: ToolContext, provider: str, key: str, *, say: Say) -> None:
    """The key changed while the backend is not `api`: prove the key anyway."""
    say(f"→ checking the key against {_host(_base(provider))}")
    ok, detail, _ = api_connection_probe(ctx, provider, key)
    _step(say, ok, detail)


def validate_selection(ctx: ToolContext, sel: DocsSelection, *, say: Say) -> str:
    """Both probes for the selection. Returns the model to PERSIST: on api the
    resolved full id (alias → newest family member), on cli sel.model as is."""
    if sel.backend == "api":
        say(f"→ checking the key against {_host(_base(sel.provider))}")
        ok, detail, models = api_connection_probe(ctx, sel.provider, sel.key)
        _step(say, ok, detail)
        wanted = sel.model or get_provider(sel.provider).docs_model_default()
        model = resolve_model(sel.provider, wanted, models)
        say(f"→ validating {model} via api")
        ok, detail = api_model_probe(ctx, sel.provider, sel.key, model)
        _step(say, ok, detail)
        return model
    say(f"→ checking {sel.provider} login")
    ok, detail = cli_connection_probe(ctx, sel.provider)
    _step(say, ok, detail)
    say(f"→ validating {sel.model or 'default model'} via cli")
    ok, detail = cli_model_probe(ctx, sel.provider, sel.model)
    _step(say, ok, detail)
    return sel.model
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_docsllm.py tests/unit/test_start.py tests/unit/test_start_mutex.py tests/unit/test_plugin.py tests/unit/test_installer.py tests/unit/test_configure.py -q`
Expected: all PASS (stub fallthrough intact for the slug/verdict tests). `ctx.home` must exist for `cwd=`: `ToolContext.from_env(stub_env(bindir))` gives `<bindir.parent>/.omc`, so the `_ctx` helper in the test must `mkdir` it — add `(bindir.parent / ".omc").mkdir(exist_ok=True)` to `_ctx`.

- [ ] **Step 6: Commit**

```bash
git add src/omc/docsllm.py tests/unit/_stubs.py tests/unit/test_docsllm.py
git commit -m "feat(docsllm): configure-time API/CLI probes with live model resolution and key redaction"
```

---

### Task 8: `omc configure --set` — route `api_key`, enforce order, probe before any write

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/configure.py` (`run_configure` non-interactive branch; new helpers `_set_pairs`, `_docs_changed`, `_probe_docs`)
- Test: `tests/unit/test_configure.py`

**Interfaces:**
- Consumes: Task 3 (`store.load_secrets`, `save_secrets`, `set_api_key`, `secrets_path`), Task 2 (`store.validate_llm`), Task 7 (`docsllm.validate_selection`, `validate_key_only`, `DocsSelection`, `mask_key`, `ProbeFailed`).
- Produces: the `--set` contract — `llm.providers.<name>.api_key=…` lands in `secrets.yaml` only; order apply → `validate_llm` → refuse `api` without key (`ConfigError`) → probes (only when a documentation key changed) → write; stdout `Updated <home>/secrets.yaml (mode 0600)`; stderr `✓ <name> API key stored (******abcd)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_configure.py`:

```python
from omc.toolctx import ToolContext

KEY = "sk-ant-api03-SECRETKEYabcdefghijklmnop1234"
_MODELS = json.dumps(
    {
        "data": [
            {"id": "claude-sonnet-5", "created_at": "2026-02-01"},
            {"id": "claude-sonnet-5-5", "created_at": "2026-06-01"},
            {"id": "claude-haiku-4-5", "created_at": "2026-07-01"},
        ]
    }
)


def _fake_http(monkeypatch, *, list_status=200):
    """Fake the ToolContext seam for every ctx main() builds; records URLs."""
    seen = []

    def http_get(self, url, *, headers=None, timeout=30.0):
        seen.append(url)
        if "/models?" in url:
            return list_status, _MODELS if list_status == 200 else '{"error":{"message":"bad key"}}'
        if url.endswith("/models/claude-sonnet-5-5") or url.endswith("/models/claude-sonnet-5"):
            return 200, '{"id":"x"}'
        return 404, '{"error":{"message":"no"}}'

    monkeypatch.setattr(ToolContext, "http_get", http_get)
    return seen


def _claude_calls(home):
    return _CLAUDE_CALLS[str(home)].read_text() if _CLAUDE_CALLS[str(home)].exists() else ""


def test_set_api_key_routes_to_secrets_and_probes_the_key(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", f"llm.providers.claude.api_key={KEY}"]) == 0
    assert store.load_secrets(home).api_keys == {"claude": KEY}
    assert oct(os.stat(home / "secrets.yaml").st_mode & 0o777) == "0o600"
    assert KEY not in (home / "config.yaml").read_text()
    out = capsys.readouterr()
    assert f"Updated {home / 'secrets.yaml'} (mode 0600)" in out.out
    assert "✓ claude API key stored (******1234)" in out.err
    assert KEY not in out.out + out.err
    assert len(seen) == 1 and seen[0].endswith("/v1/models?limit=1000")  # key probe, backend still cli
    assert " -p " not in f" {_claude_calls(home)} " and "auth status" not in _claude_calls(home)


def test_set_backend_api_resolves_default_model_and_writes_full_id(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    rc = main(
        ["configure", "--set", "llm.docs.backend=api", "--set", f"llm.providers.claude.api_key={KEY}"]
    )
    assert rc == 0
    cfg = store.load_global(home)
    assert cfg.llm.docs.backend == "api"
    assert cfg.llm.providers["claude"].docs_model == "claude-sonnet-5-5"  # sonnet → newest, full id
    assert any(u.endswith("/v1/models/claude-sonnet-5-5") for u in seen)
    err = capsys.readouterr().err
    assert "→ validating claude-sonnet-5-5 via api" in err and "✓ claude-sonnet-5-5 works" in err


def test_set_backend_api_without_key_is_refused_before_any_probe(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", "llm.docs.backend=api"]) == 1
    assert "llm.providers.claude.api_key is required for the api backend" in capsys.readouterr().err
    assert not (home / "config.yaml").exists() and not (home / "secrets.yaml").exists()
    assert seen == []


def test_probe_failure_writes_nothing_and_never_echoes_the_key(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _fake_http(monkeypatch, list_status=401)
    rc = main(
        ["configure", "--set", "llm.docs.backend=api", "--set", f"llm.providers.claude.api_key={KEY}"]
    )
    assert rc == 1
    out = capsys.readouterr()
    assert "error: key rejected by api.anthropic.com (HTTP 401)" in out.err
    assert KEY not in out.out + out.err
    assert not (home / "config.yaml").exists() and not (home / "secrets.yaml").exists()


def test_cli_docs_model_change_runs_login_and_headless_probes(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", "llm.providers.claude.docs_model=opus"]) == 0
    calls = _claude_calls(home)
    assert "auth status" in calls
    assert "-p Reply with exactly OK. --output-format text --model opus" in calls
    assert seen == []
    assert store.load_global(home).llm.providers["claude"].docs_model == "opus"  # alias kept on cli
    err = capsys.readouterr().err
    assert "→ checking claude login" in err and "✓ opus works" in err


def test_cli_not_logged_in_fails_without_writing(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _CLAUDE_CALLS[str(home)] = make_claude_stub(
        tmp_path / "bin", plugins=HEALTHY_PLUGINS, auth_logged_in=False
    )
    assert main(["configure", "--set", "llm.providers.claude.docs_model=opus"]) == 1
    assert "claude is not logged in — run claude auth login" in capsys.readouterr().err
    assert not (home / "config.yaml").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["configure", "--defaults"],
        ["configure", "--set", "llm.default=codex"],
        ["configure", "--set", "llm.providers.claude.model=fable"],
        ["configure", "--set", "notifications.enabled=true"],
    ],
)
def test_non_documentation_changes_run_no_probe(tmp_path, monkeypatch, argv):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(argv) == 0
    calls = _claude_calls(home)
    assert seen == [] and "auth status" not in calls and "-p" not in calls


def test_api_for_codex_refused_on_set_path(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _fake_http(monkeypatch)
    rc = main(["configure", "--set", "llm.default=codex", "--set", "llm.docs.backend=api"])
    assert rc == 1
    assert "codex: API documentation backend not supported yet, use cli" in capsys.readouterr().err
    assert not (home / "config.yaml").exists()


@pytest.mark.parametrize("value", ["", "op://Employee/item/password", "sk x"])
def test_bad_api_key_values_refused_without_writing(tmp_path, monkeypatch, capsys, value):
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    assert main(["configure", "--set", f"llm.providers.claude.api_key={value}"]) == 1
    err = capsys.readouterr().err
    assert "llm.providers.claude.api_key" in err
    assert not value or value not in err.replace("llm.providers.claude.api_key", "")
    assert not (home / "secrets.yaml").exists() and seen == []


def test_combined_cli_model_and_key_change_runs_both_probe_sets(tmp_path, monkeypatch):
    # Two independent triggers (spec §3.2): the key probe AND the backend's probes.
    home = _home(tmp_path, monkeypatch)
    seen = _fake_http(monkeypatch)
    rc = main(
        [
            "configure",
            "--set",
            "llm.providers.claude.docs_model=opus",
            "--set",
            f"llm.providers.claude.api_key={KEY}",
        ]
    )
    assert rc == 0
    assert len(seen) == 1 and seen[0].endswith("/v1/models?limit=1000")
    calls = _claude_calls(home)
    assert "auth status" in calls and "--model opus" in calls
    assert store.load_global(home).llm.providers["claude"].docs_model == "opus"
    assert store.load_secrets(home).api_keys == {"claude": KEY}


def test_stale_api_config_without_key_blocks_unrelated_sets(tmp_path, monkeypatch, capsys):
    # A hand-deleted secrets.yaml under backend=api: every --set is refused until
    # the key is restored or the backend set back to cli (documented behaviour).
    home = _home(tmp_path, monkeypatch)
    _fake_http(monkeypatch)
    g = GlobalConfig()  # add `from omc.config.schema import GlobalConfig` to the file imports
    g.llm.docs.backend = "api"
    g.llm.providers["claude"].docs_model = "claude-sonnet-5-5"
    store.save_global(home, g)
    assert main(["configure", "--set", "notifications.enabled=true"]) == 1
    assert "llm.providers.claude.api_key is required" in capsys.readouterr().err
    assert main(["configure", "--set", "llm.docs.backend=cli"]) == 0  # the escape hatch


def test_malformed_api_key_routing_key_is_unknown(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    assert main(["configure", "--set", "llm.providers.api_key=x"]) == 1
    assert "unknown config key" in capsys.readouterr().err
```

(add `import pytest` and `from ._stubs import HEALTHY_PLUGINS, make_claude_stub` if not already imported at the top of the file.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_configure.py -q`
Expected: the new tests FAIL (`unknown config key: providers.claude.api_key` exits 1 where 0 is expected; no probes recorded)

- [ ] **Step 3: Implement the non-interactive branch**

In `src/omc/configure.py`: add imports `import copy`, `from . import docsllm`, `from .config.schema import GlobalConfig, ProjectConfig, ProviderConfig, SecretsConfig`. Replace the body of the `if defaults or sets:` block up to (not including) `_migrate_legacy(...)` with:

```python
        gcfg = (
            GlobalConfig()
            if defaults
            else (store.load_global(ctx.home) or legacy_global or GlobalConfig())
        )
        pcfg = (store.load_project(root) if root else None) or legacy_project or ProjectConfig()
        scfg = store.load_secrets(ctx.home)
        before, before_keys = copy.deepcopy(gcfg), dict(scfg.api_keys)
        write_global = defaults
        write_project = bool(
            defaults and root is not None and not store.project_config_path(root).exists()
        )
        write_secrets = False
        for pair in sets:
            key, sep, value = pair.partition("=")
            if not sep:
                raise Refusal(f"--set expects KEY=VALUE, got {pair!r}")
            parts = key.split(".")
            if parts[0] == "worktree":
                if root is None:
                    raise Refusal("worktree.* is project config — run inside a git repository")
                store.set_key(pcfg, key, value)
                write_project = True
            elif len(parts) == 4 and parts[:2] == ["llm", "providers"] and parts[3] == "api_key":
                # Routing key, not a ProviderConfig field: secrets.yaml only.
                store.set_api_key(scfg, parts[2], value)
                write_secrets = True
            else:
                store.set_key(gcfg, key, value)
                write_global = True
        # Order (spec §3.2): consistency → refuse api-without-key → probes → write.
        store.validate_llm(gcfg.llm)
        if _probe_docs(ctx, gcfg, scfg, before=before, before_keys=before_keys):
            write_global = True  # the resolved full model id was written into docs_model
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
        if write_secrets:
            store.save_secrets(ctx.home, scfg)
            print(f"Updated {store.secrets_path(ctx.home)} (mode 0600)")
            for name, k in scfg.api_keys.items():
                if before_keys.get(name) != k:
                    _say(f"✓ {name} API key stored ({docsllm.mask_key(k)})")
```

and add these module-level helpers (after `_PLUGIN_HINTS`):

```python
def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _docs_provider(gcfg: GlobalConfig) -> str:
    return gcfg.llm.docs.provider or gcfg.llm.default


def _docs_changed(
    before: GlobalConfig, after: GlobalConfig, before_keys: dict[str, str], after_keys: dict[str, str]
) -> tuple[bool, bool]:
    """(documentation tuple changed, docs provider's key changed) — the probe
    trigger of spec §3.2. The docs provider is evaluated on the POST-apply
    config FOR BOTH SIDES (deliberate, per spec: "changed" compares the same
    provider name's value before and after); a provider entry that did not
    exist before compares as ""."""
    name = _docs_provider(after)

    def model(cfg: GlobalConfig) -> str:
        p = cfg.llm.providers.get(name)
        return p.docs_model if p else ""

    tuple_changed = (before.llm.docs.provider, before.llm.docs.backend, model(before)) != (
        after.llm.docs.provider,
        after.llm.docs.backend,
        model(after),
    )
    return tuple_changed, before_keys.get(name, "") != after_keys.get(name, "")


def _probe_docs(
    ctx: ToolContext,
    gcfg: GlobalConfig,
    scfg: SecretsConfig,
    *,
    before: GlobalConfig,
    before_keys: dict[str, str],
) -> bool:
    """Refuse api-without-key, then run exactly the probes the change calls
    for. Returns True when docs_model was rewritten (api: the resolved full
    id). Raises ProbeFailed/ConfigError — nothing has been written yet."""
    name, backend = _docs_provider(gcfg), gcfg.llm.docs.backend
    key = scfg.api_keys.get(name, "")
    if backend == "api" and not key:
        # Unconditional (spec §3.2): also fires on an unrelated --set when a stored
        # api config lost its key (hand-deleted secrets.yaml). Escape hatch:
        # `--set llm.docs.backend=cli`.
        raise ConfigError(f"llm.providers.{name}.api_key is required for the api backend")
    tuple_changed, key_changed = _docs_changed(before, gcfg, before_keys, scfg.api_keys)
    if key_changed and backend != "api":
        # The key changed while the backend does not use it: prove it anyway.
        docsllm.validate_key_only(ctx, name, key, say=_say)
    if not tuple_changed and not (key_changed and backend == "api"):
        return False
    # The two triggers are independent: a backend/provider/model change always
    # runs the selected backend's probes, even when a key probe just ran.
    # setdefault may add a provider entry (e.g. docs.provider=codex with no codex
    # section) — the same thing `--set llm.providers.codex.model=` does today.
    pcfg = gcfg.llm.providers.setdefault(name, ProviderConfig())
    model = docsllm.validate_selection(
        ctx, docsllm.DocsSelection(name, backend, pcfg.docs_model, key), say=_say
    )
    if model != pcfg.docs_model:
        pcfg.docs_model = model
        return True
    return False
```

Keep `_migrate_legacy`, `_ensure_repo_chain`, `_ensure_plugins` and the `print(_PLUGIN_HINTS)` / `return 0` tail exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_configure.py tests/unit/test_config_store.py -q`
Expected: all PASS, including every pre-existing `test_configure` test (they set no documentation key, so no probe runs)

- [ ] **Step 5: Run the quick gate and commit**

Run: `just check` — expected: all unit tests pass.

```bash
git add src/omc/configure.py tests/unit/test_configure.py
git commit -m "feat(configure): --set routes api_key to secrets.yaml and probes documentation changes before any write"
```

---

### Task 9: Interactive walkthrough — backend, masked key, fetched model list, probes

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/configure.py` (`run_configure` interactive branch, `_walkthrough_global`)
- Test: none new (PTY-driven, `pragma: no cover`, like today); `just check` must stay green.

**Interfaces:**
- Consumes: Task 7 probe functions, Task 3 store functions.
- Produces: `_walkthrough_global(ctx: ToolContext, cfg: GlobalConfig, scfg: SecretsConfig) -> None` (signature change; the only caller is `run_configure`).

- [ ] **Step 1: Change the interactive branch of `run_configure`**

Replace the lines from `gcfg = store.load_global(ctx.home) or legacy_global or GlobalConfig()` (interactive branch) through `print(f"Saved {store.global_config_path(ctx.home)}")` with:

```python
    gcfg = store.load_global(ctx.home) or legacy_global or GlobalConfig()
    scfg = store.load_secrets(ctx.home)
    before_keys = dict(scfg.api_keys)
    _walkthrough_global(ctx, gcfg, scfg)  # probes inside; raises before any write
    pcfg = None
    if root is not None:
        pcfg = store.load_project(root) or legacy_project or ProjectConfig()
        _walkthrough_project(pcfg)
    # Secrets FIRST: if this write fails, config.yaml must not already say `api`
    # (that state trips the unconditional api-without-key refusal on every run).
    if scfg.api_keys != before_keys:
        store.save_secrets(ctx.home, scfg)
        print(f"Saved {store.secrets_path(ctx.home)} (mode 0600)")
    store.save_global(ctx.home, gcfg)
    print(f"Saved {store.global_config_path(ctx.home)}")
```

(`aborted()` below raises `Refusal`, rc 2 — the user declined; probe failures stay `ProbeFailed`, rc 1 — an external verdict on well-formed input.)

- [ ] **Step 2: Extend `_walkthrough_global`**

Change its signature to `def _walkthrough_global(ctx: ToolContext, cfg: GlobalConfig, scfg: SecretsConfig) -> None:  # pragma: no cover - PTY-driven, E2E territory` and insert, after the `cfg.llm.default = …` block and before the notifications `enable = questionary.confirm(...)`:

```python
    # Documentation generation (spec 2026-10-01 §3.3). Nothing below is saved
    # unless BOTH probes pass; Ctrl-C/Esc (None) aborts with nothing written.
    def aborted() -> Refusal:
        return Refusal("configure aborted — nothing was saved")

    docs_provider = cfg.llm.docs.provider if cfg.llm.docs.provider in selected else ""
    if len(selected) > 1:
        picked = questionary.select(
            "Documentation provider",
            choices=selected,
            default=docs_provider or cfg.llm.default,
        ).ask()
        if picked is None:
            raise aborted()
        docs_provider = "" if picked == cfg.llm.default else picked
    name = docs_provider or cfg.llm.default
    provider = get_provider(name)
    backend_choices = [Choice(f"CLI (uses your {name} login)", "cli")]
    if provider.api_base_url():
        backend_choices.append(Choice("API key", "api"))
    current = next((c for c in backend_choices if c.value == cfg.llm.docs.backend), backend_choices[0])
    backend = questionary.select("Documentation backend", choices=backend_choices, default=current).ask()
    if backend is None:
        raise aborted()
    pcfg = cfg.llm.providers[name]
    other = "Other (type a model id)…"
    if backend == "api":
        key = scfg.api_keys.get(name, "")
        while True:
            hint = f" (blank keeps {docsllm.mask_key(key)})" if key else ""
            typed = questionary.password(f"{name} API key{hint}").ask()
            if typed is None or (not typed and not key):
                raise aborted()
            candidate = typed or key
            try:
                store.validate_api_key(candidate, f"llm.providers.{name}.api_key")
            except ConfigError as exc:
                print(exc)
                continue
            _say(f"→ checking the key against {provider.api_base_url().split('/')[2]}")
            ok, detail, models = docsllm.api_connection_probe(ctx, name, candidate)
            _say(("✓ " if ok else "✗ ") + detail)
            if ok:
                key = candidate
                break
            key = ""  # a failed probe never "keeps" the old key: retype or abort
        choices = docsllm.model_choices(models)
        default = (
            pcfg.docs_model
            if pcfg.docs_model in choices
            else docsllm.resolve_model(name, pcfg.docs_model or provider.docs_model_default(), models)
        )
        picked = questionary.select(
            "Documentation model", choices=[*choices, other], default=default
        ).ask()
        if picked is None:
            raise aborted()
        model = questionary.text("model id").ask() if picked == other else picked
        if not model:
            raise aborted()
        _say(f"→ validating {model} via api")
        ok, detail = docsllm.api_model_probe(ctx, name, key, model)
        _say(("✓ " if ok else "✗ ") + detail)
        if not ok:
            raise docsllm.ProbeFailed(detail)
        scfg.api_keys[name] = key
    else:
        known = provider.models()
        if known:
            default = pcfg.docs_model if pcfg.docs_model in known else provider.docs_model_default()
            picked = questionary.select(
                "Documentation model",
                choices=[*known, other],
                default=default or provider.docs_model_default() or known[0],
            ).ask()
            if picked is None:
                raise aborted()
            model = questionary.text("model id").ask() if picked == other else picked
        else:
            model = questionary.text(
                "Documentation model (blank = CLI default)", default=pcfg.docs_model
            ).ask()
        if model is None:
            raise aborted()
        _say(f"→ checking {name} login")
        ok, detail = docsllm.cli_connection_probe(ctx, name)
        _say(("✓ " if ok else "✗ ") + detail)
        if not ok:
            raise docsllm.ProbeFailed(detail)
        _say(f"→ validating {model or 'default model'} via cli")
        ok, detail = docsllm.cli_model_probe(ctx, name, model)
        _say(("✓ " if ok else "✗ ") + detail)
        if not ok:
            raise docsllm.ProbeFailed(detail)
    pcfg.docs_model = model
    cfg.llm.docs.provider = docs_provider
    cfg.llm.docs.backend = backend
```

(`Choice` is already imported inside the function; `get_provider`, `store`, `ConfigError`, `Refusal`, `docsllm`, `_say` are module-level imports/helpers from earlier tasks.)

- [ ] **Step 3: Lint, gate, and commit**

Run: `just build` (ruff format/check + package build) — fix any formatting ruff reports with `uvx ruff format .`. Then `just check`.
Expected: both green.

```bash
git add src/omc/configure.py
git commit -m "feat(configure): interactive documentation backend/key/model dialog with live probes"
```

---

### Task 10: Behaviour layer and docs — README, gitnexus-document skill, project AGENTS.md

**Model:** standard coding tier

**Files:**
- Modify: `README.md` (the "Configure it" paragraph, line ~21)
- Modify: `skills/gitnexus-document/SKILL.md` (Step 2 paragraph)
- Modify: `.omc/config/AGENTS.md` (Architectural invariants, the ToolContext bullet)

- [ ] **Step 1: README**

Replace the sentence beginning `Documentation/wiki generation uses its own model —` (and ending `never your session model.`) with:

```markdown
Documentation/wiki generation has its own provider, backend and model, chosen in the dialog or via `--set llm.docs.provider=claude`, `--set llm.docs.backend=cli|api` and `--set llm.providers.claude.docs_model=opus` (on the API backend an alias like `opus` is resolved to the newest matching model id and that full id is what gets stored); the default is the provider's standard coding tier (claude: the `sonnet` alias on the CLI backend, the newest `claude-sonnet-*` id on the API backend), never your session model. `api` drives GitNexus through Anthropic's OpenAI-compatible endpoint with a key you paste once (`--set llm.providers.claude.api_key=…` for scripts — note that a key on the command line lands in your shell history and is visible in `ps` while it runs; the interactive prompt hides it). The key is stored in `~/.omc/secrets.yaml` (mode 0600, never in `config.yaml`) and shown afterwards only as `******` plus its last four characters. Configure **validates with real calls** before saving anything: the key against the models endpoint, the login via `claude auth status`, and the chosen model with one short headless call or a per-model GET — a failing probe saves nothing. Anthropic documents the compatibility layer as intended for testing and comparison rather than as its production priority, so a failing API run has no silent fallback: the knowledge verdict stays stale and `omc watch` retries as usual. Two consequences to know: after an API run a bare `gitnexus wiki` typed by hand opens GitNexus's own setup wizard (omc hands the key only to the processes it launches), and `fable` can pass the probe yet fail generation for zero-data-retention organisations.
```

- [ ] **Step 2: Skill prose**

In `skills/gitnexus-document/SKILL.md`, replace `Python owns the LLM choice (omc's configured default and that provider's docs model, never the session model);` with `Python owns the LLM choice — the configured documentation provider, backend (CLI or API key) and model (`omc configure`), never the session model;`.

- [ ] **Step 3: Project invariant**

In `.omc/config/AGENTS.md`, replace

```markdown
- **`ToolContext` (src/omc/toolctx.py) is the only subprocess/env boundary.**
  Nothing else imports subprocess or reads `~/.omc`. Argv lists only — never
```

with

```markdown
- **`ToolContext` (src/omc/toolctx.py) is the only subprocess/env/network
  boundary.** Nothing else imports `subprocess` or `urllib`, or reads `~/.omc`
  (`http_get` is omc's single network call). Argv lists only — never
```

- [ ] **Step 4: README fast-tier wording, mechanical invariant check, commit**

In `README.md`, the "Development" section line `Fast tier — unit tests; no LLM, no network, no Docker:` becomes `Fast tier — unit tests; no LLM, no external network (one loopback socket in tests/unit/test_toolctx_http.py), no Docker:`.

Run: `grep -rln "import subprocess\|import urllib" src/omc | grep -v toolctx.py` — expected output: exactly `src/omc/gitnexus.py`, whose `import subprocess` sits under `if TYPE_CHECKING:` (annotation-only; confirm by reading the lines). Any other file listed is a violation.

```bash
git add README.md skills/gitnexus-document/SKILL.md .omc/config/AGENTS.md
git commit -m "docs: documentation backend/key/model options, secrets file, widened ToolContext invariant"
```

---

### Task 11: E2E — `require_env` helper and the one real API-backend documentation test (written, not run here)

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/harness.py` (new `require_env`)
- Create: `tests/unit/test_e2e_require_env.py`
- Modify: `tests/e2e/test_e2e_gitnexus.py` (new test)

**Interfaces:**
- Produces: `harness.require_env(var: str, guidance: str) -> None` (`pytest.fail` naming the variable when unset/empty — never skip); E2E `test_document_api_backend_generates_wiki_docs`.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_e2e_require_env.py`:

```python
"""require_env: a stricter gate than require_token — the API-backend E2E needs
ANTHROPIC_API_KEY specifically; an OAuth token alone must FAIL, never skip."""

import pytest

from tests.e2e.harness import require_env


def test_require_env_passes_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    require_env("ANTHROPIC_API_KEY", "put an ANTHROPIC_API_KEY in .env")


@pytest.mark.parametrize("value", [None, ""])
def test_require_env_fails_naming_the_variable(monkeypatch, value):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-only")  # not enough for the API backend
    if value is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    with pytest.raises(pytest.fail.Exception, match=r"\$ANTHROPIC_API_KEY.*put an ANTHROPIC_API_KEY"):
        require_env("ANTHROPIC_API_KEY", "put an ANTHROPIC_API_KEY in .env")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_e2e_require_env.py -q`
Expected: FAIL with `ImportError: cannot import name 'require_env'`

- [ ] **Step 3: Implement `require_env`**

In `tests/e2e/harness.py`, after `require_token`:

```python
def require_env(var: str, guidance: str) -> None:
    """A specific variable, not "any auth for the provider": the API-backend
    E2E talks to Anthropic directly, so CLAUDE_CODE_OAUTH_TOKEN cannot stand in."""
    if not os.environ.get(var):
        pytest.fail(f"live E2E needs ${var} — {guidance}; then re-run.")
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `uv run pytest tests/unit/test_e2e_require_env.py -q`
Expected: PASS

- [ ] **Step 5: Write the E2E test**

Append to `tests/e2e/test_e2e_gitnexus.py` (add `require_env` to the harness import):

```python
def test_document_api_backend_generates_wiki_docs(container):
    """The api documentation backend end to end against the REAL Anthropic
    endpoint through GitNexus's custom provider (spec 2026-10-01 §7). Needs
    ANTHROPIC_API_KEY specifically: configure's probe talks to Anthropic directly."""
    require_env("ANTHROPIC_API_KEY", "put an ANTHROPIC_API_KEY in .env")
    # Deliberately NO require_token("claude"): an OAuth token alone must make this
    # test fail, not pass — the api backend talks to Anthropic directly. (Side
    # note: with ANTHROPIC_API_KEY forwarded, any `claude -p` elsewhere in the
    # session bills that key; pre-existing harness behaviour, recorded in the
    # build ledger.)
    configure_omc(container, "claude")  # `--set llm.default=claude`: no documentation key → no probe
    # One bash -c script: run_in quotes argv via shlex.join, so "$ANTHROPIC_API_KEY"
    # as a separate argv item would never expand; expanding INSIDE the container
    # keeps the key out of pytest's argv and output. Real validation runs here.
    rc, out = run_in(
        container,
        [
            "bash",
            "-c",
            'omc configure --set llm.docs.backend=api '
            '--set llm.providers.claude.api_key="$ANTHROPIC_API_KEY"',
        ],
        timeout=120,
    )
    assert rc == 0, out
    assert "✓ key accepted" in out and re.search(r"✓ claude-sonnet-\S+ works", out), out
    assert os.environ["ANTHROPIC_API_KEY"] not in out

    repo = make_work_repo(container)
    seed = (
        f"cd {repo} && mkdir -p app && "
        "printf 'def add(a, b):\\n    return a + b\\n\\n\\n"
        "def sub(a, b):\\n    return a - b\\n' > app/calc.py && "
        "printf 'from app.calc import add\\n\\n\\n"
        "def total(xs):\\n    t = 0\\n    for x in xs:\\n        t = add(t, x)\\n    return t\\n'"
        " > app/report.py && git add -A && git commit -qm 'add app' && git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", seed])
    assert rc == 0, out

    # The same _run_wiki path omc watch uses, without a second LLM in the loop.
    rc, out = run_in(
        container,
        ["omc", "internal", "gitnexus", "refresh", "--enable-documentation"],
        cwd=repo,
        timeout=600,
    )
    assert rc == 0, out
    assert "→ regenerating documentation via claude api (claude-sonnet-" in out, out
    assert os.environ["ANTHROPIC_API_KEY"] not in out

    rc, listing = run_in(
        container,
        ["bash", "-c", f"ls {repo}/.omc/docs/gitnexus/docs/*.md 2>/dev/null | head -5"],
    )
    assert rc == 0 and listing.strip(), f"no markdown docs landed:\n{out[:2000]}"

    # GitNexus persisted provider/baseUrl/model — and NO key (env-supplied keys are
    # never saved). Fresh container: no prior config.json can carry an apiKey.
    # baseUrl is stored VERBATIM with its trailing slash (wiki.ts at 8de99dc).
    rc, gn = run_in(container, ["cat", "/root/.gitnexus/config.json"])
    assert rc == 0, gn
    saved = json.loads(gn)
    assert saved["provider"] == "custom"
    assert saved["baseUrl"] == "https://api.anthropic.com/v1/"
    assert saved["model"].startswith("claude-sonnet-")
    assert "apiKey" not in saved

    # omc's own files: key only in secrets.yaml, mode 600, never in config.yaml.
    rc, mode = run_in(container, ["stat", "-c", "%a", "/root/.omc/secrets.yaml"])
    assert rc == 0 and mode.strip() == "600", mode
    rc, cfg_text = run_in(container, ["cat", "/root/.omc/config.yaml"])
    assert rc == 0 and os.environ["ANTHROPIC_API_KEY"] not in cfg_text
    assert "backend: api" in cfg_text
```

(add `import json`, `import os` and `import re` to the module imports.)

- [ ] **Step 6: Collect-only check and commit**

Run: `uv run pytest tests/e2e/test_e2e_gitnexus.py --collect-only -q` — expected: 4 tests collected (the three existing ones plus `test_document_api_backend_generates_wiki_docs`), all under marker `e2e` (module `pytestmark`). Do **not** run it here (token-gated, Docker, real spend); `just e2e-tests -k api_backend` is the command for `/omc:verify` / finish, and the manual-dispatch workflow needs `secrets.ANTHROPIC_API_KEY` populated or it turns red by design.

```bash
git add tests/e2e/harness.py tests/unit/test_e2e_require_env.py tests/e2e/test_e2e_gitnexus.py
git commit -m "test(e2e): require_env gate and the api-backend documentation E2E"
```

---

## Self-review notes (run by the plan author)

- **Spec coverage:** §2.1 → Tasks 2, 4; §2.2 → Task 3; §2.3 → Tasks 4, 5; §3.1 → Task 8; §3.2 → Tasks 6, 7, 8; §3.3 → Task 9; §3.4 → Tasks 5, 7, 8; §4 → Task 5; §5 → Task 1; §6 → Task 10; §7 → every task's tests + Task 11; §8 → nothing to build.
- **Type consistency:** `DocsRun.wiki_args` is a `tuple[str, ...]` and both call sites splat it into `gitnexus_argv(ctx, *run.wiki_args)`; `validate_selection` returns `str`; `api_connection_probe` returns a 3-tuple everywhere; `make_claude_stub(auth_logged_in, headless_reply, headless_rc)` names match Tasks 7 and 8; `store.validate_llm` (public) replaces `_validate_llm_providers` in Task 2 and is used by Task 8.
- **Deviation from spec wording, deliberate:** the backend/provider *consistency* check runs on load and save but not at `set_key` time (Task 2), because `--set` pairs apply in arbitrary order; per-leaf type/closed-set/provider-name checks still run at set time. Task 7 adds a third pure adapter method, `auth_status_argv`, so `docsllm` carries no provider-name branches.
