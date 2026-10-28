# Enable Embeddings for GitNexus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, default-off global option `gitnexus.embeddings` that makes omc install GitNexus's local embedding runtime under `~/.omc`, embed on every refresh, backfill an existing index once, and fall back to keyword-only indexing whenever embedding fails — without moving any freshness verdict or the `OMC_KNOWLEDGE` contract.

**Architecture:** One new config dataclass (`GitNexusConfig`) on `GlobalConfig`/`Config`, read in exactly one place (`gitnexus.ensure_embeddings`). `toolctx.GITNEXUS_ENV` becomes `gitnexus_env(home)` so every omc child sees the same runtime and model-cache paths. Inside `gitnexus.py`, one narrated supervised runner (`_run_narrated`, over the existing `ctx.run_supervised`) serves the runtime install, the embedding analyze and the `embeddings sync` backfill; `_run_analyze` grows an `embed` flag and a one-shot keyword-only retry so both analyze sites (incremental and destroy-and-rebuild) behave alike. The E2E image pins Node and pre-bakes the runtime, model and VECTOR extension.

**Tech Stack:** Python 3 (`src/omc/`), dataclasses + PyYAML config, mininterface menu, pytest with restricted-PATH shell stubs, the managed GitNexus fork CLI (`node ~/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js`), Docker E2E image (`docker/Dockerfile.e2e`).

**Spec:** `docs/superpowers/specs/2026-10-27-enable-embeddings-for-gitnexus-design.md` (the design truth; argue from it).

## Global Constraints

- Option name, exact: `gitnexus.embeddings`; `GitNexusConfig.embeddings: bool = False`, label `Embeddings`, help `Generate local vector embeddings for semantic /omc:explain queries.`; `GlobalConfig.gitnexus` label `GitNexus`, help `Local embedding runtime for the knowledge graph.` (record §3). `--set` accepts exactly `true`/`false`; anything else is refused with `gitnexus.embeddings expects true or false`. `ProjectConfig` never gets it.
- Paths, exact (record Decision 4): `GITNEXUS_EMBEDDING_RUNTIME_DIR=<home>/dependencies/gitnexus-embeddings/runtime`, `HF_HOME=<home>/dependencies/gitnexus-embeddings/hf`, always alongside `GITNEXUS_SHARED_STORE=off`, unconditionally in `child_env()` and in the interactive session env. `GITNEXUS_LBUG_EXTENSION_INSTALL=auto` only as `extra_env` on the two embedding write paths (embedding analyze, `embeddings sync`) — never in `child_env()`.
- Readiness probe (record §5): both `<runtime>/node_modules/@huggingface/transformers/package.json` and `<runtime>/node_modules/onnxruntime-node/package.json` exist. Install argv, exact: `node <cli> embeddings install` (no `--force`, no `--cuda`). Backfill argv, exact: `node <cli> embeddings sync <primary-root>`. Embedding analyze argv, exact: today's analyze argv + `--embeddings` as the LAST element.
- `ensure_embeddings` never raises and never returns an exit code; a failed install is a narrated `✗` and `False`. Nothing in this feature may fail `omc update`, `omc watch` or `omc internal gitnexus refresh`; nothing changes `snapshot_freshness`, `Freshness`, `OMC_KNOWLEDGE`, or the destroy-and-rebuild escalation. With the option off, every existing argv assertion stays byte-identical.
- Narration goes through the `say` callback / stderr with `→` `✓` `·` `✗` prefixes; exact strings are given per task.
- `ToolContext` stays the only subprocess/env boundary: argv lists, no `shell=True`, no new `subprocess` imports (gitnexus.py's stays under `TYPE_CHECKING`).
- Red → green for every task: write the test, run it, see it fail for the expected reason, implement, see green. Never skip; a missing prerequisite is `pytest.fail` naming the fixing command. Stub scripts run on a restricted PATH: shell builtins, absolute paths (`/bin/mkdir`, `/bin/rm`, `/bin/cat`, `/usr/bin/git`), quoted heredocs. Exact-argv assertions.
- Worktree trap: run focused tests as `uv run python -m pytest <path> -q` (a bare `uv run pytest`/`just check` in this worktree may import main's installed omc). The conductor runs `/omc:check` after each task; each task still runs its own focused tests. Restore `uv.lock` with `git checkout -- uv.lock` before every commit if `uv run` rewrote it.
- Lint: `uvx ruff format <changed files>` then `uvx ruff check <changed files>` before every commit; the 100-column limit (E501) applies to tests too, so wrap the long stub lines in the task code below where ruff asks.
- `README.md` is generated: Task 11 edits `.claude/skills/regenerate-readme/SKILL.md` and runs the `regenerate-readme` skill; nobody hand-edits README.md.
- E2E runs only at the milestone verify stage (`/omc:verify`), never per task; Task 12's code must still be importable and pass `uvx ruff format --check . && uvx ruff check .`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Pressure-test notes (from `/omc:explain` over a graph 96 commits behind; every claim re-checked against current files)

- **Config:** `configure._compose_menu` composes root entries only for the `llm` field (plus the project `worktree` entry), and `commit` copies candidates back with `gcfg.llm = candidate_g.llm` / `pcfg.worktree = …` / `scfg.api_keys = …`. A new `gitnexus` section needs BOTH an explicit root branch and `gcfg.gitnexus = candidate_g.gitnexus` in `commit`, or a menu toggle would save to disk while `current()` kept serving the stale in-memory value (tag replay bug). `store._hydrate`'s generic leaf stores the raw YAML value, so the bool check must be added for `cls is GitNexusConfig`, mirroring `ProviderConfig.notifications`. `set_key`'s generic path recurses into `GitNexusConfig` with head `embeddings`, so the branch goes before the generic tail. `test_schema_menu_metadata_does_not_change_persisted_defaults` pins `asdict(GlobalConfig())` and must grow `"gitnexus": {"embeddings": False}`.
- **Env:** `child_env()` feeds `run`, `run_bounded`, `run_supervised`, `stream`; `plugin.py:270` derives the Claude probe context from `child_env()`; `session.py` spreads `GITNEXUS_ENV` into the interactive env (the one bypass). Nothing in `src/` or `tests/` reads `HF_HOME`. The record's `gitnexus_env(home)` reaches every child; no second mechanism is needed. Two exact-dict tests grow (`test_session_plan_wires_notifications_for_the_provider_that_takes_argv`, `test_child_env_forces_gitnexus_local_storage`).
- **Runtime install / update:** `installer.run_update` already holds `cfg = store.load_global(ctx.home)` (possibly `None`) before `gitnexus.update_gitnexus(ctx)`; the call is a module attribute that six `test_installer.py` monkeypatches replace with `lambda ctx: …`. Adding a keyword `cfg=` to `update_gitnexus` means those lambdas must accept `**kw`. The supervised runner precedent is `_run_wiki` (`ctx.run_supervised` + `on_line` narration off a TTY); `_run_narrated` reuses it with a constant heartbeat so bytes are the only progress signal. No progress bar.
- **Refresh / backfill:** `_run_analyze` has no `say` today; the retry narration needs one, so its signature becomes `_run_analyze(ctx, root, base, *, embed=False, say=_say)`. `did_anything` is the only gate on `✓ knowledge is current`, so the backfill sets it. The backfill check runs after the index block closes (before `if not documentation`), so an already-fresh index with the option freshly on also gets it. `read_index_meta` prefers `gitnexus.json` over `meta.json`; the count helper reads `stats.embeddings` from whichever it returns.
- **E2E / docs:** token-free E2E tests exist (`test_e2e_watch.py` calls `configure_omc(container, "claude")` without `require_token`; `setup-plugins.sh` only installs plugins). The fork's `clean --force` unregisters the repo, so the image pre-bake fixture can be cleaned and removed. The README recipe lists schema labels in its Configure slot and the `~/.omc/dependencies/gitnexus` layout in its architecture slot; both slots are edited (recipe is a source), then regenerated. pytest-timeout is 300 s per test, function-only: the new E2E refresh over `/repo` gets `timeout=240` on its `run_in`.

---

### Task 1: Verify the real GitNexus CLI contract and record it at the call site

Complexity: medium

**Files:**
- Modify: `src/omc/gitnexus.py` (comment block only, inserted after `_run_analyze`, before `def gitnexus_root`)

**Interfaces:**
- Consumes: the managed CLI `node ~/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js` (fork at `20c559d`), network, a throwaway temp git repo.
- Produces: a verified fact sheet (as a code comment) that Tasks 6–9 and 12 rely on: which metadata file carries `stats.embeddings`, that `embeddings sync [path]` writes the flat slot and leaves `lastCommit` alone, that an analyze without `--embeddings` preserves vectors, the shape of off-TTY analyze lines, and the two-manifest readiness layout under `GITNEXUS_EMBEDDING_RUNTIME_DIR`.

This task touches NO real state: `GITNEXUS_EMBEDDING_RUNTIME_DIR`, `HF_HOME` and the repo all live under one `mktemp -d`; `GITNEXUS_SHARED_STORE=off` keeps the index inside the temp repo; the temp repo is `clean --force`d (unregisters it from `~/.gitnexus/registry.json`) and deleted. Never run anything against the primary checkout's `.gitnexus/`.

- [ ] **Step 1: Build the throwaway lab.**

```bash
LAB=$(mktemp -d /tmp/omc-embed-lab.XXXXXX); echo "$LAB"
export GITNEXUS_SHARED_STORE=off
export GITNEXUS_EMBEDDING_RUNTIME_DIR="$LAB/runtime" HF_HOME="$LAB/hf"
CLI=$HOME/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js
mkdir -p "$LAB/repo" && cd "$LAB/repo" && git init -q -b main
printf 'def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n' > calc.py
printf 'from calc import add\n\n\ndef total(xs):\n    t = 0\n    for x in xs:\n        t = add(t, x)\n    return t\n' > report.py
printf '# lab\n' > README.md
git -c user.email=lab@omc.invalid -c user.name=lab add -A
git -c user.email=lab@omc.invalid -c user.name=lab commit -qm fixture
```

- [ ] **Step 2: Verify the runtime install layout (record §5).**

```bash
node "$CLI" embeddings install 2>&1 | tee "$LAB/install.log" | tail -5
ls "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/@huggingface/transformers/package.json" \
   "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/onnxruntime-node/package.json"
test ! -e "$HOME/.gitnexus/embedding-runtime" && echo "nothing landed in ~/.gitnexus"
```

Expected: exit 0, both manifests exist, `~/.gitnexus/embedding-runtime` absent. Note whether the install log contains pino JSON lines (`{"level":…}`) or plain npm lines.

- [ ] **Step 3: Verify keyword-only analyze, then `embeddings sync` on the flat slot (record §6).**

```bash
node "$CLI" analyze --skip-agents-md --skip-skills --branch main > "$LAB/analyze-plain.log" 2>&1; echo rc=$?
ls .gitnexus/  # which metadata file(s) exist: gitnexus.json and/or meta.json
python3 - <<'PY'
import json, pathlib
root = pathlib.Path(".gitnexus")
for name in ("gitnexus.json", "meta.json"):
    p = root / name
    if p.exists():
        m = json.loads(p.read_text())
        print(name, "lastCommit=", m.get("lastCommit"), "stats.embeddings=", (m.get("stats") or {}).get("embeddings"), "branch=", m.get("branch"))
PY
GITNEXUS_LBUG_EXTENSION_INSTALL=auto node "$CLI" embeddings sync "$PWD" > "$LAB/sync.log" 2>&1; echo rc=$?
test ! -e .gitnexus/branches && echo "no branch sub-slot: sync wrote the flat slot"
python3 - <<'PY'
import json, pathlib
root = pathlib.Path(".gitnexus")
for name in ("gitnexus.json", "meta.json"):
    p = root / name
    if p.exists():
        m = json.loads(p.read_text())
        print(name, "lastCommit=", m.get("lastCommit"), "stats.embeddings=", (m.get("stats") or {}).get("embeddings"))
PY
```

Expected: plain analyze exit 0 with `stats.embeddings` 0 or absent; sync exit 0, `.gitnexus/branches` absent, `stats.embeddings > 0` in the same metadata file, `lastCommit` unchanged. If `stats.embeddings` stays 0/absent after a successful sync, or `.gitnexus/branches/` appears, or `lastCommit` moved: STOP and report it as a CRITICAL contradiction of record §6 (do not continue the plan).

- [ ] **Step 4: Verify the incremental embedding analyze and preservation (record Decision 6).**

```bash
printf 'def mul(a, b):\n    return a * b\n' > extra.py
git -c user.email=lab@omc.invalid -c user.name=lab add -A
git -c user.email=lab@omc.invalid -c user.name=lab commit -qm more
GITNEXUS_LBUG_EXTENSION_INSTALL=auto node "$CLI" analyze --skip-agents-md --skip-skills --branch main --embeddings > "$LAB/analyze-embed.log" 2>&1; echo rc=$?
grep -c '^{' "$LAB/analyze-embed.log"; grep -v '^{' "$LAB/analyze-embed.log" | head -30
node "$CLI" analyze --skip-agents-md --skip-skills --branch main > /dev/null 2>&1; echo rc=$?   # "Already up to date" path
python3 -c 'import json,pathlib; r=pathlib.Path(".gitnexus"); p=(r/"gitnexus.json") if (r/"gitnexus.json").exists() else r/"meta.json"; m=json.loads(p.read_text()); print(p.name, m["stats"]["embeddings"])'
```

Expected: embedding analyze exit 0, count grew; the plain analyze afterwards exits 0 and the count is unchanged (vectors preserved). Capture the first ~30 non-JSON lines: these are what `_run_narrated` will narrate (e.g. a `Generating embeddings` phase line) — record their shape.

- [ ] **Step 5: Verify the failure shape with no runtime (record §1, §10 Node floor).**

```bash
GITNEXUS_EMBEDDING_RUNTIME_DIR="$LAB/absent" GITNEXUS_LBUG_EXTENSION_INSTALL=never \
  node "$CLI" analyze --skip-agents-md --skip-skills --branch main --embeddings --force > "$LAB/analyze-noruntime.log" 2>&1; echo rc=$?
tail -5 "$LAB/analyze-noruntime.log"; ls "$LAB/absent" 2>/dev/null && echo "analyze auto-installed into the absent dir"
```

Expected: either a non-zero exit (the fallback omc's retry handles) or an auto-install into `$LAB/absent` (confirms record §1's reason for owning the install). Record which.

- [ ] **Step 6: Tear down without touching real state.**

```bash
node "$CLI" clean --force; cd /; rm -rf "$LAB"
python3 -c 'import json,os; r=json.load(open(os.path.expanduser("~/.gitnexus/registry.json"))); print([k for k in json.dumps(r).split("\"") if "omc-embed-lab" in k])'  # expect []
```

- [ ] **Step 7: Record the facts as a comment in `src/omc/gitnexus.py`** immediately after `_run_analyze` (before `def gitnexus_root`). Fill every `<…>` from Steps 2–5; keep it under 25 lines:

```python
# ─── Embeddings (spec 2026-10-27-enable-embeddings-for-gitnexus) ───────────
# Verified against the managed fork CLI (gitnexus 1.6.12 @ 20c559d, <date>):
# - `embeddings install` honours GITNEXUS_EMBEDDING_RUNTIME_DIR and lands
#   node_modules/@huggingface/transformers + node_modules/onnxruntime-node there;
#   nothing is written to ~/.gitnexus/embedding-runtime.
# - `embeddings sync [path]` takes no --branch and writes the FLAT slot
#   (<path>/.gitnexus; no branches/ sub-slot appeared). It writes
#   stats.embeddings into <gitnexus.json|meta.json> and leaves lastCommit alone,
#   so it can never move the freshness verdict.
# - `analyze … --embeddings` embeds new nodes incrementally; an analyze without
#   the flag afterwards preserves the vectors (count unchanged).
# - Off a TTY, analyze prints <phase lines, e.g. "Generating embeddings">; pino
#   JSON lines start with "{" and are not narrated.
# - With no runtime: <non-zero exit / auto-install into the runtime dir>.
```

- [ ] **Step 8: Confirm nothing else changed and the suite is green.** Run: `git status --short` (only `src/omc/gitnexus.py`), then `uv run python -m pytest tests/unit/test_gitnexus_refresh.py tests/unit/test_gitnexus_update.py -q`. Expected: PASS.

- [ ] **Step 9: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null; git add src/omc/gitnexus.py
git commit -m "docs(gitnexus): record the verified embeddings sync/analyze contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `GitNexusConfig` schema, hydration, `set_key`, `load_effective`

Complexity: medium

**Files:**
- Modify: `src/omc/config/schema.py` (new dataclass after `SecretsConfig`; fields on `Config` and `GlobalConfig`)
- Modify: `src/omc/config/store.py` (`set_key` branch, `_hydrate` bool check, import)
- Modify: `src/omc/config/resolve.py:29-33`
- Test: `tests/unit/test_config_store.py`

**Interfaces:**
- Produces: `omc.config.schema.GitNexusConfig(embeddings: bool = False)`; `GlobalConfig.gitnexus: GitNexusConfig`; `Config.gitnexus: GitNexusConfig`; `store.set_key(cfg, "gitnexus.embeddings", "true"|"false")`; `ConfigError("gitnexus.embeddings expects true or false")`; hydration error `invalid gitnexus.embeddings in <path>: expected true/false`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_config_store.py`; also add `GitNexusConfig` to the schema import at the top):

```python
# --- gitnexus.embeddings (spec 2026-10-27 §3) ---


def test_gitnexus_embeddings_defaults_off_everywhere(tmp_path):
    from omc.config.schema import Config

    assert GlobalConfig().gitnexus.embeddings is False
    assert Config().gitnexus.embeddings is False
    # a config.yaml written before the option existed hydrates to off
    (tmp_path / "config.yaml").write_text("schema_version: 1\nllm:\n  default: claude\n")
    assert store.load_global(tmp_path).gitnexus.embeddings is False


def test_gitnexus_embeddings_round_trips_and_is_typed(tmp_path):
    cfg = GlobalConfig()
    cfg.gitnexus.embeddings = True
    store.save_global(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "gitnexus:\n  embeddings: true" in text
    assert store.load_global(tmp_path).gitnexus.embeddings is True


@pytest.mark.parametrize("value", ['"yes"', "1", "null", "[true]"])
def test_gitnexus_embeddings_wrong_type_is_a_config_error(tmp_path, value):
    (tmp_path / "config.yaml").write_text(f"gitnexus:\n  embeddings: {value}\n")
    with pytest.raises(ConfigError, match="gitnexus.embeddings"):
        store.load_global(tmp_path)


def test_set_gitnexus_embeddings_accepts_exactly_true_false():
    cfg = GlobalConfig()
    store.set_key(cfg, "gitnexus.embeddings", "true")
    assert cfg.gitnexus.embeddings is True
    store.set_key(cfg, "gitnexus.embeddings", "false")
    assert cfg.gitnexus.embeddings is False
    for value in ("1", "on", "True", "yes"):
        with pytest.raises(ConfigError, match="gitnexus.embeddings expects true or false"):
            store.set_key(cfg, "gitnexus.embeddings", value)
    assert cfg.gitnexus.embeddings is False
    with pytest.raises(ConfigError, match="gitnexus is a section, not a settable key"):
        store.set_key(cfg, "gitnexus", "true")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "gitnexus.nope", "true")
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(cfg, "gitnexus.embeddings.extra", "true")


def test_project_config_has_no_gitnexus_section():
    with pytest.raises(ConfigError, match="unknown config key"):
        store.set_key(ProjectConfig(), "gitnexus.embeddings", "true")


def test_load_effective_carries_gitnexus_from_the_global_file(tmp_path):
    from omc.config import resolve
    from omc.toolctx import ToolContext

    (tmp_path / "config.yaml").write_text("llm:\n  default: claude\ngitnexus:\n  embeddings: true\n")
    ctx = ToolContext(home=tmp_path, env={"HOME": str(tmp_path), "PATH": ""})
    cfg = resolve.load_effective(ctx)
    assert cfg is not None and cfg.gitnexus.embeddings is True
```

Also update `test_schema_menu_metadata_does_not_change_persisted_defaults`: add `"gitnexus": {"embeddings": False},` as the last key of the expected `asdict(GlobalConfig())` dict, and add `GitNexusConfig` to the `for cls in (...)` tuple so its label/help metadata is checked.

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_config_store.py -q -k "gitnexus or schema_menu_metadata or load_effective"`. Expected: FAIL — `ImportError: cannot import name 'GitNexusConfig'`.

- [ ] **Step 3: Schema.** In `src/omc/config/schema.py`, after `SecretsConfig`:

```python
@dataclass
class GitNexusConfig:
    """Knowledge-graph options (spec 2026-10-27). Personal and global: a
    teammate's laptop must not be committed into embedding CPU time."""

    embeddings: bool = field(
        default=False,
        metadata={
            "label": "Embeddings",
            "help": "Generate local vector embeddings for semantic /omc:explain queries.",
        },
    )
```

Add to `Config` (after `worktree`, before `secrets`) and to `GlobalConfig` (after `llm`):

```python
    gitnexus: GitNexusConfig = field(
        default_factory=GitNexusConfig,
        metadata={"label": "GitNexus", "help": "Local embedding runtime for the knowledge graph."},
    )
```

- [ ] **Step 4: Store.** In `src/omc/config/store.py`: add `GitNexusConfig` to the `.schema` import. In `set_key`, before `field_names = {f.name for f in fields(cfg)}`:

```python
    if isinstance(cfg, GitNexusConfig):
        if tail or head != "embeddings":
            raise ConfigError(f"unknown config key: gitnexus.{dotted}")
        if value not in ("true", "false"):
            raise ConfigError("gitnexus.embeddings expects true or false")
        cfg.embeddings = value == "true"
        return
```

In `_hydrate`, after the `if cls is WorktreeConfig:` block:

```python
    if cls is GitNexusConfig and not isinstance(obj.embeddings, bool):
        raise ConfigError(f"invalid gitnexus.embeddings in {path}: expected true/false")
```

- [ ] **Step 5: Resolve.** In `src/omc/config/resolve.py:load_effective`, add `gitnexus=gcfg.gitnexus,` after `llm=gcfg.llm,`.

- [ ] **Step 6: Run green.** `uv run python -m pytest tests/unit/test_config_store.py tests/unit/test_resolve.py -q`. Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/config/schema.py src/omc/config/store.py src/omc/config/resolve.py tests/unit/test_config_store.py
git commit -m "feat(config): add gitnexus.embeddings (default off) to the global config

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `omc configure`: `--set gitnexus.embeddings` and the GitNexus menu section

Complexity: medium

**Files:**
- Modify: `src/omc/configure.py` (`commit` copy-back ~line 474; root composition ~lines 619-624)
- Test: `tests/unit/test_configure.py`, `tests/unit/test_configure_menu.py`

**Interfaces:**
- Consumes: Task 2's `GitNexusConfig`, `store.set_key`.
- Produces: menu root entry `"GitNexus"` → leaf `"Embeddings"` (a bool `SavedTag`), saved through the existing `leaf`/`guarded_commit`/`_apply_settings` path to the global file only.

- [ ] **Step 1: Write the failing tests.** In `tests/unit/test_configure.py` (next to `test_configure_set_global`):

```python
def test_configure_set_gitnexus_embeddings_writes_only_the_global_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    repo = _repo(tmp_path, monkeypatch)
    assert main(["configure", "--set", "gitnexus.embeddings=true"]) == 0
    assert store.load_global(home).gitnexus.embeddings is True
    assert not store.project_config_path(repo).exists()  # personal, never project
    assert main(["configure", "--set", "gitnexus.embeddings=false"]) == 0
    assert store.load_global(home).gitnexus.embeddings is False
    assert main(["configure", "--set", "gitnexus.embeddings=on"]) == 1
    assert store.load_global(home).gitnexus.embeddings is False
```

(`_repo` exists in that file already — see `test_configure_set_worktree_routes_to_project_file`.) In `tests/unit/test_configure_menu.py`: change the first assertion of `test_menu_groups_schema_fields_and_registry_providers` to `assert list(session.root) == ["LLM", "Documentation", "GitNexus"]` and add after it `assert session.root["GitNexus"]["Embeddings"].description == ("Generate local vector embeddings for semantic /omc:explain queries.")`. Then add:

```python
def test_gitnexus_embeddings_toggle_saves_only_the_global_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session, (ctx, gcfg, _, _) = _menu(tmp_path, root=repo)
    tag = session.root["GitNexus"]["Embeddings"]
    assert tag.val is False
    assert tag.update(True)
    assert gcfg.gitnexus.embeddings is True  # in-memory copy-back, not only disk
    assert store.load_global(ctx.home).gitnexus.embeddings is True
    assert not store.project_config_path(repo).exists()
    assert session.flags == (True, False, False)
    assert tag.update(False)
    assert gcfg.gitnexus.embeddings is False
    assert store.load_global(ctx.home).gitnexus.embeddings is False
```

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_configure.py tests/unit/test_configure_menu.py -q -k "gitnexus or menu_groups"`. Expected: `--set` test FAILS only if Task 2 is missing (it should PASS already through the generic `else: store.set_key(gcfg, …)` arm — if so, note it and keep it as a regression guard); the menu tests FAIL with `KeyError: 'GitNexus'` / root list mismatch.

- [ ] **Step 3: Implement.** In `_compose_menu`, replace the root loop with:

```python
    session.root = {}
    for config_field in fields(gcfg):
        if config_field.name == "llm":
            session.root[config_field.metadata["label"]] = llm
            docs_field = next(f for f in fields(gcfg.llm) if f.name == "docs")
            session.root[docs_field.metadata["label"]] = docs
        elif config_field.name == "gitnexus":
            session.root[config_field.metadata["label"]] = {
                f.metadata["label"]: leaf(gcfg.gitnexus, f.name, f"gitnexus.{f.name}")
                for f in fields(gcfg.gitnexus)
            }
```

In `commit`, after `gcfg.llm = candidate_g.llm` add `gcfg.gitnexus = candidate_g.gitnexus` (the pressure-test finding: without it the stable tag tree replays the stale value).

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_configure.py tests/unit/test_configure_menu.py tests/unit/test_configure_pty.py -q`. Expected: PASS (the PTY tests navigate by label, not by root index).

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/configure.py tests/unit/test_configure.py tests/unit/test_configure_menu.py
git commit -m "feat(configure): GitNexus → Embeddings toggle and --set gitnexus.embeddings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `gitnexus_env(home)` replaces the `GITNEXUS_ENV` constant

Complexity: simple

**Files:**
- Modify: `src/omc/toolctx.py:16-18,57-58`
- Modify: `src/omc/session.py:18,26-29,70`
- Test: `tests/unit/test_toolctx.py`, `tests/unit/test_session.py`

**Interfaces:**
- Produces: `omc.toolctx.gitnexus_env(home: Path) -> dict[str, str]` returning exactly `{"GITNEXUS_SHARED_STORE": "off", "GITNEXUS_EMBEDDING_RUNTIME_DIR": str(home / "dependencies" / "gitnexus-embeddings" / "runtime"), "HF_HOME": str(home / "dependencies" / "gitnexus-embeddings" / "hf")}`. `GITNEXUS_ENV` is removed (its only importer is `session.py`). Task 6 derives the runtime dir from this function — it is the single source of the path.

- [ ] **Step 1: Write the failing tests.** In `tests/unit/test_toolctx.py`, extend `test_child_env_forces_gitnexus_local_storage`: add `"HF_HOME": "/users/own/hf", "GITNEXUS_EMBEDDING_RUNTIME_DIR": "/users/own/rt",` to `env`, and after the existing asserts add:

```python
    base = tmp_path / ".omc" / "dependencies" / "gitnexus-embeddings"
    assert child["GITNEXUS_EMBEDDING_RUNTIME_DIR"] == str(base / "runtime")
    assert child["HF_HOME"] == str(base / "hf")  # omc's cache wins over the inherited one
```

Add a new test:

```python
def test_gitnexus_env_is_a_pure_function_of_the_omc_home(tmp_path):
    from omc.toolctx import gitnexus_env

    home = tmp_path / "custom-home"
    assert gitnexus_env(home) == {
        "GITNEXUS_SHARED_STORE": "off",
        "GITNEXUS_EMBEDDING_RUNTIME_DIR": str(home / "dependencies/gitnexus-embeddings/runtime"),
        "HF_HOME": str(home / "dependencies/gitnexus-embeddings/hf"),
    }
    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(home)})
    assert {k: ctx.child_env()[k] for k in gitnexus_env(home)} == gitnexus_env(home)
```

In `tests/unit/test_session.py`, change the exact-dict assertion in `test_session_plan_wires_notifications_for_the_provider_that_takes_argv` to:

```python
    assert plan.env == {
        "OMC_SLUG": "s",
        "OMC_PROVIDER": "codex",
        "GITNEXUS_SHARED_STORE": "off",
        "GITNEXUS_EMBEDDING_RUNTIME_DIR": "/nowhere/.omc/dependencies/gitnexus-embeddings/runtime",
        "HF_HOME": "/nowhere/.omc/dependencies/gitnexus-embeddings/hf",
    }
```

(`_ctx()` there uses `HOME=/nowhere`, so `ctx.home` is `/nowhere/.omc`.)

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_toolctx.py tests/unit/test_session.py -q -k "gitnexus or notifications_for_the_provider"`. Expected: FAIL — `KeyError: 'GITNEXUS_EMBEDDING_RUNTIME_DIR'`, `ImportError` for `gitnexus_env`, dict mismatch.

- [ ] **Step 3: Implement.** In `src/omc/toolctx.py` replace lines 16–18 with:

```python
def gitnexus_env(home: Path) -> dict[str, str]:
    """Where every omc child finds GitNexus's storage and embedding runtime.

    Unconditional: these say WHERE GitNexus would look, never WHETHER to embed
    (that is `gitnexus.embeddings`, read only by gitnexus.ensure_embeddings).
    Local storage overrides inherited sharing; GITNEXUS_STORAGE_PATH / _ROOT
    are unsupported and left untouched. The runtime dir and the Hugging Face
    cache live beside, not inside, the GitNexus clone so `omc update`'s
    `checkout -f` never touches them. HF_HOME is a Hugging Face convention;
    among omc's children only GitNexus's @huggingface/transformers reads it.
    """
    base = home / "dependencies" / "gitnexus-embeddings"
    return {
        "GITNEXUS_SHARED_STORE": "off",
        "GITNEXUS_EMBEDDING_RUNTIME_DIR": str(base / "runtime"),
        "HF_HOME": str(base / "hf"),
    }
```

and `child_env` becomes `return {**self.env, **self.uv_env, **gitnexus_env(self.home)}`. In `src/omc/session.py`: import `gitnexus_env` instead of `GITNEXUS_ENV`; the `SessionPlan.env` comment says `gitnexus_env(ctx.home) lives here because …`; line 70 becomes `**gitnexus_env(ctx.home)`; the `run_headless` comment says `No gitnexus_env here: …`.

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_toolctx.py tests/unit/test_session.py tests/unit/test_plugin.py tests/unit/test_start.py -q`. Expected: PASS. Also `grep -rn GITNEXUS_ENV src tests` prints nothing.

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/toolctx.py src/omc/session.py tests/unit/test_toolctx.py tests/unit/test_session.py
git commit -m "feat(toolctx): gitnexus_env(home) pins the embedding runtime and HF cache under ~/.omc

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `_run_narrated`: one supervised, line-narrating runner

Complexity: medium

**Files:**
- Modify: `src/omc/gitnexus.py` (new helper under the Task 1 comment block)
- Test: `tests/unit/test_gitnexus_refresh.py`

**Interfaces:**
- Consumes: `ToolContext.run_supervised`, `_WIKI_STALL_SECONDS`, `_WIKI_POLL_SECONDS` (module globals, patched by the `_fast_wiki_poll` fixture — reference them by name inside the function).
- Produces: `_run_narrated(ctx, argv, *, cwd, say, extra_env=None) -> tuple[subprocess.CompletedProcess[str], bool]` returning `(completed, stalled)`. Narrates each new, non-empty line that does not start with `{` (pino JSON) as `· <line>` (stripped, truncated to 200 chars), never the same line twice in a row. Byte activity is the only progress signal (`heartbeat=lambda: None`). Raises `OSError`/`FileNotFoundError` like `ctx.run` would — callers decide.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_gitnexus_refresh.py`):

```python
def test_run_narrated_narrates_new_plain_lines_and_skips_pino_json(tmp_path):
    import omc.gitnexus as gn

    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / "chatty"
    # Every narrated line goes to stdout: run_supervised reads the two pipes on
    # two threads, so cross-stream order is best-effort. Only the pino line (never
    # narrated) lives on stderr, where GitNexus's logger really writes it.
    tool.write_text(
        "#!/bin/sh\n"
        'echo "Generating embeddings"\n'
        'echo "Generating embeddings"\n'
        "echo '{\"level\":30,\"time\":1,\"msg\":\"pino noise\"}' >&2\n"
        'echo "   "\n'
        'echo "Generating embeddings"\n'
        'echo "Done: 3 embedded"\n'
        'echo "KEY=$GITNEXUS_LBUG_EXTENSION_INSTALL"\n'
        "exit 3\n"
    )
    tool.chmod(0o755)
    ctx = ToolContext.from_env({"HOME": str(tmp_path), "PATH": str(bindir)})
    said = []
    cp, stalled = gn._run_narrated(
        ctx,
        [str(tool)],
        cwd=str(tmp_path),
        say=said.append,
        extra_env={"GITNEXUS_LBUG_EXTENSION_INSTALL": "auto"},
    )
    assert (cp.returncode, stalled) == (3, False)
    # "not equal to the LAST narrated line": the blank and the pino line are not
    # narrated, so the third "Generating embeddings" is still a repeat.
    assert said == ["· Generating embeddings", "· Done: 3 embedded", "· KEY=auto"]
    assert "pino noise" in cp.stderr and "KEY=auto" in cp.stdout  # capture is unaffected
```

(Add `from omc.toolctx import ToolContext` to this test module's imports.)

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_gitnexus_refresh.py -q -k run_narrated`. Expected: FAIL — `AttributeError: module 'omc.gitnexus' has no attribute '_run_narrated'`.

- [ ] **Step 3: Implement** (in `src/omc/gitnexus.py`, right after the Task 1 comment block):

```python
def _run_narrated(
    ctx: ToolContext,
    argv: list[str],
    *,
    cwd: str,
    say,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run a long GitNexus step supervised for liveness and narrate its lines.

    Serves the embedding runtime install, the embedding analyze and
    `embeddings sync`: minutes of CPU and downloads that a captured `ctx.run`
    would hide (a silent minute is a bug). Byte activity is the only progress
    signal — no heartbeat token, no progress bar (GITNEXUS_PROGRESS is a
    wiki-only feature of the fork). Off a TTY analyze prints cli-progress's
    newline-mode phase lines; pino JSON lines start with "{" and are not news.
    Returns (completed, stalled) like run_supervised."""
    last = [""]

    def on_line(line: str) -> None:
        text = line.strip()
        if not text or text.startswith("{") or text == last[0]:
            return
        last[0] = text
        say(f"· {text[:200]}")

    return ctx.run_supervised(
        argv,
        cwd=cwd,
        heartbeat=lambda: None,
        stall_after=_WIKI_STALL_SECONDS,
        poll=_WIKI_POLL_SECONDS,
        extra_env=extra_env,
        on_line=on_line,
    )
```

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_gitnexus_refresh.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/gitnexus.py tests/unit/test_gitnexus_refresh.py
git commit -m "feat(gitnexus): _run_narrated — supervised runner that narrates GitNexus phase lines

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `ensure_embeddings`: probe, install, never fail

Complexity: high

**Files:**
- Modify: `src/omc/gitnexus.py` (new `embedding_runtime_dir`, `_embeddings_ready`, `ensure_embeddings` after `_run_narrated`; import `gitnexus_env`)
- Test: `tests/unit/test_gitnexus_update.py`

**Interfaces:**
- Consumes: `gitnexus_env` (Task 4), `_run_narrated` (Task 5), `gitnexus_argv`, `GitNexusConfig` on `Config`/`GlobalConfig` (Task 2).
- Produces:
  - `embedding_runtime_dir(ctx) -> Path` = `Path(gitnexus_env(ctx.home)["GITNEXUS_EMBEDDING_RUNTIME_DIR"])`.
  - `_embeddings_ready(ctx) -> bool` — both manifests exist.
  - `ensure_embeddings(ctx, cfg, *, reinstall=False, say=_say) -> bool` where `cfg` is `Config | GlobalConfig | None` (`None` = off). Narration, exact: `→ installing GitNexus embedding runtime…`, then `·` lines from the install, then `✓ GitNexus embedding runtime installed` or `✗ embedding runtime install failed (exit N): <last 400 chars of stderr/stdout> — indexing keyword-only` or `✗ embedding runtime install stalled — no progress for 300s; killed — indexing keyword-only` or `✗ embedding runtime install failed: <OSError> — indexing keyword-only`. Returns the probe's answer after the attempt.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_gitnexus_update.py`; add `import json` is not needed; add these imports at the top: `from omc.config.schema import Config, GitNexusConfig, GlobalConfig` and `from omc.gitnexus import embedding_runtime_dir, ensure_embeddings`):

```python
def _embedding_stub(tmp_path, calls, *, install_rc=0):
    """Replace the node stub: `embeddings install` populates the runtime prefix
    like npm would (or fails), everything else answers --version. Records env."""
    node = tmp_path / "bin" / "node"
    populate = (
        '/bin/mkdir -p "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/@huggingface/transformers" '
        '"$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/onnxruntime-node"; '
        ': > "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/@huggingface/transformers/package.json"; '
        ': > "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/onnxruntime-node/package.json"; '
        if install_rc == 0
        else ""
    )
    # Both npm lines on stdout: run_supervised's two reader threads make
    # cross-stream order best-effort, and the test pins the narration order.
    record = (
        'echo "node RT=$GITNEXUS_EMBEDDING_RUNTIME_DIR HF=$HF_HOME '
        f'LBUG=${{GITNEXUS_LBUG_EXTENSION_INSTALL:-unset}} $@" >> "{calls}"\n'
    )
    node.write_text(
        "#!/bin/sh\n"
        f"{record}"
        'case "$*" in\n'
        f'  *" embeddings install") {populate}echo "added 2 packages"; '
        f'echo "npm warn deprecated"; exit {install_rc} ;;\n'
        "esac\n"
        "echo 9.9.9\nexit 0\n"
    )


def _on():
    return Config(gitnexus=GitNexusConfig(embeddings=True))


def _install_calls(calls):
    text = calls.read_text() if calls.exists() else ""
    return [ln for ln in text.splitlines() if ln.endswith(" embeddings install")]


def test_ensure_embeddings_off_runs_nothing_and_says_nothing(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    said = []
    for cfg in (Config(), GlobalConfig(), None):
        assert ensure_embeddings(ctx, cfg, say=said.append) is False
    assert said == [] and not calls.exists()


def test_ensure_embeddings_on_and_ready_probes_only(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    rt = embedding_runtime_dir(ctx)
    assert rt == home / "dependencies" / "gitnexus-embeddings" / "runtime"
    for pkg in ("@huggingface/transformers", "onnxruntime-node"):
        (rt / "node_modules" / pkg).mkdir(parents=True)
        (rt / "node_modules" / pkg / "package.json").write_text("{}")
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is True
    assert said == [] and not calls.exists()


def test_ensure_embeddings_installs_once_into_the_omc_runtime_dir(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    rt = embedding_runtime_dir(ctx)
    # transformers alone is an interrupted install, not a ready runtime
    (rt / "node_modules" / "@huggingface" / "transformers").mkdir(parents=True)
    (rt / "node_modules" / "@huggingface" / "transformers" / "package.json").write_text("{}")
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is True
    cli = home / "dependencies" / "gitnexus" / "gitnexus" / "dist" / "cli" / "index.js"
    assert _install_calls(calls) == [
        f"node RT={rt} HF={home / 'dependencies/gitnexus-embeddings/hf'} LBUG=unset {cli} embeddings install"
    ]
    assert said == [
        "→ installing GitNexus embedding runtime…",
        "· added 2 packages",
        "· npm warn deprecated",
        "✓ GitNexus embedding runtime installed",
    ]
    assert (rt / "node_modules" / "onnxruntime-node" / "package.json").is_file()
    # second call: the probe answers, no node call
    assert ensure_embeddings(ctx, _on(), say=said.append) is True
    assert len(_install_calls(calls)) == 1


def test_ensure_embeddings_failed_install_narrates_and_returns_false(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls, install_rc=1)
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is False
    assert len(_install_calls(calls)) == 1
    failed = [s for s in said if s.startswith("✗ embedding runtime install failed (exit 1): ")]
    assert len(failed) == 1 and failed[0].endswith(" — indexing keyword-only")
    assert "npm warn deprecated" in failed[0]
    assert not any(s.startswith("✓") for s in said)


def test_ensure_embeddings_reinstall_runs_even_when_ready(tmp_path):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    assert ensure_embeddings(ctx, _on(), say=lambda s: None) is True  # installs
    assert ensure_embeddings(ctx, _on(), reinstall=True, say=lambda s: None) is True
    assert len(_install_calls(calls)) == 2


def test_ensure_embeddings_missing_node_is_a_narrated_miss(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home)
    real = ToolContext.run_supervised

    def no_node(self, argv, **kw):
        if argv and argv[0] == "node":
            raise FileNotFoundError("node")
        return real(self, argv, **kw)

    monkeypatch.setattr(ToolContext, "run_supervised", no_node)
    said = []
    assert ensure_embeddings(ctx, _on(), say=said.append) is False
    assert any(s.startswith("✗ embedding runtime install failed: ") and s.endswith(" — indexing keyword-only") for s in said)
```

The `_fast_wiki_poll` fixture lives in the refresh/watch test modules; add the same autouse fixture to `tests/unit/test_gitnexus_update.py` (copy the three-line fixture from `tests/unit/test_gitnexus_refresh.py`) so the supervised poll does not add a second per call.

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_gitnexus_update.py -q -k ensure_embeddings`. Expected: FAIL — `ImportError: cannot import name 'embedding_runtime_dir'`.

- [ ] **Step 3: Implement** in `src/omc/gitnexus.py` after `_run_narrated` (add `from .toolctx import ToolContext, gitnexus_env` at the top; `Config`/`GlobalConfig` go under the `TYPE_CHECKING` import):

```python
def embedding_runtime_dir(ctx: ToolContext) -> Path:
    """The one source of the runtime path is gitnexus_env (spec §4)."""
    return Path(gitnexus_env(ctx.home)["GITNEXUS_EMBEDDING_RUNTIME_DIR"])


def _embeddings_ready(ctx: ToolContext) -> bool:
    """GitNexus's own two-package rule (resolveEmbeddingRuntime): probing
    transformers alone would misread an interrupted install as ready."""
    modules = embedding_runtime_dir(ctx) / "node_modules"
    return all(
        (modules / pkg / "package.json").is_file()
        for pkg in ("@huggingface/transformers", "onnxruntime-node")
    )


def ensure_embeddings(
    ctx: ToolContext,
    cfg: Config | GlobalConfig | None,
    *,
    reinstall: bool = False,
    say=_say,
) -> bool:
    """Is the local embedding runtime ready to be spent? The ONLY reader of
    `gitnexus.embeddings` (spec §3/§5). Off (or no config) → False, silently:
    the hot path for everyone who never opts in. On → install under the §4
    runtime dir when the probe fails or `reinstall` asks (the clone's pins may
    have moved). GitNexus's CPU install is `npm install --prefix <runtime>
    --ignore-scripts` of its pinned stack; re-running it moves the packages to
    the current pins, so `--force` is never passed. Never raises, never returns
    an exit code: a failed install degrades to keyword-only (Decision 5)."""
    if cfg is None or not cfg.gitnexus.embeddings:
        return False
    if _embeddings_ready(ctx) and not reinstall:
        return True
    say("→ installing GitNexus embedding runtime…")
    tail = " — indexing keyword-only"
    try:
        cp, stalled = _run_narrated(
            ctx, gitnexus_argv(ctx, "embeddings", "install"), cwd=str(ctx.home), say=say
        )
    except OSError as exc:  # node missing or not runnable: an expected failure
        say(f"✗ embedding runtime install failed: {exc}{tail}")
        return False
    if stalled:
        say(
            f"✗ embedding runtime install stalled — no progress for "
            f"{int(_WIKI_STALL_SECONDS)}s; killed{tail}"
        )
        return False
    if cp.returncode != 0:
        excerpt = (cp.stderr or cp.stdout or "").strip()[-400:]
        say(f"✗ embedding runtime install failed (exit {cp.returncode}): {excerpt}{tail}")
        return False
    ready = _embeddings_ready(ctx)
    if ready:
        say("✓ GitNexus embedding runtime installed")
    else:
        say(f"✗ embedding runtime install exited 0 but the runtime is incomplete{tail}")
    return ready
```

`cwd=str(ctx.home)`: the install needs no repo; `ctx.home` exists whenever the CLI exists. Create nothing.

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_gitnexus_update.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/gitnexus.py tests/unit/test_gitnexus_update.py
git commit -m "feat(gitnexus): ensure_embeddings installs the local runtime under ~/.omc, never fails

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `omc update` reinstalls the runtime after a rebuild

Complexity: medium

**Files:**
- Modify: `src/omc/gitnexus.py:update_gitnexus` (signature + two call sites of `ensure_embeddings`)
- Modify: `src/omc/installer.py:157`
- Test: `tests/unit/test_gitnexus_update.py`, `tests/unit/test_installer.py` (six `lambda ctx:` monkeypatches)

**Interfaces:**
- Produces: `update_gitnexus(ctx, *, approved_origin=GITNEXUS_ORIGIN, cfg: GlobalConfig | None = None) -> int`. After a successful build/rebuild → `ensure_embeddings(ctx, cfg, reinstall=True)`; on the up-to-date short-circuit → `ensure_embeddings(ctx, cfg)` (probe only). The rc is never affected. `installer.run_update` passes `cfg=cfg`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_gitnexus_update.py`):

```python
def _global_on():
    return GlobalConfig(gitnexus=GitNexusConfig(embeddings=True))


def test_update_rebuild_with_option_on_reinstalls_runtime_even_when_ready(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    _embedding_stub(tmp_path, calls)
    assert ensure_embeddings(ctx, _on(), say=lambda s: None) is True  # ready beforehand
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    assert len(_install_calls(calls)) == 2  # the rebuild re-pins the runtime
    err = capsys.readouterr().err
    assert "✓ GitNexus embedding runtime installed" in err
    # the build (npm via _run_tool) happened before the runtime install
    recorded = calls.read_text()
    assert recorded.index("npm run build") < recorded.rindex("embeddings install")


def test_update_up_to_date_with_option_on_probes_only(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, _ = _seed_clone(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    assert ensure_embeddings(ctx, _on(), say=lambda s: None) is True
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    assert len(_install_calls(calls)) == 1  # no second install on the short-circuit
    assert "up to date" in capsys.readouterr().err


def test_update_up_to_date_with_missing_runtime_installs_it(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, _ = _seed_clone(tmp_path, home)
    _embedding_stub(tmp_path, calls)
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    assert len(_install_calls(calls)) == 1


def test_update_failed_runtime_install_does_not_fail_update(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    _embedding_stub(tmp_path, calls, install_rc=1)
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=_global_on()) == 0
    err = capsys.readouterr().err
    assert "✗ embedding runtime install failed (exit 1)" in err and "updated" in err


@pytest.mark.parametrize("cfg", [None, GlobalConfig()])
def test_update_with_option_off_or_unconfigured_never_touches_embeddings(tmp_path, cfg):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    _embedding_stub(tmp_path, calls)
    assert update_gitnexus(ctx, approved_origin=str(origin), cfg=cfg) == 0
    assert _install_calls(calls) == []
    assert "embeddings" not in calls.read_text()
```

(`import pytest` at the top of this module.) In `tests/unit/test_installer.py`, change every `monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: …)` (lines 22, 71, 82, 166–170, 310, 402) to `lambda ctx, **kw: …`, and add one test proving the config reaches the dependency step:

```python
def test_run_update_passes_the_global_config_to_the_gitnexus_step(monkeypatch, tmp_path):
    from omc import installer
    from omc.config import store
    from omc.config.schema import GlobalConfig
    from omc.toolctx import ToolContext

    home = tmp_path / "home"
    gcfg = GlobalConfig()
    gcfg.gitnexus.embeddings = True
    store.save_global(home, gcfg)
    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(home), "PATH": ""})
    seen = []
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: 0)
    monkeypatch.setattr(installer, "require_tools", lambda ctx, cfg: None)
    monkeypatch.setattr(installer, "_fresh_cli", lambda ctx: None)
    monkeypatch.setattr(installer, "ensure_plugin", lambda ctx, name, **kw: "ok")
    monkeypatch.setattr(
        "omc.gitnexus.update_gitnexus", lambda ctx, **kw: seen.append(kw.get("cfg")) or 0
    )
    assert installer.run_update(ctx) == 0
    assert len(seen) == 1 and seen[0].gitnexus.embeddings is True
```

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_gitnexus_update.py tests/unit/test_installer.py -q -k "option or runtime or passes_the_global"`. Expected: FAIL — `TypeError: update_gitnexus() got an unexpected keyword argument 'cfg'` and `lambda() got an unexpected keyword argument 'cfg'`.

- [ ] **Step 3: Implement.** `update_gitnexus` signature: `def update_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN, cfg: Config | GlobalConfig | None = None) -> int:`; docstring gains: `With the embeddings option on, a rebuilt clone re-pins the embedding runtime (its package.json gitnexusEmbeddingStack may have moved); an up-to-date clone only probes. Neither outcome touches the rc (Decision 5).` In the up-to-date branch, before `return 0`: `ensure_embeddings(ctx, cfg)`. After the final `✓ GitNexus installed/updated` prints, before `return 0`: `ensure_embeddings(ctx, cfg, reinstall=True)`. In `src/omc/installer.py:157`: `dep_rc = gitnexus.update_gitnexus(ctx, cfg=cfg)` and extend the comment above it: `cfg (possibly None: unconfigured machine) lets the step decide about the embedding runtime; None means off.`

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_gitnexus_update.py tests/unit/test_installer.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/gitnexus.py src/omc/installer.py tests/unit/test_gitnexus_update.py tests/unit/test_installer.py
git commit -m "feat(update): re-pin the embedding runtime after a GitNexus rebuild when enabled

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Embed on refresh: `--embeddings` on both analyze sites, one keyword-only retry

Complexity: high

**Files:**
- Modify: `src/omc/gitnexus.py` (`analyze_argv`, `_run_analyze`, `_destroy_and_rebuild`, `refresh_knowledge`)
- Test: `tests/unit/test_gitnexus_refresh.py`

**Interfaces:**
- Consumes: `ensure_embeddings` (Task 6), `_run_narrated` (Task 5).
- Produces:
  - `analyze_argv(ctx, base, *, embeddings: bool = False) -> list[str]` — appends `"--embeddings"` last when asked.
  - `_run_analyze(ctx, root, base, *, embed: bool = False, say=_say) -> CompletedProcess` — `embed=False`: today's captured `ctx.run`. `embed=True`: `_run_narrated(... extra_env={"GITNEXUS_LBUG_EXTENSION_INSTALL": "auto"})`; on non-zero exit or stall, narrate `✗ analyze with embeddings failed: <last 400 chars> — retrying keyword-only` (stall: `✗ analyze with embeddings stalled — no progress for 300s; killed — retrying keyword-only`) and run today's plain analyze ONCE; return that retry's result. `_require_base` before and after exactly as today.
  - `_destroy_and_rebuild(ctx, root, base, say, *, embed: bool = False)` passes `embed` and `say` through.
  - `refresh_knowledge` computes `embed = ensure_embeddings(ctx, cfg, say=say)` once, right before the first `v = verdict()` (after the reset block), and passes it to both sites. `_say`/`say` strings already in the function stay byte-identical.
  - Task 9 inserts the backfill after the index block; this task writes nothing there (no marker comment).

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_gitnexus_refresh.py`; add `import os`, `import stat`, and `from omc.config.schema import GitNexusConfig` at the top):

```python
def _embedding_fixture(tmp_path, home, *, runtime_ready=True, install_rc=0, analyze_embed_rc=0,
                       sync_rc=0, sync_count=7, phase_lines=False):
    """Restricted-PATH node stub for the embedding paths. Records
    `LBUG=<value or unset>|<argv>` per call so tests can pin which argv carried
    GITNEXUS_LBUG_EXTENSION_INSTALL=auto. analyze stamps a fresh meta.json like
    the healing stub; with --embeddings it also writes stats.embeddings (3) unless
    analyze_embed_rc != 0, in which case it writes nothing and exits that code.
    `embeddings install` populates the runtime prefix (install_rc == 0).
    `embeddings sync <path>` rewrites stats.embeddings = sync_count keeping
    lastCommit (sync_rc == 0) or exits sync_rc writing nothing."""
    ctx, calls = _ctx_with_healing_node_stub(tmp_path, home)
    node = tmp_path / "bin" / "node"
    rt = home / "dependencies" / "gitnexus-embeddings" / "runtime"
    if runtime_ready:
        for pkg in ("@huggingface/transformers", "onnxruntime-node"):
            (rt / "node_modules" / pkg).mkdir(parents=True)
            (rt / "node_modules" / pkg / "package.json").write_text("{}")
    populate = (
        f'/bin/mkdir -p "{rt}/node_modules/@huggingface/transformers" '
        f'"{rt}/node_modules/onnxruntime-node"; '
        f': > "{rt}/node_modules/@huggingface/transformers/package.json"; '
        f': > "{rt}/node_modules/onnxruntime-node/package.json"; '
        if install_rc == 0
        else ""
    )
    phases = (
        'echo "Generating embeddings"; echo "Generating embeddings"; '
        "echo '{\"level\":30,\"msg\":\"pino\"}' >&2; "
        if phase_lines
        else ""
    )
    meta = (
        '/bin/mkdir -p .gitnexus; printf \'{"branch":"main","lastCommit":"%s","repoPath":"%s"%s}\' '
        '"$(/usr/bin/git rev-parse HEAD)" "$PWD" "$1" > .gitnexus/meta.json'
    )
    node.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s\\n" "${{GITNEXUS_LBUG_EXTENSION_INSTALL:-unset}}" "$*" >> "{calls}"\n'
        "shift\n"  # drop the CLI path; $* is now the verb and its arguments
        'case "$*" in\n'
        '  "clean --force") /bin/rm -rf .gitnexus ;;\n'
        f'  "embeddings install") {populate}echo "added 2 packages"; exit {install_rc} ;;\n'
        f'  "embeddings sync "*) if [ {sync_rc} -ne 0 ]; then exit {sync_rc}; fi; '
        f'stamp() {{ {meta}; }}; stamp \',"stats":{{"embeddings":{sync_count}}}\' ;;\n'
        f'  "analyze --skip-agents-md --skip-skills --branch main --embeddings") {phases}'
        f'if [ {analyze_embed_rc} -ne 0 ]; then exit {analyze_embed_rc}; fi; '
        f'stamp() {{ {meta}; }}; stamp \',"stats":{{"embeddings":3}}\' ;;\n'
        f'  "analyze --skip-agents-md --skip-skills --branch main") stamp() {{ {meta}; }}; stamp "" ;;\n'
        "esac\n"
        "echo ok\nexit 0\n"
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    ctx.env = {**ctx.env, "PATH": str(tmp_path / "bin")}
    ctx.git_bin = "/usr/bin/git"
    return ctx, calls


def _on_cfg():
    return Config(gitnexus=GitNexusConfig(embeddings=True))


def _run_on(ctx, repo, **kw):
    said = []
    v = refresh_knowledge(ctx, _on_cfg(), str(repo), "main", say=said.append, documentation=False,
                          reset=False, **kw)
    return v, said


def _rows(calls):
    import shlex

    return [(env, shlex.split(argv)[1:]) for env, argv in
            (line.split("|", 1) for line in calls.read_text().splitlines())]


PLAIN = ["analyze", "--skip-agents-md", "--skip-skills", "--branch", "main"]
EMBED = [*PLAIN, "--embeddings"]


def test_off_keeps_todays_argv_and_never_mentions_embeddings(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", runtime_ready=False)
    v, said = _run(ctx, repo, documentation=False, reset=False)  # Config(): off
    assert v.fresh
    assert _rows(calls) == [("unset", PLAIN)]
    assert not any("embedding" in s for s in said)


def test_on_and_ready_embeds_with_extension_install_auto(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("auto", EMBED)]
    assert "→ refreshing GitNexus index (incremental)" in said and "✓ index refreshed" in said
    assert json.loads((repo / ".gitnexus/meta.json").read_text())["stats"]["embeddings"] == 3


def test_on_and_missing_runtime_installs_then_embeds(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", runtime_ready=False)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("unset", ["embeddings", "install"]), ("auto", EMBED)]
    assert said.index("✓ GitNexus embedding runtime installed") < said.index(
        "→ refreshing GitNexus index (incremental)"
    )


def test_on_with_failing_install_indexes_keyword_only(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", runtime_ready=False, install_rc=1)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("unset", ["embeddings", "install"]), ("unset", PLAIN)]
    assert any(s.startswith("✗ embedding runtime install failed (exit 1)") for s in said)


def test_embedding_analyze_failure_retries_once_keyword_only_before_any_clean(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", analyze_embed_rc=5)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("auto", EMBED), ("unset", PLAIN)]
    assert any(
        s.startswith("✗ analyze with embeddings failed: ") and s.endswith(" — retrying keyword-only")
        for s in said
    )
    assert "✓ index refreshed" in said  # the retry's rc is what the existing narration judges


def test_embedding_retry_that_stays_stale_escalates_and_rebuild_also_embeds(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    _foreign_stamping_stub(tmp_path)  # every analyze stamps a foreign repoPath: stale forever
    v, said = _run_on(ctx, repo)
    rows = _rows(calls)
    assert rows[0] == ("auto", EMBED)
    assert ("unset", ["clean", "--force"]) in rows
    assert rows[rows.index(("unset", ["clean", "--force"])) + 1] == ("auto", EMBED)
    assert any(s.startswith("✗ index still stale after analyze") for s in said)
    assert not v.fresh


def test_rebuild_path_embedding_failure_retries_keyword_only(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus" / "meta.json").write_text(json.dumps({"branch": "feature/x", "lastCommit": "old"}))
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", analyze_embed_rc=5)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("unset", ["clean", "--force"]), ("auto", EMBED), ("unset", PLAIN)]
    assert "✓ index rebuilt for main" in said


def test_embedding_analyze_phase_lines_are_narrated_once_without_pino(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, _ = _embedding_fixture(tmp_path, tmp_path / "home", phase_lines=True)
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert said.count("· Generating embeddings") == 1
    assert not any("pino" in s for s in said)
    assert said.index("· Generating embeddings") > said.index("→ refreshing GitNexus index (incremental)")
```

Note `_foreign_stamping_stub` replaces `"repoPath":"%s"` with `"repoPath":"/elsewhere%s"` — the fixture's `meta` string must contain the exact `REPO_PATH_SLOT` text `"repoPath":"%s"` (it does).

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_gitnexus_refresh.py -q -k "off_keeps or on_and or failing_install or retries or escalates or phase_lines"`. Expected: `test_off_keeps_todays_argv…` PASSES (byte-identical today); the rest FAIL — the stub records `("unset", PLAIN)` only, no `--embeddings`, no install.

- [ ] **Step 3: Implement.**

`analyze_argv`:

```python
def analyze_argv(ctx: ToolContext, base: str, *, embeddings: bool = False) -> list[str]:
    # (existing comment kept)
    # --embeddings (spec 2026-10-27 §6): GitNexus's default 50 000-node cap is
    # kept; an analyze WITHOUT the flag preserves vectors already present.
    argv = gitnexus_argv(ctx, "analyze", "--skip-agents-md", "--skip-skills", "--branch", base)
    if embeddings:
        argv.append("--embeddings")
    return argv
```

`_run_analyze`:

```python
_EMBED_WRITE_ENV = {"GITNEXUS_LBUG_EXTENSION_INSTALL": "auto"}  # Decision 9: write paths only


def _run_analyze(
    ctx: ToolContext, root: Path, base: str, *, embed: bool = False, say=_say
) -> subprocess.CompletedProcess[str]:
    """The single analyze call site for the incremental step and the rebuild.
    embed=True runs narrated (minutes of CPU, a model download) and falls back
    ONCE to today's keyword-only analyze when it fails: a model download dying
    on a plane must not cost the index (spec §6). The retry's result is what the
    caller's "still stale → destroy and rebuild" escalation judges."""
    _require_base(ctx, root, base)
    if embed:
        cp, stalled = _run_narrated(
            ctx, analyze_argv(ctx, base, embeddings=True), cwd=str(root), say=say,
            extra_env=_EMBED_WRITE_ENV,
        )
        if stalled:
            say(
                f"✗ analyze with embeddings stalled — no progress for "
                f"{int(_WIKI_STALL_SECONDS)}s; killed — retrying keyword-only"
            )
        elif cp.returncode != 0:
            excerpt = (cp.stderr or cp.stdout or "").strip()[-400:]
            say(f"✗ analyze with embeddings failed: {excerpt} — retrying keyword-only")
        else:
            return cp
        _require_base(ctx, root, base)
    cp = ctx.run(analyze_argv(ctx, base), cwd=str(root))
    if cp.returncode != 0:
        _require_base(ctx, root, base)
    return cp
```

`_destroy_and_rebuild(ctx, root, base, say, *, embed: bool = False)` calls `_run_analyze(ctx, root, base, embed=embed, say=say)`. In `refresh_knowledge`: after the `if reset:` block and before `v = verdict()` insert

```python
    # Decided once per run; true only when the option is on AND the runtime is
    # ready after any install attempt. Off emits nothing and changes no argv.
    embed = ensure_embeddings(ctx, cfg, say=say)
```

and change the three call sites: `_destroy_and_rebuild(ctx, rootp, base, say, embed=embed)` (twice) and `_run_analyze(ctx, rootp, base, embed=embed, say=say)`.

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_gitnexus_refresh.py tests/unit/test_watch.py tests/unit/test_internal.py -q`. Expected: PASS, including every pre-existing argv assertion (`Config()` is off).

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/gitnexus.py tests/unit/test_gitnexus_refresh.py
git commit -m "feat(gitnexus): embed on refresh when enabled, retry keyword-only once on failure

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: One-time backfill: `embeddings sync` for an index without vectors

Complexity: medium

**Files:**
- Modify: `src/omc/gitnexus.py` (`_embedding_count`, `_backfill_embeddings`; one block in `refresh_knowledge`)
- Test: `tests/unit/test_gitnexus_refresh.py`

**Interfaces:**
- Consumes: `read_index_meta`, `_run_narrated`, `_EMBED_WRITE_ENV`, the Task 8 fixture `_embedding_fixture` / `_run_on` / `_rows`.
- Produces: `_embedding_count(root) -> int` (`stats.embeddings` from `read_index_meta`, 0 when absent/non-int); `_backfill_embeddings(ctx, root, say) -> None` running `node <cli> embeddings sync <root>` narrated with `→ embedding the existing index (one-time)…`, then `✓ index embedded (N vectors)` when the count turned positive, else `✗ embeddings sync failed (exit N): <tail>` / `✗ embeddings sync stalled — no progress for 300s; killed` / `✗ embeddings sync exited 0 but stats.embeddings is still 0`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_gitnexus_refresh.py`):

```python
def test_fresh_index_without_vectors_is_backfilled_once(tmp_path):
    _, repo = _repo_with_origin(tmp_path)  # fresh index, no stats at all
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    head = _git_out(repo, "rev-parse", "HEAD")
    v, said = _run_on(ctx, repo)
    assert v.fresh
    assert _rows(calls) == [("auto", ["embeddings", "sync", str(repo)])]
    meta = json.loads((repo / ".gitnexus/meta.json").read_text())
    assert meta["stats"]["embeddings"] == 7 and meta["lastCommit"] == head
    assert "→ embedding the existing index (one-time)…" in said
    assert "✓ index embedded (7 vectors)" in said
    assert "✓ knowledge is current" not in said  # a minutes-long pass is work
    again, said2 = _run_on(ctx, repo)
    assert again.fresh and len(_rows(calls)) == 1  # one JSON read, no second sync
    assert "✓ knowledge is current" in said2


def test_zero_count_also_backfills_but_positive_count_does_not(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    meta = repo / ".gitnexus/meta.json"
    data = json.loads(meta.read_text())
    meta.write_text(json.dumps({**data, "stats": {"embeddings": 0}}))
    v, _ = _run_on(ctx, repo)
    assert v.fresh and len(_rows(calls)) == 1
    meta.write_text(json.dumps({**data, "stats": {"embeddings": 7}}))
    v, said = _run_on(ctx, repo)
    assert v.fresh and len(_rows(calls)) == 1 and "✓ knowledge is current" in said


def test_backfill_after_a_stale_index_heals_runs_once_after_the_clean_verdict(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _stale_index(repo)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    v, _ = _run_on(ctx, repo)
    assert v.fresh
    # the incremental analyze already wrote stats.embeddings = 3 → no sync
    assert _rows(calls) == [("auto", EMBED)]


def test_backfill_failure_is_narrated_and_leaves_the_verdict_alone(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home", sync_rc=4)
    before = (repo / ".gitnexus/meta.json").read_bytes()
    v, said = _run_on(ctx, repo)
    assert v.fresh and (repo / ".gitnexus/meta.json").read_bytes() == before
    assert _rows(calls) == [("auto", ["embeddings", "sync", str(repo)])]
    assert any(s.startswith("✗ embeddings sync failed (exit 4): ") for s in said)
    assert "✓ knowledge is current" not in said


def test_off_never_backfills(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    ctx, calls = _embedding_fixture(tmp_path, tmp_path / "home")
    v, said = _run(ctx, repo, documentation=False, reset=False)
    assert v.fresh and not calls.exists() and "✓ knowledge is current" in said


def test_embedding_count_reads_whichever_metadata_file_wins(tmp_path):
    import omc.gitnexus as gn

    root = tmp_path / "r"
    (root / ".gitnexus").mkdir(parents=True)
    assert gn._embedding_count(root) == 0
    (root / ".gitnexus/meta.json").write_text('{"stats":{"embeddings":"7"}}')
    assert gn._embedding_count(root) == 0  # non-int is "no vectors"
    (root / ".gitnexus/meta.json").write_text('{"stats":{"embeddings":7}}')
    assert gn._embedding_count(root) == 7
    (root / ".gitnexus/gitnexus.json").write_text('{"stats":{"embeddings":2}}')
    assert gn._embedding_count(root) == 2  # gitnexus.json wins, meta.json ignored
```

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_gitnexus_refresh.py -q -k "backfill or embedding_count or positive_count"`. Expected: FAIL — no `embeddings sync` row recorded, `✓ knowledge is current` present, `AttributeError: _embedding_count`.

- [ ] **Step 3: Implement.** Helpers (after `_run_analyze`'s section):

```python
def _embedding_count(root: Path) -> int:
    """stats.embeddings from the index metadata (gitnexus.json first, then
    meta.json — read_index_meta's rule); absent, unreadable or non-int → 0."""
    meta = read_index_meta(root) or {}
    stats = meta.get("stats")
    count = stats.get("embeddings") if isinstance(stats, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def _backfill_embeddings(ctx: ToolContext, root: Path, say) -> None:
    """GitNexus's analyze short-circuits "Already up to date" whatever flags it
    carries, so an index that turns the option on would gain vectors one commit
    at a time, forever. `embeddings sync` adds the missing vectors to a healthy
    index, checkpoints for resume, writes stats.embeddings and never touches
    lastCommit (verified, see the Embeddings comment above) — it cannot move the
    freshness verdict either way. Failure is narrated only."""
    say("→ embedding the existing index (one-time)…")
    cp, stalled = _run_narrated(
        ctx, gitnexus_argv(ctx, "embeddings", "sync", str(root)), cwd=str(root), say=say,
        extra_env=_EMBED_WRITE_ENV,
    )
    if stalled:
        say(f"✗ embeddings sync stalled — no progress for {int(_WIKI_STALL_SECONDS)}s; killed")
        return
    if cp.returncode != 0:
        say(f"✗ embeddings sync failed (exit {cp.returncode}): {(cp.stderr or cp.stdout or '').strip()[-400:]}")
        return
    count = _embedding_count(root)
    if count > 0:
        say(f"✓ index embedded ({count} vectors)")
    else:
        say("✗ embeddings sync exited 0 but stats.embeddings is still 0")
```

In `refresh_knowledge`, immediately after the `if v.index_codes():` block closes (before `if not documentation:`):

```python
    if embed and _embedding_count(rootp) == 0:
        # Clean index verdict, option on, no vectors yet: the one-time backfill
        # (spec §6). Once stats.embeddings is positive this is a single JSON read.
        did_anything = True
        _backfill_embeddings(ctx, rootp, say)
```

(`v` has no index codes here: every stale path above returned already.)

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_gitnexus_refresh.py tests/unit/test_watch.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add src/omc/gitnexus.py tests/unit/test_gitnexus_refresh.py
git commit -m "feat(gitnexus): one-time embeddings sync backfill for an index without vectors

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `gitnexus-explain` skill relays the keyword-only hint

Complexity: simple

**Files:**
- Modify: `skills/gitnexus-explain/SKILL.md` (Step 2)
- Test: `tests/unit/test_plugin_manifests.py:test_gitnexus_explain_contract`

**Interfaces:**
- Produces: one paragraph in Step 2 (record §7). The skill relays, never fixes.

- [ ] **Step 1: Write the failing needles.** Extend `test_gitnexus_explain_contract` with:

```python
    for needle in (
        "keyword-only",
        "gitnexus.embeddings",
        "semantic when the index carries vectors",
        "relay that as a hint",
        "never try to enable it",
    ):
        assert needle in text, f"gitnexus-explain missing {needle!r}"
    assert "analyze --embeddings" not in text  # the skill never runs the fix itself
```

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_plugin_manifests.py -q -k gitnexus_explain_contract`. Expected: FAIL — `gitnexus-explain missing 'keyword-only'`.

- [ ] **Step 3: Add the paragraph** to `skills/gitnexus-explain/SKILL.md`, after the "The proxy exits 1 with an install hint…" paragraph in Step 2:

```markdown
Queries are semantic when the index carries vectors. GitNexus's stderr line
"This index has no embedding vectors — results are keyword-only" means the user
has not enabled `gitnexus.embeddings` (`omc configure` → GitNexus → Embeddings,
or `--set gitnexus.embeddings=true`), or the runtime is missing, or the one-time
backfill has not run yet. Relay that as a hint in your findings; never try to
enable it from here.
```

- [ ] **Step 4: Run green.** `uv run python -m pytest tests/unit/test_plugin_manifests.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add skills/gitnexus-explain/SKILL.md tests/unit/test_plugin_manifests.py
git commit -m "docs(skills): gitnexus-explain relays the keyword-only hint to gitnexus.embeddings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: README: recipe slots for `gitnexus.embeddings`, then regenerate

Complexity: medium

**Files:**
- Modify: `.claude/skills/regenerate-readme/SKILL.md` (Configure slot ~line 102–107; "How omc is put together" slot ~line 212)
- Regenerate: `README.md` (via the `regenerate-readme` skill — never by hand)
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: Tasks 2–9 landed (the recipe renders `omc configure --help` and reads `schema.py`, `toolctx.py`).
- Produces: README Configure section names `gitnexus.embeddings` (off by default; `omc configure` → GitNexus → Embeddings, or `--set gitnexus.embeddings=true`); "How omc is put together" names the `~/.omc/dependencies/gitnexus-embeddings/{runtime,hf}` layout.

- [ ] **Step 1: Write the failing guard** (append to `tests/unit/test_cli.py`, next to `test_readme_documents_human_review`):

```python
def test_readme_documents_gitnexus_embeddings():
    root = Path(__file__).resolve().parents[2]
    recipe = (root / ".claude/skills/regenerate-readme/SKILL.md").read_text()
    readme = (root / "README.md").read_text()
    for text, name in ((recipe, "recipe"), (readme, "README")):
        assert "gitnexus.embeddings" in text, f"{name} does not mention gitnexus.embeddings"
        assert "gitnexus-embeddings" in text, f"{name} does not name the runtime layout"
    assert "--set gitnexus.embeddings=true" in readme
    assert "off by default" in readme.lower() or "default off" in readme.lower()
```

- [ ] **Step 2: Run red.** `uv run python -m pytest tests/unit/test_cli.py -q -k readme_documents_gitnexus`. Expected: FAIL — `recipe does not mention gitnexus.embeddings`.

- [ ] **Step 3: Edit the recipe.** In the `## Configure` slot, after `Native notifications; Branch prefix; Base branch.` add the sentence: `GitNexus → Embeddings (\`gitnexus.embeddings\`, off by default; also \`--set gitnexus.embeddings=true\`): omc installs GitNexus's local embedding runtime under \`~/.omc\`, embeds on every refresh, backfills an existing index once, and falls back to keyword-only indexing when the runtime or a download fails — never failing update, watch or refresh.` In the `## How omc is put together` slot item 2, after the `~/.omc/dependencies/gitnexus` sentence add: `When \`gitnexus.embeddings\` is on, the embedding runtime and model cache live beside the clone under \`~/.omc/dependencies/gitnexus-embeddings/{runtime,hf}\` (\`GITNEXUS_EMBEDDING_RUNTIME_DIR\`, \`HF_HOME\` from \`gitnexus_env\`), so an update of the clone never touches them; \`/omc:explain\` queries become semantic and the keyword-only warning disappears.` Also add `\`src/omc/gitnexus.py\` (embeddings: ensure_embeddings, the sync backfill)` to the "How omc is put together" sources row.

- [ ] **Step 4: Regenerate.** Invoke the `regenerate-readme` skill (Claude Code: `/regenerate-readme`; Codex: follow `.claude/skills/regenerate-readme/SKILL.md` directly). Follow its steps 1–6 in full, including `uv run --frozen pytest tests/unit/test_cli.py -q -k 'readme or shell_integration_documents'`.

- [ ] **Step 5: Run green.** `uv run python -m pytest tests/unit/test_cli.py -q`. Expected: PASS (new guard included). `git diff --check` clean.

- [ ] **Step 6: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add .claude/skills/regenerate-readme/SKILL.md README.md tests/unit/test_cli.py
git commit -m "docs(readme): document gitnexus.embeddings and the embedding runtime layout

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: E2E: pin Node, pre-bake the runtime, prove embeddings on the real repo

Complexity: high

**Files:**
- Modify: `docker/Dockerfile.e2e:26` (FROM pin) and a new RUN after the GitNexus pre-bake (line ~87), before `COPY . /repo`
- Modify: `tests/e2e/test_e2e_gitnexus.py` (one new test + one helper)

**Interfaces:**
- Consumes: everything above landed; the fork's `embeddings install`, `analyze --embeddings`, `clean --force` (unregisters), the pre-baked CLI at `_CLI`.
- Produces: an image whose cached layers hold the runtime (two manifests under `/root/.omc/dependencies/gitnexus-embeddings/runtime/node_modules/`), the model under `/root/.omc/dependencies/gitnexus-embeddings/hf`, and LadybugDB's VECTOR extension; and `test_embeddings_opt_in_refresh_embeds_the_real_repo`, which needs no LLM token. The test is NOT run in this task (`/omc:verify` runs it at the milestone); the task guarantees the code is importable and lint-clean and that the image builds.

- [ ] **Step 1: Resolve the Node pin (Decision 7: minor ≥ 22.18).**

```bash
curl -s https://nodejs.org/dist/index.json | python3 -c '
import json, sys
latest = next(e["version"] for e in json.load(sys.stdin) if e["version"].startswith("v22."))
major, minor, patch = (int(x) for x in latest[1:].split("."))
assert (major, minor) >= (22, 18), latest
print(latest[1:])'
```

Expected: a version like `22.NN.P` with `NN ≥ 18`. Then confirm the tag exists: `docker manifest inspect node:<22.NN.P>-bookworm-slim > /dev/null && echo ok` (if `docker` is unavailable on this host, `curl -sI https://hub.docker.com/v2/repositories/library/node/tags/<22.NN.P>-bookworm-slim | head -1` must show `200`).

- [ ] **Step 2: Write the E2E test** (append to `tests/e2e/test_e2e_gitnexus.py`):

```python
_INDEX_META = (
    "import json; from pathlib import Path; root=Path('/repo/.gitnexus'); "
    "meta=root/'gitnexus.json'; meta=meta if meta.exists() else root/'meta.json'; "
    "print(meta.read_text())"
)


def test_embeddings_opt_in_refresh_embeds_the_real_repo(container):
    """Real GitNexus, real local embedding runtime, no LLM (spec 2026-10-27 §9).
    The runtime, model and VECTOR extension are pre-baked into the image
    (Dockerfile.e2e), so this needs no network for them. Artifacts, not
    transcripts: stats.embeddings on the FLAT slot, and a query whose stderr
    no longer carries GitNexus's keyword-only warning."""
    configure_omc(container, "claude")
    rc, out = run_in(container, ["omc", "configure", "--set", "gitnexus.embeddings=true"])
    assert rc == 0, out
    rc, cfg_text = run_in(container, ["cat", "/root/.omc/config.yaml"])
    assert rc == 0 and "gitnexus:\n  embeddings: true" in cfg_text, cfg_text
    rc, head = run_in(container, ["git", "-C", "/repo", "rev-parse", "HEAD"])
    assert rc == 0, head

    # First index of /repo with the option on: embeds every node once (minutes
    # of CPU at most; pytest-timeout is 300 s, function-only).
    rc, out = run_in(container, ["omc", "internal", "gitnexus", "refresh"], cwd="/repo", timeout=240)
    assert rc == 0, out
    assert "retrying keyword-only" not in out, out  # the embedding analyze itself succeeded
    assert "indexing keyword-only" not in out, out  # the pre-baked runtime was found
    rc, meta_text = run_in(container, ["python3", "-c", _INDEX_META])
    assert rc == 0, meta_text
    meta = json.loads(meta_text)
    assert meta["lastCommit"] == head.strip(), meta
    assert isinstance(meta.get("stats", {}).get("embeddings"), int), meta
    assert meta["stats"]["embeddings"] > 0, meta
    rc, _ = run_in(container, ["test", "!", "-e", "/repo/.gitnexus/branches"])
    assert rc == 0, "embedding analyze created a branch sub-slot; the flat slot must be the index"

    # The runtime and model live under ~/.omc, never ~/.gitnexus (Decision 4).
    for rel in ("runtime/node_modules/@huggingface/transformers/package.json",
                "runtime/node_modules/onnxruntime-node/package.json"):
        rc, _ = run_in(container, ["test", "-f", f"/root/.omc/dependencies/gitnexus-embeddings/{rel}"])
        assert rc == 0, rel
    rc, _ = run_in(container, ["test", "!", "-e", "/root/.gitnexus/embedding-runtime"])
    assert rc == 0, "a runtime landed in ~/.gitnexus behind omc's back"

    # A semantic query: exit 0 and no keyword-only warning on stderr.
    rc, out = run_in(
        container,
        ["omc", "internal", "gitnexus", "query", "how is the knowledge snapshot repaired"],
        cwd="/repo",
    )
    assert rc == 0, out
    assert "keyword-only" not in out, out
    assert "OMC_KNOWLEDGE" not in out, out  # the refresh left the snapshot fresh

    # Second refresh: nothing to do, no second embedding pass.
    rc, out = run_in(container, ["omc", "internal", "gitnexus", "refresh"], cwd="/repo", timeout=120)
    assert rc == 0, out
    assert "✓ knowledge is current" in out, out
    assert "embedding the existing index" not in out, out
```

- [ ] **Step 3: Lint red → green on the test file.** Run `uvx ruff format --check tests/e2e/test_e2e_gitnexus.py && uvx ruff check tests/e2e/test_e2e_gitnexus.py && uv run python -c "import tests.e2e.test_e2e_gitnexus"`. Fix formatting (`uvx ruff format tests/e2e/test_e2e_gitnexus.py`) until clean. Also run `uv run python -m pytest tests/e2e/test_e2e_gitnexus.py --collect-only -q -k embeddings_opt_in` and expect `1 test collected`.

- [ ] **Step 4: Dockerfile.** Change line 26 to `FROM node:<22.NN.P>-bookworm-slim` with the comment above it:

```dockerfile
# Pinned minor (spec 2026-10-27 Decision 7): GitNexus `engines` is
# ^22.18.0 || >=24.11.0 and its embedding-runtime loader needs
# module.registerHooks (Node >= 22.15); the floating node:22 tag could drift
# below either. Bump deliberately, together with the pre-bake below.
```

After the GitNexus pre-bake `RUN` (the one ending in `node … --version`) and BEFORE `COPY . /repo`, add:

```dockerfile
# Pre-bake the local embedding runtime, the embedding model and LadybugDB's
# VECTOR extension (spec 2026-10-27 §9). The two paths MUST equal what
# src/omc/toolctx.py:gitnexus_env derives from OMC_HOME=/root/.omc, so the
# tests' refresh finds them. One `analyze --embeddings` on a throwaway fixture
# downloads the model into HF_HOME and the extension into cached layers; the
# fixture is then `clean --force`d (unregisters it) and removed. Before
# `COPY . /repo` so the layer caches across repo changes.
RUN set -eux; \
    export GITNEXUS_SHARED_STORE=off GITNEXUS_LBUG_EXTENSION_INSTALL=auto; \
    export GITNEXUS_EMBEDDING_RUNTIME_DIR=/root/.omc/dependencies/gitnexus-embeddings/runtime; \
    export HF_HOME=/root/.omc/dependencies/gitnexus-embeddings/hf; \
    CLI=/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js; \
    node "$CLI" embeddings install; \
    test -f "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/@huggingface/transformers/package.json"; \
    test -f "$GITNEXUS_EMBEDDING_RUNTIME_DIR/node_modules/onnxruntime-node/package.json"; \
    mkdir -p /tmp/embed-fixture && cd /tmp/embed-fixture && git init -q -b main; \
    printf 'def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n' > calc.py; \
    printf 'from calc import add\n\n\ndef total(xs):\n    t = 0\n    for x in xs:\n        t = add(t, x)\n    return t\n' > report.py; \
    printf '# fixture\n' > README.md; \
    git -c user.email=e2e@omc.invalid -c user.name="omc e2e" add -A; \
    git -c user.email=e2e@omc.invalid -c user.name="omc e2e" commit -qm fixture; \
    node "$CLI" analyze --skip-agents-md --skip-skills --branch main --embeddings; \
    python3 -c 'import json, pathlib; r = pathlib.Path(".gitnexus"); p = r / "gitnexus.json" if (r / "gitnexus.json").exists() else r / "meta.json"; m = json.loads(p.read_text()); assert m["stats"]["embeddings"] > 0, m'; \
    test -d "$HF_HOME"; \
    node "$CLI" clean --force; \
    cd / && rm -rf /tmp/embed-fixture; \
    test ! -e /root/.gitnexus/embedding-runtime
```

- [ ] **Step 5: Build the image once to prove the layers.** `docker build -f docker/Dockerfile.e2e --build-arg TARGETARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/') -t omc-e2e:embeddings-prebake .` (from the worktree root; first build is slow). Expected: exit 0; the embeddings RUN prints the install and one analyze with a `Generating embeddings` phase. If the pre-bake fails on the fixture analyze (for example a VECTOR extension download failure), report the exact error — do not weaken the assertions.

- [ ] **Step 6: Lint the world.** `uvx ruff format --check . && uvx ruff check .` Expected: clean.

- [ ] **Step 7: Commit.**

```bash
git checkout -- uv.lock 2>/dev/null
git add docker/Dockerfile.e2e tests/e2e/test_e2e_gitnexus.py
git commit -m "test(e2e): pin Node 22 minor, pre-bake the embedding runtime, prove embeddings on /repo

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Milestone

After Task 12 the conductor runs `/omc:check` → `/omc:build` → `/omc:verify` (the verify stage builds the image from this checkout and runs `tests/e2e`, including the new test). The new E2E test needs no provider token; `just e2e-tests` still gates on `.env` tokens for the other tests.

## Self-review against the record

- §3 Config → Tasks 2–3 (schema, hydration bool check, `set_key`, `load_effective`, `--set`, menu root entry, global-only write, legacy untouched: `load_legacy`'s explicit `GlobalConfig(llm=…)` gets the default section).
- §4 Env → Task 4 (`gitnexus_env`, `child_env`, `session.py`; `GITNEXUS_LBUG_EXTENSION_INSTALL` only on the write paths — Tasks 8–9 via `_EMBED_WRITE_ENV`).
- §5 Runtime install → Tasks 5–7 (`_run_narrated`, probe, install without `--force`, two callers: `update_gitnexus` with `reinstall=True` after a build and probe-only when up to date; `refresh_knowledge` at the start of its index phase; `omc configure` installs nothing).
- §6 Index flow → Tasks 8–9 (flag on both sites through `_run_analyze`, narrated embedding analyze with `auto`, plain analyze stays `ctx.run`, one retry, backfill keyed on `stats.embeddings`, counts as work, `snapshot_freshness` untouched); Task 1 verifies and records the sync routing at the call site.
- §7 Skills/docs → Tasks 10–11. Behavior layer unchanged (no task touches `src/omc/distribution/AGENTS.md`).
- §9 Tests → every listed unit case has a test above; the real-tool E2E and Dockerfile are Task 12.
- §10 Risks: the first-opt-in busy-lock duration and disk cost need no code; the Node floor is the retry path (Task 8) plus the pin (Task 12).
- Type consistency: `ensure_embeddings(ctx, cfg, *, reinstall=False, say=_say) -> bool` (Tasks 6, 7, 8); `_run_narrated(ctx, argv, *, cwd, say, extra_env=None) -> (cp, stalled)` (Tasks 5, 6, 8, 9); `_run_analyze(ctx, root, base, *, embed=False, say=_say)` (Task 8); `update_gitnexus(ctx, *, approved_origin=…, cfg=None)` (Task 7); `gitnexus_env(home)` / `embedding_runtime_dir(ctx)` (Tasks 4, 6).
