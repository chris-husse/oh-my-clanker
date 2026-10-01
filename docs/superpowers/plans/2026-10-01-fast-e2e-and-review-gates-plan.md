# Fast E2E and Review Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The default E2E run finishes in minutes with every case in parallel and none above 5 minutes; one golden lifecycle path runs once per provider and is snapshotted per stage; variation tests fork from a stage; the review stage rejects slow or serial-only tests.

**Architecture:** A `scripts/e2e.sh` runner builds the image once, runs the golden stage tests sequentially (snapshotting the container with `docker commit` after each stage), then runs everything else with `pytest -n auto --dist loadgroup`. Variation tests start a container from a stage image and resume the named session. pytest-timeout enforces a 300 s ceiling; `-n auto` enforces parallel-safety; a hermetic `GIT_CONFIG_GLOBAL` makes the unit suite parallel-safe. Codex tests carry an `xdist_group` and run serially behind `just codex-gate`.

**Tech Stack:** pytest, pytest-xdist 3.8, pytest-timeout 2.4, testcontainers, docker CLI, Markdown skills.

**Spec:** `docs/superpowers/specs/2026-10-01-fast-e2e-and-review-gates-design.md`

## Global Constraints

- Every test has a 300 s ceiling (`timeout = 300`, `timeout_method = "signal"`; the spec said `thread`, corrected: the thread method cannot interrupt and kills the whole worker, SIGALRM interrupts `subprocess.communicate` cleanly). Only the `expensive` tier may override with `@pytest.mark.timeout`.
- No test may hard-fail or diverge under `-n auto`. Serialization is expressed only through `xdist_group`.
- Test actors and judges default to `claude-sonnet-5-5` (`CLAUDE_E2E_MODEL`, `CLAUDE_E2E_JUDGE_MODEL`); Codex stays `gpt-6-astra` on the account volume.
- Stage images are tagged `omc-e2e-stage:<provider>-<stage>-<source>` where `<source>` is `git rev-parse --short HEAD` plus `-dirty` when the tree is dirty. The in-image manifest is `/tmp/omc-stage.json` with keys `provider, stage, slug, repo, worktree, branch, model, judge_model`.
- `_image_provenance` requires `OMC_E2E_PREBUILT_SOURCE` whenever `OMC_E2E_PREBUILT_IMAGE` is set; the runner sets both.
- Codex account volume: never mounted into two running containers at once; the lock blocks instead of failing (`OMC_E2E_AUTH_LOCK_TIMEOUT`, default 3600 s).
- Verbatim review-stage sentences: `No slow tests` and `No serial-only tests` (headings), each "an Important finding".
- Commit after every task with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A test that passes serially but not under `-n auto` because it inherits host git config (GPG signing). Pinned in Task 1 by running `just check` under `-n auto` with the hermetic config.
2. A variation started from a stage image whose named session does not resume (different container id, same paths). Pinned in Task 4 `test_resume_from_primer_snapshot`.
3. A Codex test scheduled on two workers at once. Pinned in Task 1 `test_codex_items_share_one_xdist_group` and the blocking-lock unit test.
4. A dirty working tree reusing a stale snapshot. Pinned in Task 2 `test_source_id_marks_dirty_trees`.
5. The golden `implemented` stage exceeding 300 s on today's omc. Not a test bug: the stage fails on timeout and the report names omc's implement duration as the next thing to fix.

---

### Task 1: Parallel-safe suites, ceiling, image build, Codex grouping

**Model:** heavy coding tier

**Files:**
- Modify: `pyproject.toml` (dev deps already added: `pytest-xdist>=3.8`, `pytest-timeout>=2.4`; pytest options)
- Create: `tests/conftest.py` (hermetic git config)
- Create: `tests/e2e/parallel.py`
- Modify: `tests/e2e/conftest.py` (collection hook, marker import)
- Modify: `tests/e2e/codex_auth.py:104-112` (blocking lock)
- Modify: `tests/unit/test_e2e_codex_auth.py` (lock expectations)
- Create: `scripts/e2e.sh`
- Modify: `justfile` (`check`, `e2e-tests`, `lifecycle-tests` → `golden`, `codex-gate`, `lifecycle-full`)
- Modify: `docker/Dockerfile.e2e:89-91` (plugin setup strict), `docker/setup-plugins.sh` (idempotent)
- Test: `tests/unit/test_e2e_parallel.py`

**Interfaces:**
- Produces: `tests/e2e/parallel.py`: `CODEX_GROUP = "codex-account"`, `item_provider(item) -> str | None`, `wait_for_lock(fd, timeout: float, poll: float = 1.0) -> None` (raises `TimeoutError`); `scripts/e2e.sh` exporting `OMC_E2E_PREBUILT_IMAGE`, `OMC_E2E_PREBUILT_SOURCE`, `OMC_E2E_SOURCE_ID`; markers `codex_gate`, `golden`, `variation(stage)`.

- [ ] **Step 1: pytest options**

In `pyproject.toml` `[tool.pytest.ini_options]` add:

```toml
timeout = 300
timeout_method = "signal"
```

and markers:

```toml
    "codex_gate: Codex integration cases; serial on the account volume (just codex-gate)",
    "golden: ordered golden-path stage; run with -n 0 (scripts/e2e.sh phase 1)",
    "variation(stage): forks a container from a golden stage snapshot",
```

- [ ] **Step 2: hermetic git for every test process**

Create `tests/conftest.py`:

```python
"""Root test configuration: hermetic git for every subprocess the suites spawn.

The unit suite creates hundreds of throwaway repos. Inheriting the host's
global git config made them depend on it — on a host with `commit.gpgsign`,
sixteen parallel workers exhausted gpg-agent ("Cannot allocate memory") and
`git commit` failed with exit 128. A private global config, pointed at by
GIT_CONFIG_GLOBAL before any test runs, removes the host from the picture.
"""

import os
import tempfile
from pathlib import Path

_GIT_CONFIG = Path(tempfile.mkdtemp(prefix="omc-tests-git-")) / "gitconfig"
_GIT_CONFIG.write_text(
    "[user]\n\tname = omc tests\n\temail = tests@omc.invalid\n"
    "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
    "[init]\n\tdefaultBranch = main\n"
)
os.environ["GIT_CONFIG_GLOBAL"] = str(_GIT_CONFIG)
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
```

- [ ] **Step 3: parallel helpers + tests (TDD)**

`tests/unit/test_e2e_parallel.py`:

```python
import fcntl
import threading
import time

import pytest

from tests.e2e.parallel import CODEX_GROUP, item_provider, wait_for_lock


class _Node:
    def __init__(self, params=None, marker=None):
        self.callspec = type("C", (), {"params": params or {}})() if params is not None else None
        self._marker = marker

    def get_closest_marker(self, name):
        return self._marker if name == "e2e_provider" and self._marker else None


def test_item_provider_prefers_param_then_marker():
    assert item_provider(_Node(params={"provider": "codex"})) == "codex"
    marker = type("M", (), {"args": ("claude",)})()
    assert item_provider(_Node(marker=marker)) == "claude"
    assert item_provider(_Node()) is None


def test_codex_group_is_one_constant():
    assert CODEX_GROUP == "codex-account"


def test_wait_for_lock_blocks_until_released(tmp_path):
    path = tmp_path / "lock"
    holder = path.open("a+b")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    waiter = path.open("a+b")
    released = []

    def release():
        time.sleep(0.3)
        fcntl.flock(holder, fcntl.LOCK_UN)
        released.append(time.monotonic())

    threading.Thread(target=release).start()
    started = time.monotonic()
    wait_for_lock(waiter, timeout=5, poll=0.05)
    assert released and time.monotonic() - started >= 0.3


def test_wait_for_lock_times_out(tmp_path):
    path = tmp_path / "lock"
    holder = path.open("a+b")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(TimeoutError, match="still held after"):
        wait_for_lock(path.open("a+b"), timeout=0.2, poll=0.05)
```

`tests/e2e/parallel.py`:

```python
"""Parallel-execution helpers shared by the E2E conftest and the Codex lock."""

from __future__ import annotations

import fcntl
import time

CODEX_GROUP = "codex-account"


def item_provider(item) -> str | None:
    """The provider a collected test declares: a `provider` param wins, then
    the `e2e_provider` marker, else None (generic container)."""
    callspec = getattr(item, "callspec", None)
    params = getattr(callspec, "params", {}) if callspec is not None else {}
    if "provider" in params:
        return params["provider"]
    marker = item.get_closest_marker("e2e_provider")
    return marker.args[0] if marker else None


def wait_for_lock(fd, timeout: float, poll: float = 1.0) -> None:
    """Take an exclusive flock, waiting up to `timeout` seconds. Polling with
    LOCK_NB keeps the wait interruptible by pytest-timeout's SIGALRM."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"lock still held after {timeout:.0f}s") from None
            time.sleep(poll)
```

In `tests/e2e/conftest.py` replace the `e2e_provider` fixture body with `return item_provider(request.node)` (import from `.parallel`) and add:

```python
def pytest_collection_modifyitems(items):
    """Codex tests share one xdist group so the account volume is never
    mounted by two workers at once; golden stages are grouped per provider
    so `-n` never reorders them."""
    for item in items:
        provider = item_provider(item)
        if provider == "codex":
            item.add_marker(pytest.mark.xdist_group(CODEX_GROUP))
        if item.get_closest_marker("golden"):
            item.add_marker(pytest.mark.xdist_group(f"golden-{provider or 'claude'}"))
```

- [ ] **Step 4: blocking Codex lock**

In `tests/e2e/codex_auth.py` replace the `PYTEST_XDIST_WORKER` fail and the `LOCK_NB` attempt with:

```python
    lock_dir = Path(os.environ.get("OMC_E2E_AUTH_LOCK_DIR", tempfile.gettempdir()))
    lock_path = lock_dir / f"omc-codex-auth-{volume}.lock"
    wait = float(os.environ.get("OMC_E2E_AUTH_LOCK_TIMEOUT", "3600"))
    with lock_path.open("a+b") as lock_file:
        # Under xdist, Codex items share one group (tests/e2e/parallel.py), so
        # this normally returns at once; a misrouted item waits its turn
        # instead of failing — the volume's refresh token must never rotate twice.
        try:
            wait_for_lock(lock_file, wait)
        except TimeoutError:
            pytest.fail(f"Codex account volume {volume} is still in use after {wait:.0f}s.")
```

Update `tests/unit/test_e2e_codex_auth.py`: `test_account_lock_rejects_concurrent_use` sets `OMC_E2E_AUTH_LOCK_TIMEOUT=0.2` and matches `"still in use after"`; the `[locked]` case of `test_codex_fixture_keeps_account_failures_loud` does the same. Remove any assertion on "cannot run under pytest-xdist".

- [ ] **Step 5: runner script and recipes**

`scripts/e2e.sh`:

```bash
#!/usr/bin/env bash
# E2E runner: build the image ONCE from this checkout, run the golden stages
# sequentially (snapshots are their side effect), then everything else in
# parallel. Usage: scripts/e2e.sh [golden|rest|all] [pytest args...]
set -euo pipefail
mode=${1:-all}; shift || true
root=$(git rev-parse --show-toplevel)
source_id=$(git -C "$root" rev-parse --short HEAD)
if [ -n "$(git -C "$root" status --porcelain)" ]; then source_id="${source_id}-dirty"; fi
image="omc-e2e:${source_id}"
export OMC_E2E_PREBUILT_IMAGE="$image" OMC_E2E_PREBUILT_SOURCE="$source_id" OMC_E2E_SOURCE_ID="$source_id"
if ! docker image inspect "$image" >/dev/null 2>&1 || [[ "$source_id" == *-dirty ]]; then
    DOCKER_BUILDKIT=1 docker build -q -f "$root/docker/Dockerfile.e2e" -t "$image" "$root" >/dev/null
fi
# Snapshots from other sources are stale: prune them.
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^omc-e2e-stage:' | grep -v -- "-${source_id}\$" | xargs -r docker rmi -f >/dev/null 2>&1 || true
run_golden() { uv run pytest -m "e2e and golden and not expensive and not codex_gate" -q -n 0 -p no:cacheprovider tests/e2e/golden "$@"; }
run_rest()   { uv run pytest -m "e2e and not golden and not expensive and not codex_gate" -q -n auto --dist loadgroup -p no:cacheprovider "$@"; }
case "$mode" in
    golden) run_golden "$@" ;;
    rest)   run_rest "$@" ;;
    all)    run_golden; run_rest "$@" ;;
    *) echo "usage: scripts/e2e.sh [golden|rest|all] [pytest args]" >&2; exit 2 ;;
esac
```

`justfile`:

```just
check:
    uv run pytest -m "not e2e and not local_iterm2" -q -n auto

# Dockerized E2E: golden stages first (sequential, snapshotting), then everything else in parallel.
e2e-tests *args:
    bash scripts/e2e.sh all {{args}}

# Only the golden lifecycle path (refreshes stage snapshots).
golden *args:
    bash scripts/e2e.sh golden {{args}}

# Codex integration gate: serial on the account volume; run when a change touches Codex.
codex-gate *args:
    CODEX_AUTH_VOLUME=${CODEX_AUTH_VOLUME:-omc-e2e-codex-auth} bash scripts/e2e.sh rest -m "e2e and codex_gate" -n 1 {{args}}

# The old monolithic lifecycle cases, kept as evidence runs; never a gate.
lifecycle-full *args:
    uv run pytest -m "e2e and expensive" -q tests/e2e/test_e2e_lifecycle_full.py {{args}}
```

(`codex-gate` passes `-m` after the script's own `-m`; pytest uses the last `-m`, so `rest` with `-m "e2e and codex_gate"` selects only the gate. Verify with `--collect-only`.)

- [ ] **Step 6: image bakes plugin setup; script idempotent**

`docker/Dockerfile.e2e`: `RUN bash /repo/docker/setup-plugins.sh` (drop `|| echo …`). In `docker/setup-plugins.sh`, before each `run_step`, skip when done:

```bash
have_marketplace() { "$1" plugin marketplace list --json 2>/dev/null | python3 -c 'import json,sys; sys.exit(0 if any(m.get("name")==sys.argv[1] for m in json.load(sys.stdin)) else 1)' "$2"; }
have_plugin() { "$1" plugin list --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d if isinstance(d,list) else d.get("installed",[]); sys.exit(0 if any(p.get("id")==sys.argv[1] and p.get("enabled",True) and not p.get("errors") for p in d) else 1)' "$2"; }
```

and guard each registration/installation with `have_marketplace claude oh-my-clanker ||`, `have_plugin claude omc@oh-my-clanker ||`, etc. (same for codex with `codex`). The final listing check stays unconditional.

- [ ] **Step 7: verify**

Run: `just check` → expected all pass in well under a minute. Run: `uv run pytest tests/unit/test_e2e_parallel.py tests/unit/test_e2e_codex_auth.py -q`. Run: `bash scripts/e2e.sh rest tests/e2e/test_e2e_smoke.py tests/e2e/test_e2e_marketplace_repair.py -vv` → image built once, 9 tests in parallel, all pass; note the wall time.

- [ ] **Step 8: commit**

```bash
git add -A && git commit -m "test: parallel-safe suites, 300 s ceiling, one image build, Codex xdist group

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Stage snapshots and the variation fixture

**Model:** heavy coding tier

**Files:**
- Create: `tests/e2e/stages.py`
- Modify: `tests/e2e/conversation.py` (`ClaudeConversation.bind`, env passthrough)
- Modify: `tests/e2e/conftest.py` (`stage_session` fixture)
- Test: `tests/unit/test_e2e_stages.py`

**Interfaces:**
- Produces: `stages.source_id() -> str`, `stages.stage_tag(provider, stage) -> str`, `stages.snapshot(container, manifest: dict) -> str` (writes `/tmp/omc-stage.json`, `docker commit`, returns tag), `stages.read_manifest(container) -> dict`, `stages.stage_image(provider, stage) -> str` (fails loud when absent: "run `just golden` first"); `ClaudeConversation.bind(slug, worktree, repo)`; fixture `stage_session` yielding `(container, session, manifest)` for `@pytest.mark.variation("<stage>")` tests.

- [ ] **Step 1: unit tests first**

```python
# tests/unit/test_e2e_stages.py
import json
from types import SimpleNamespace

from tests.e2e import stages


def test_source_id_marks_dirty_trees(monkeypatch):
    monkeypatch.delenv("OMC_E2E_SOURCE_ID", raising=False)
    calls = iter(["abc1234\n", " M file\n"])
    monkeypatch.setattr(stages.subprocess, "check_output", lambda *a, **k: next(calls))
    assert stages.source_id() == "abc1234-dirty"


def test_source_id_prefers_runner_env(monkeypatch):
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbee")
    assert stages.stage_tag("claude", "agreed") == "omc-e2e-stage:claude-agreed-deadbee"


def test_snapshot_writes_manifest_then_commits(monkeypatch):
    seen = []
    monkeypatch.setenv("OMC_E2E_SOURCE_ID", "deadbee")
    monkeypatch.setattr(stages.subprocess, "run", lambda argv, **k: seen.append(argv) or SimpleNamespace(returncode=0, stdout="sha256:x"))
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="cid"))
    tag = stages.snapshot(container, {"provider": "claude", "stage": "agreed", "slug": "s"})
    assert tag == "omc-e2e-stage:claude-agreed-deadbee"
    assert seen[0][:3] == ["docker", "exec", "cid"] and "/tmp/omc-stage.json" in " ".join(seen[0])
    assert seen[1][:3] == ["docker", "commit", "cid"] and seen[1][-1] == tag
    assert json.loads(seen[0][-1])["stage"] == "agreed"
```

- [ ] **Step 2: implement `tests/e2e/stages.py`**

```python
"""Golden-path stage snapshots: commit the container after a stage, fork
variations from the image. Filesystem only — mounts and processes are not
part of a commit, so snapshots are taken between turns and the Codex volume
is remounted per clone."""

from __future__ import annotations

import json
import os
import subprocess

MANIFEST = "/tmp/omc-stage.json"


def source_id() -> str:
    if env := os.environ.get("OMC_E2E_SOURCE_ID"):
        return env
    sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
    return f"{sha}-dirty" if dirty else sha


def stage_tag(provider: str, stage: str) -> str:
    return f"omc-e2e-stage:{provider}-{stage}-{source_id()}"


def snapshot(container, manifest: dict) -> str:
    cid = container.get_wrapped_container().id
    payload = json.dumps(manifest)
    subprocess.run(
        ["docker", "exec", cid, "python3", "-c",
         "import sys; open(sys.argv[1], 'w').write(sys.argv[2])", MANIFEST, payload],
        check=True, capture_output=True, text=True,
    )
    tag = stage_tag(manifest["provider"], manifest["stage"])
    subprocess.run(["docker", "commit", "-q", cid, tag], check=True, capture_output=True, text=True)
    return tag


def read_manifest(container) -> dict:
    cid = container.get_wrapped_container().id
    out = subprocess.run(["docker", "exec", cid, "cat", MANIFEST], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def stage_image(provider: str, stage: str) -> str:
    tag = stage_tag(provider, stage)
    probe = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    if probe.returncode != 0:
        import pytest
        pytest.fail(f"stage snapshot {tag} is absent — run `just golden` first (same source)")
    return tag
```

- [ ] **Step 3: driver binding**

In `ClaudeConversation` add:

```python
    def bind(self, slug: str, worktree: str, repo: str) -> None:
        """Attach to a named session created in a snapshot: later turns resume it."""
        self.slug, self.worktree, self.repo, self.first = slug, worktree, repo, False
```

and make `_launch` forward `OMC_SLUG` when `self.slug` is set (`"-e", f"OMC_SLUG={self.slug}"` after `IS_SANDBOX=1`), so resumed skills see the prepared-path slug exactly as a live `omc start` session does. The Codex `Conversation` gets the same `bind` storing slug/worktree/repo for the variation fixture's use.

- [ ] **Step 4: variation fixture in `tests/e2e/conftest.py`**

```python
@pytest.fixture
def stage_session(request, e2e_provider):
    """A container forked from the golden stage named by @pytest.mark.variation,
    with a conversation bound to its resumable session."""
    from testcontainers.core.container import DockerContainer
    from .conversation import ClaudeConversation, Conversation
    from .stages import read_manifest, stage_image

    marker = request.node.get_closest_marker("variation")
    assert marker and marker.args, "variation tests declare @pytest.mark.variation('<stage>')"
    provider = e2e_provider or "claude"
    image = stage_image(provider, marker.args[0])
    use_codex_account = provider == "codex"
    c = _forward_tokens(DockerContainer(image).with_command("sleep infinity"), use_codex_account=use_codex_account)
    with codex_account(c, _finish_container_setup, use_account=use_codex_account):
        manifest = read_manifest(c)
        session = ClaudeConversation(c, manifest["model"]) if provider == "claude" else Conversation(c)
        with session:
            session.bind(manifest["slug"], manifest["worktree"], manifest["repo"])
            yield c, session, manifest
```

- [ ] **Step 5: verify and commit**

Run: `uv run pytest tests/unit/test_e2e_stages.py -q` → pass. `just check` → pass. Commit: `test: stage snapshots via docker commit and a variation fixture that forks from them`.

---

### Task 3: The golden path (Claude), and the old cases moved out

**Model:** heavy coding tier

**Files:**
- Create: `tests/e2e/lifecycle_helpers.py` (moved from `test_e2e_lifecycle.py`: `_fixture`, `_worktree`, judges, `_assert_*`, `_configure_*`, `_start_*`, `_require_complete_design`, `_direct_implement`, `_assert_successful_implementation`, `_assert_published_fix`)
- Create: `tests/e2e/golden/__init__.py`, `tests/e2e/golden/test_golden_claude.py`
- Create: `tests/e2e/test_e2e_lifecycle_full.py` (the three parametrized cases, `@pytest.mark.expensive`, `@pytest.mark.timeout(3600)`)
- Modify: `tests/e2e/test_e2e_lifecycle.py` (keeps the two Codex integration cases marked `codex_gate` and the two Claude session cases with budgets ≤ 300)
- Modify: `lifecycle_helpers._configure_claude_conversation` defaults → `claude-sonnet-5-5`

**Interfaces:**
- Consumes: `stages.snapshot`, `ClaudeConversation`, helpers above.
- Produces: stage images `claude-start`, `claude-design`, `claude-agreed`, `claude-implemented`, `claude-finished`.

- [ ] **Step 1: move helpers** into `tests/e2e/lifecycle_helpers.py` (pure move; `test_e2e_lifecycle.py` imports them). Run `uv run pytest tests/unit -q -n auto -k conversation` (unit tests import the lifecycle module in places; fix imports).

- [ ] **Step 2: golden stages**

```python
# tests/e2e/golden/test_golden_claude.py
"""The Claude golden path: the real lifecycle once, one or two turns per
stage, snapshotted after each. Sequential by design (scripts/e2e.sh phase 1)."""
import pytest

from ..conversation import ClaudeConversation
from ..lifecycle_helpers import (_assert_discussion_boundary, _assert_successful_implementation,
    _configure_claude_conversation, _direct_implement, _fixture, _judge_claude, _launch_start,
    _record_phase, _require_complete_design, _worktree)
from ..stages import snapshot

pytestmark = [pytest.mark.e2e, pytest.mark.golden, pytest.mark.e2e_provider("claude")]

CONTEXT = ("Please discuss correcting greeting.py: greeting() currently returns "
           "'Goodbye, world!' but must return exactly 'Hello, world!' with its "
           "zero-argument signature. Add an exact-value unittest.")


class Flow:
    def __init__(self, container):
        self.container = container
        self.model, self.judge_model, self.metadata = _configure_claude_conversation(container)
        self.repo = _fixture(container)
        self.session = ClaudeConversation(container, self.model).__enter__()
        self.evidence = {"provider": "claude", "turns": []}
        self.reached = None

    def manifest(self, stage):
        return {"provider": "claude", "stage": stage, "slug": self.session.slug, "repo": self.repo,
                "worktree": self.worktree, "branch": self.branch, "model": self.model,
                "judge_model": self.judge_model}

    def done(self, stage):
        self.reached = stage
        return snapshot(self.container, self.manifest(stage))


@pytest.fixture(scope="module")
def flow(request):
    container = request.getfixturevalue("module_container")
    f = Flow(container)
    yield f
    f.session.close()


def _require(flow, stage):
    assert flow.reached == stage, f"prior stage {stage!r} did not pass (reached {flow.reached!r})"


def test_stage_start(flow):
    initial = flow.session.snapshot(flow.repo)
    _launch_start(flow.session, flow.repo, "claude", CONTEXT)
    first = flow.session.wait_turn(300)
    flow.worktree, flow.branch = _worktree(flow.container, flow.repo)
    flow.baseline, flow.primary_start = _record_phase(flow.session, flow.repo, flow.worktree, flow.evidence, "start", first)
    for f in ("greeting.py", "test_greeting.py"):
        assert flow.baseline["source"][f] == initial["source"][f]
    primer = _judge_claude(flow.container, flow.judge_model, "OMC start received a greeting correction context",
        ["The answer presents project context or a primer about the greeting change.",
         "The answer asks the user for their seed or intended direction.",
         "The answer has not claimed implementation or publication."], first["text"])
    assert primer["passed"], primer
    flow.done("start")


def test_stage_design(flow):
    _require(flow, "start")
    flow.session.send("My seed: change greeting() to return exactly 'Hello, world!'. Keep its "
                      "zero-argument signature and add a unittest for the exact value. "
                      "Please present the complete solution.")
    design = flow.session.wait_turn(300)
    feature, primary = _record_phase(flow.session, flow.repo, flow.worktree, flow.evidence, "seed", design)
    _assert_discussion_boundary(flow.baseline, feature, flow.primary_start, primary, "seed discussion")
    _require_complete_design(flow.container, _judge_claude, flow.judge_model, flow.session, flow.repo,
                             flow.worktree, flow.baseline, flow.evidence, design)
    flow.done("design")


def test_stage_agreed(flow):
    _require(flow, "design")
    flow.session.send("One detail: preserve the exact capitalization and punctuation in "
                      "'Hello, world!'. Please incorporate that into the design.")
    detail = flow.session.wait_turn(300)
    feature, primary = _record_phase(flow.session, flow.repo, flow.worktree, flow.evidence, "detail", detail)
    _assert_discussion_boundary(flow.baseline, feature, flow.primary_start, primary, "design detail")
    flow.session.send("ok")
    agreed = flow.session.wait_turn(300)
    feature, primary = _record_phase(flow.session, flow.repo, flow.worktree, flow.evidence, "ok", agreed)
    _assert_discussion_boundary(flow.baseline, feature, flow.primary_start, primary, "agreement")
    flow.done("agreed")


def test_stage_implemented(flow):
    _require(flow, "agreed")
    flow.session.send(_direct_implement("claude"))
    implemented = flow.session.wait_turn(300)
    _record_phase(flow.session, flow.repo, flow.worktree, flow.evidence, "implement", implemented)
    _assert_successful_implementation(flow.container, "claude", flow.session, flow.repo, flow.worktree,
                                      flow.branch, flow.evidence, flow.baseline)
    flow.done("implemented")
```

`module_container` is a module-scoped twin of `container` in `tests/e2e/conftest.py` (same body, `scope="module"`, provider from the module's `pytestmark`). `_require_complete_design`'s inner `wait_turn(600)` becomes `300`.

- [ ] **Step 3: lifecycle file split.** Move the three parametrized cases and `_scenario_setup`/`_start_discussion` users into `tests/e2e/test_e2e_lifecycle_full.py` with `pytestmark = [pytest.mark.e2e, pytest.mark.expensive]` and `@pytest.mark.timeout(3600)` on each. In `tests/e2e/test_e2e_lifecycle.py` keep `test_codex_conversation_capabilities`, `test_codex_native_skill_mention_is_submitted` (add `@pytest.mark.codex_gate`), `test_claude_conversation_capabilities`, `test_claude_named_session_resume_protocol` (budgets 600 → 300).

- [ ] **Step 4: run the golden path**

Run: `just golden -vv` (builds image, runs 4 stages sequentially). Expected: `start`, `design`, `agreed` pass within 300 s each and leave `omc-e2e-stage:claude-{start,design,agreed}-<source>` images. `implemented` either passes (snapshot `claude-implemented`) or fails on the 300 s ceiling; record the measured duration in the commit message either way. Do not raise the ceiling.

- [ ] **Step 5: commit** `test: golden Claude lifecycle path in snapshotted stages; monolithic cases moved to the expensive tier`.

---

### Task 4: Variations from the `agreed` and `start` snapshots

**Model:** standard coding tier

**Files:**
- Create: `tests/e2e/variations/__init__.py`, `tests/e2e/variations/test_from_start.py`, `tests/e2e/variations/test_from_agreed.py`

**Interfaces:**
- Consumes: `stage_session` fixture, `lifecycle_helpers` asserts and judges.

- [ ] **Step 1: resume smoke from `start`**

```python
# tests/e2e/variations/test_from_start.py
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude")]


@pytest.mark.variation("start")
def test_resume_from_primer_snapshot(stage_session):
    container, session, manifest = stage_session
    session.send("Reply with exactly SNAPSHOT-OK and nothing else.")
    turn = session.wait_turn(120)
    assert "SNAPSHOT-OK" in turn["text"]
    assert turn["session_id"] == manifest["slug"]
```

- [ ] **Step 2: the three scenarios from `agreed`**

```python
# tests/e2e/variations/test_from_agreed.py
import pytest

from ..harness import run_in
from ..lifecycle_helpers import (_assert_critical_wait, _assert_successful_implementation,
    _direct_implement, _judge_claude, _record_phase)

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_provider("claude")]


def _baseline(session, manifest):
    return session.snapshot(manifest["worktree"]), session.snapshot(manifest["repo"])


@pytest.mark.variation("agreed")
def test_implement_publishes(stage_session):
    container, session, m = stage_session
    baseline, _ = _baseline(session, m)
    evidence = {"turns": []}
    session.send(_direct_implement("claude"))
    turn = session.wait_turn(300)
    _record_phase(session, m["repo"], m["worktree"], evidence, "implement", turn)
    _assert_successful_implementation(container, "claude", session, m["repo"], m["worktree"], m["branch"], evidence, baseline)


@pytest.mark.variation("agreed")
def test_critical_question_waits(stage_session):
    container, session, m = stage_session
    baseline, primary = _baseline(session, m)
    session.send(_direct_implement("claude", "A new requirement says the same zero-argument greeting() "
                                  "call must return exactly both 'Hello, world!' and 'Hello there!'."))
    question = session.wait_turn(300)
    _assert_critical_wait(baseline, session.snapshot(m["worktree"]), "critical question")
    _assert_critical_wait(primary, session.snapshot(m["repo"]), "primary critical question")
    verdict = _judge_claude(container, m["judge_model"], "Conflicting exact return values for the same no-argument function call",
        ["The assistant identifies the contradiction as a critical unanswered requirement.",
         "The assistant asks which exact value to implement before proceeding.",
         "The assistant does not request routine approval of an otherwise complete spec or plan."], question["text"])
    assert verdict["passed"], verdict


@pytest.mark.variation("agreed")
def test_failing_build_blocks_publication(stage_session):
    container, session, m = stage_session
    # Inject the failure into the clone: the external build gate goes away.
    rc, _ = run_in(container, ["bash", "-c",
        f"printf '\\ntest ! -e /tmp/omc-external-build-unavailable\\n' >> {m['worktree']}/.omc/stage-scripts/build.sh && "
        f"cp {m['worktree']}/.omc/stage-scripts/build.sh {m['repo']}/.omc/stage-scripts/build.sh && "
        "touch /tmp/omc-external-build-unavailable"])
    assert rc == 0
    baseline, _ = _baseline(session, m)
    session.send(_direct_implement("claude"))
    session.wait_turn(300)
    rc, markers = run_in(container, ["cat", "/tmp/omc-lifecycle-stages"])
    assert rc == 0 and "build" in markers.splitlines(), "failing build never executed"
    after = session.snapshot(m["worktree"])
    assert after["remote_refs"] == baseline["remote_refs"], "failing stage published"
    rc, _ = run_in(container, ["git", "-C", f"{m['repo']}-origin", "rev-parse", "--verify", m["branch"]])
    assert rc != 0, "failing stage published feature branch"
```

(Since the fixture's build script is committed in the golden path's repo, the sabotage edits the clone's worktree copy and primary copy so the modification is not itself a dirty-tree failure; if the agent's finish rebases, the repo copy carries the same change.)

- [ ] **Step 3: run** `bash scripts/e2e.sh rest tests/e2e/variations -vv` (requires Task 3's snapshots). Expected: `test_resume_from_primer_snapshot` passes (proves cross-container resume); the three `agreed` variations run in parallel; record each duration. A variation over 300 s fails on the ceiling and is reported as such.

- [ ] **Step 4: commit** `test: variations fork from golden stage snapshots (resume, implement, critical question, failing build)`.

---

### Task 5: Policy and documentation

**Model:** top tier

**Files:**
- Modify: `.omc/skills/review/SKILL.md`, `.omc/skills/verify/SKILL.md`, `AGENTS.md`, `docker/PLUGIN-NOTES.md`, `README.md`
- Test: `tests/unit/test_plugin_manifests.py` (dogfood needles)

- [ ] **Step 1: tests first** — add to `tests/unit/test_plugin_manifests.py`:

```python
def test_dogfood_review_stage_rejects_slow_and_serial_tests():
    text = (ROOT / ".omc" / "skills" / "review" / "SKILL.md").read_text()
    for needle in ("No slow tests", "5 minutes", "No serial-only tests", "-n auto", "xdist_group", "Important"):
        assert needle in text, f"review stage missing {needle!r}"


def test_dogfood_verify_stage_gates_on_parallel_e2e_only():
    text = (ROOT / ".omc" / "skills" / "verify" / "SKILL.md").read_text()
    assert "just e2e-tests" in text and "just codex-gate" in text
    assert "never a gate" in text and "lifecycle-tests" not in text
```

- [ ] **Step 2: review stage** — append to the load-bearing rules in `.omc/skills/review/SKILL.md`:

```markdown
- **No slow tests.** A test whose runtime can exceed 5 minutes (its turn
  budgets, loops, or `timeout` marker allow it) is an Important finding.
  The only exception is the `expensive` tier, which no stage gates on.
- **No serial-only tests.** A test that cannot run under `-n auto` (module
  or session state shared across tests, locks that fail instead of wait,
  guards on `PYTEST_XDIST_WORKER`, fixed host ports or paths) is an
  Important finding. Serialization is expressed only through `xdist_group`.
```

- [ ] **Step 3: verify stage** — replace `.omc/skills/verify/SKILL.md` body:

```markdown
Run the parallel E2E suite:

```sh
just e2e-tests
```

It builds the image once, runs the golden lifecycle stages sequentially
(snapshotting each), then everything else with `-n auto`. Exit 0 passes.

When the change touches the Codex provider (`src/omc/providers/codex.py`),
the Codex conversation driver or plugin payload (`tests/e2e/conversation.py`,
`tests/e2e/codex_plugin_payload.py`, `docker/setup-plugins.sh` codex half,
`docker/conversation.py`), also run the Codex gate, serially on the account
volume (needs a prior `just codex-login`):

```sh
just codex-gate
```

The old monolithic lifecycle run (`just lifecycle-full`, `expensive`) is
evidence-only and never a gate. Include failing output in the stage summary.
```

- [ ] **Step 4: AGENTS.md** — under Model selection add: "Test judges that check a short rubric over a one-line fixture are not omc's own review or judging work: `tests/e2e` actors and judges default to the standard coding tier."

- [ ] **Step 5: PLUGIN-NOTES.md** — top entry "Parallel E2E and stage snapshots (2026-10-01)": why the matrix took an hour (preamble replayed six times, 30-minute implement budgets, top-tier models, Codex lock, per-worker image builds, host GPG config breaking the unit suite under xdist), what replaced it, measured durations from Tasks 1, 3 and 4.

- [ ] **Step 6: README** — in the development section, replace the E2E paragraph with: `just check` (parallel unit tests), `just e2e-tests` (golden path then parallel variations), `just golden`, `just codex-gate`, `just lifecycle-full`.

- [ ] **Step 7:** `just check`, commit `docs: review rejects slow or serial-only tests; verify gates on the parallel E2E suite`.

## Self-review

Spec 3.1 → Tasks 2, 3, 4. 3.2 → Task 3 (defaults). 3.3 → Tasks 1, 2. 3.4 → Task 1. 3.5 → Task 5. 3.6 → Task 1 Step 6. Decision 7 (Codex opt-in) → Task 1 recipes, Task 3 markers. Review Focus 1–4 pinned; 5 is reported, not pinned. Names consistent: `stage_session`, `snapshot`, `stage_image`, `read_manifest`, `bind`, `item_provider`, `wait_for_lock`, `CODEX_GROUP`.
