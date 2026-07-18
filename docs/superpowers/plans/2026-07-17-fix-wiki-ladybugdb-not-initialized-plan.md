# Fix wiki "LadybugDB not initialized" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wiki generation survives >5-minute buffered LLM calls (GitNexus keepalive), stops rewriting `~/.gitnexus/config.json` on unchanged flags, and `omc update` learns to refresh the managed GitNexus dependency.

**Architecture:** Two repos. GitNexus (`/Users/chriphus/Projects/GitNexus`): a run-scoped `setInterval` keepalive in `WikiGenerator.run()` replaces the streaming-dependent `onChunk` touch, and CLI flag persistence becomes save-on-effective-change — both wiki-local, zero pool-adapter changes. omc (this worktree): a deterministic `update_gitnexus()` in `src/omc/gitnexus.py`, wired into the existing `run_update`.

**Tech Stack:** TypeScript + vitest (GitNexus, `gitnexus/` package); Python + pytest (omc).

**Spec:** `docs/superpowers/specs/2026-07-17-fix-wiki-ladybugdb-not-initialized-design.md`

## Global Constraints

- GitNexus work happens in `/Users/chriphus/Projects/GitNexus`, branch `fix/wiki-lbug-keepalive` off `origin/main`; when green it is merged to `main` and pushed directly (user override — no PR).
- omc work happens in this worktree (`feature/fix-wiki-ladybugdb-not-initialized`).
- Do NOT modify `gitnexus/src/core/lbug/pool-adapter.ts` — its eviction semantics are shared with the MCP server.
- The only approved GitNexus origin is `https://github.com/chris-husse/GitNexus.git`; refuse to update a clone with any other origin. Never echo credentials embedded in URLs — redact userinfo before printing.
- Managed-clone updates always force `main` (`checkout main` + `merge --ff-only origin/main`); the clone is not a dev workspace.
- Build order for GitNexus is fixed: `npm install --no-audit --no-fund` in `gitnexus-shared/` FIRST, then `npm ci && npm run build` in `gitnexus/`.
- Never claim success on a broken build: after building, `node <CLI> --version` must succeed.
- GitNexus gates before push (from `TESTING.md`): `cd gitnexus && npx tsc --noEmit && npm test`.
- omc messages go to stderr via `print(..., file=sys.stderr)`; helpers return `int` exit codes (no exceptions across command boundaries — matches `installer.py`/`watch.py` style).

---

### Task 1: GitNexus — branch off origin/main

**Files:** none (git only)

**Interfaces:**
- Produces: branch `fix/wiki-lbug-keepalive` at `origin/main`, clean tree, for Tasks 2–4.

- [ ] **Step 1: Create the branch**

```bash
cd /Users/chriphus/Projects/GitNexus
git fetch origin --prune
git status --porcelain   # must be empty; STOP and report if not
git checkout -b fix/wiki-lbug-keepalive origin/main
```

Expected: new branch at the `origin/main` commit (contains the round-3 security merge `597195bc`).

---

### Task 2: GitNexus — run-scoped wiki keepalive

**Files:**
- Modify: `gitnexus/src/core/wiki/generator.ts` (the `run()` method ~line 254-322, and `streamOpts` ~line 161-190)
- Test: `gitnexus/test/unit/wiki-keepalive.test.ts` (new)

**Interfaces:**
- Consumes: `touchWikiDb`, `initWikiDb`, `closeWikiDb` from `../../src/core/wiki/graph-queries.js` (already imported by generator.ts).
- Produces: `WikiGenerator.run()` touches the `__wiki__` pool entry every 60s from init to completion, success or throw. No signature changes.

- [ ] **Step 1: Write the failing test**

Create `gitnexus/test/unit/wiki-keepalive.test.ts`. It follows the exact mock pattern of `test/unit/wiki-grouping-batch.test.ts` (vi.doMock of graph-queries + spied `callLLM` + `reviewOnly: true` so `run()` stops after the grouping call):

```ts
/**
 * Wiki keepalive: the generator must touch the __wiki__ pool entry every 60s
 * for the WHOLE run. Local agent CLI providers (claude/codex/opencode) buffer
 * stdout until process exit, so the old onChunk-based touch never fired during
 * long LLM calls; the pool's 5-minute idle sweep evicted __wiki__ mid-run and
 * the next graph query threw 'LadybugDB not initialized for repo "__wiki__"'.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import os from 'os';
import path from 'path';
import fs from 'fs/promises';

const SLOW_LLM_MS = 6 * 60_000; // longer than the pool's 5-minute idle timeout

describe('WikiGenerator keepalive', () => {
  let tmpDir: string;

  beforeEach(async () => {
    vi.resetModules();
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'wiki-keepalive-test-'));
  });

  afterEach(async () => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  async function makeGenerator(graphOverrides: Record<string, unknown> = {}) {
    const touchWikiDb = vi.fn();
    vi.doMock('../../src/core/wiki/graph-queries.js', () => ({
      initWikiDb: vi.fn().mockResolvedValue(undefined),
      closeWikiDb: vi.fn().mockResolvedValue(undefined),
      touchWikiDb,
      getFilesWithExports: vi
        .fn()
        .mockResolvedValue([
          { filePath: 'src/a.ts', symbols: [{ name: 'a', type: 'function' }] },
        ]),
      getAllFiles: vi.fn().mockResolvedValue(['src/a.ts']),
      getIntraModuleCallEdges: vi.fn().mockResolvedValue([]),
      getInterModuleCallEdges: vi.fn().mockResolvedValue({ incoming: [], outgoing: [] }),
      getProcessesForFiles: vi.fn().mockResolvedValue([]),
      getAllProcesses: vi.fn().mockResolvedValue([]),
      getInterModuleEdgesForOverview: vi.fn().mockResolvedValue([]),
      ...graphOverrides,
    }));
    vi.doMock('child_process', () => ({
      execSync: vi.fn().mockImplementation(() => {
        throw new Error('not a git repo');
      }),
      execFileSync: vi.fn(),
    }));

    const llmClient = await import('../../src/core/wiki/llm-client.js');
    // Buffered provider simulation: nothing streams; the answer lands after 6 min.
    vi.spyOn(llmClient, 'callLLM').mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () => resolve({ content: JSON.stringify({ All: ['src/a.ts'] }) }),
            SLOW_LLM_MS,
          ),
        ),
    );

    const { WikiGenerator } = await import('../../src/core/wiki/generator.js');
    const storagePath = path.join(tmpDir, 'storage');
    await fs.mkdir(path.join(storagePath, 'wiki'), { recursive: true });
    const repoPath = path.join(tmpDir, 'repo');
    await fs.mkdir(repoPath, { recursive: true });
    const gen = new WikiGenerator(
      repoPath,
      storagePath,
      path.join(storagePath, 'lbug'),
      {
        apiKey: 'key',
        baseUrl: 'http://localhost',
        model: 'test',
        maxTokens: 1000,
        temperature: 0,
        provider: 'openai',
      },
      { reviewOnly: true },
    );
    return { gen, touchWikiDb };
  }

  it('touches the wiki DB every 60s while a buffered LLM call is in flight', async () => {
    vi.useFakeTimers();
    const { gen, touchWikiDb } = await makeGenerator();

    const run = gen.run();
    await vi.advanceTimersByTimeAsync(SLOW_LLM_MS);
    await run;

    // 6 minutes of silent LLM call → at least 5 keepalive touches
    expect(touchWikiDb.mock.calls.length).toBeGreaterThanOrEqual(5);

    // After run() settles the interval is cleared — no further touches
    const settled = touchWikiDb.mock.calls.length;
    await vi.advanceTimersByTimeAsync(SLOW_LLM_MS);
    expect(touchWikiDb.mock.calls.length).toBe(settled);
  });

  it('clears the keepalive when the run throws', async () => {
    vi.useFakeTimers();
    const { gen, touchWikiDb } = await makeGenerator({
      getFilesWithExports: vi.fn().mockRejectedValue(new Error('boom')),
      getAllFiles: vi.fn().mockRejectedValue(new Error('boom')),
    });

    await expect(gen.run()).rejects.toThrow('boom');

    const settled = touchWikiDb.mock.calls.length;
    await vi.advanceTimersByTimeAsync(SLOW_LLM_MS);
    expect(touchWikiDb.mock.calls.length).toBe(settled);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/chriphus/Projects/GitNexus/gitnexus
npx vitest run test/unit/wiki-keepalive.test.ts
```

Expected: FAIL — first test gets far fewer than 5 touches (the current `onChunk` touch fires at most on the single end-of-call chunk, and only after 60s of "streaming", so effectively 0).

- [ ] **Step 3: Implement the keepalive in `run()`**

In `gitnexus/src/core/wiki/generator.ts`, `run()` currently reads:

```ts
    // Init graph
    this.onProgress('init', 2, 'Connecting to knowledge graph...');
    await initWikiDb(this.lbugPath);

    let result: WikiRunResult;
    try {
```

and ends the block with:

```ts
    } finally {
      await closeWikiDb();
    }
```

Change to:

```ts
    // Init graph
    this.onProgress('init', 2, 'Connecting to knowledge graph...');
    await initWikiDb(this.lbugPath);

    // Keepalive: touch the __wiki__ pool entry every 60s for the whole run.
    // Local agent CLI providers (claude/codex/opencode) buffer stdout until
    // process exit, so no streaming callback can be relied on to reset the
    // pool's 5-minute idle timeout during long LLM calls — without this the
    // idle sweeper evicts __wiki__ mid-run and the next graph query throws
    // 'LadybugDB not initialized for repo "__wiki__"'.
    const keepalive = setInterval(() => touchWikiDb(), 60_000);
    keepalive.unref?.();

    let result: WikiRunResult;
    try {
```

```ts
    } finally {
      clearInterval(keepalive);
      await closeWikiDb();
    }
```

- [ ] **Step 4: Remove the redundant `onChunk` touch from `streamOpts`**

In the same file, in `streamOpts` (~line 161): delete `let lastTouch = Date.now();` and the block

```ts
        // Touch DB every 60s to prevent idle timeout during long LLM calls
        const now = Date.now();
        if (now - lastTouch > 60_000) {
          touchWikiDb();
          lastTouch = now;
        }
```

and remove the sentence "Also touches the DB connection periodically to prevent idle timeout." from the `streamOpts` doc comment. `touchWikiDb` stays imported (the keepalive uses it).

- [ ] **Step 5: Run the test to verify it passes**

```bash
npx vitest run test/unit/wiki-keepalive.test.ts
```

Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/chriphus/Projects/GitNexus
git add gitnexus/src/core/wiki/generator.ts gitnexus/test/unit/wiki-keepalive.test.ts
git commit -m "fix(wiki): keep __wiki__ pool entry alive for the whole generator run

Buffered local-CLI providers (claude/codex/opencode) emit stdout only at
process exit, so the onChunk-based touch never fired mid-call and the pool's
5-minute idle sweep evicted __wiki__ during long LLM calls, failing the next
graph query with 'LadybugDB not initialized for repo __wiki__'."
```

---

### Task 3: GitNexus — save CLI config only on effective change

**Files:**
- Modify: `gitnexus/src/cli/wiki.ts` (flag-persistence block ~line 198-225; helpers `isLocalProvider`/`localModelConfigKey` at ~line 59-70 stay where they are)
- Test: `gitnexus/test/unit/wiki-config-save.test.ts` (new)

**Interfaces:**
- Consumes: `CLIConfig` (via `loadCLIConfig` return type), `isLocalProvider`, `localModelConfigKey`, `WikiCommandOptions` — all already in `wiki.ts`.
- Produces: exported pure function `applyCliConfigOverrides(options: WikiCommandOptions, existing: CLIConfig): { merged: CLIConfig; changed: boolean }` in `gitnexus/src/cli/wiki.ts` (add `import type { CLIConfig } from '../storage/repo-manager.js'` to the existing repo-manager import).

- [ ] **Step 1: Write the failing test**

Create `gitnexus/test/unit/wiki-config-save.test.ts`:

```ts
/**
 * CLI flag persistence must be save-on-effective-change: omc watch passes
 * --provider/--model on every 30s tick, and unconditional saves rewrote
 * ~/.gitnexus/config.json (plus printed "Config saved") on every run.
 */
import { describe, it, expect } from 'vitest';
import { applyCliConfigOverrides } from '../../src/cli/wiki.js';

describe('applyCliConfigOverrides', () => {
  it('reports no change when flags match the saved config (local provider model routing)', () => {
    const existing = { provider: 'claude' as const, claudeModel: 'opus' };
    const { merged, changed } = applyCliConfigOverrides(
      { provider: 'claude', model: 'opus' },
      existing,
    );
    expect(changed).toBe(false);
    expect(merged).toEqual(existing);
  });

  it('reports a change when the model differs', () => {
    const { merged, changed } = applyCliConfigOverrides(
      { provider: 'claude', model: 'sonnet' },
      { provider: 'claude', claudeModel: 'opus' },
    );
    expect(changed).toBe(true);
    expect(merged.claudeModel).toBe('sonnet');
  });

  it('routes non-local provider models to the flat model key', () => {
    const { merged, changed } = applyCliConfigOverrides(
      { provider: 'openai', model: 'gpt-4o' },
      { provider: 'openai', model: 'gpt-4o-mini' },
    );
    expect(changed).toBe(true);
    expect(merged.model).toBe('gpt-4o');
  });

  it('treats reasoningModel=false as an explicit override', () => {
    const { changed } = applyCliConfigOverrides(
      { reasoningModel: false },
      { isReasoningModel: true },
    );
    expect(changed).toBe(true);
  });

  it('reports no change when identical flags repeat (watch-loop case)', () => {
    const existing = {
      provider: 'claude' as const,
      claudeModel: 'opus',
      isReasoningModel: false,
    };
    const { changed } = applyCliConfigOverrides(
      { provider: 'claude', model: 'opus', reasoningModel: false },
      existing,
    );
    expect(changed).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/chriphus/Projects/GitNexus/gitnexus
npx vitest run test/unit/wiki-config-save.test.ts
```

Expected: FAIL — `applyCliConfigOverrides` is not exported.

- [ ] **Step 3: Extract the helper and rewire the persistence block**

In `gitnexus/src/cli/wiki.ts`, add `type CLIConfig` to the repo-manager import, then add above `wikiCommand` (near `isLocalProvider`/`localModelConfigKey`):

```ts
/**
 * Merge CLI flag overrides into the saved config. Callers must skip
 * saveCLIConfig when `changed` is false: repeated invocations with identical
 * flags (omc watch passes --provider/--model every tick) must not rewrite
 * ~/.gitnexus/config.json on every run.
 */
export function applyCliConfigOverrides(
  options: WikiCommandOptions,
  existing: CLIConfig,
): { merged: CLIConfig; changed: boolean } {
  const updates: Partial<CLIConfig> = {};
  if (options.apiKey) updates.apiKey = options.apiKey;
  if (options.baseUrl) updates.baseUrl = options.baseUrl;
  if (options.provider) updates.provider = options.provider as CLIConfig['provider'];
  if (options.apiVersion) updates.apiVersion = options.apiVersion;
  if (options.reasoningModel !== undefined) updates.isReasoningModel = options.reasoningModel;
  // Save model to appropriate field based on provider.
  if (options.model) {
    const targetProvider = options.provider ?? existing.provider;
    if (isLocalProvider(targetProvider)) {
      updates[localModelConfigKey(targetProvider)] = options.model;
    } else {
      updates.model = options.model;
    }
  }
  const changed = (Object.keys(updates) as Array<keyof CLIConfig>).some(
    (key) => existing[key] !== updates[key],
  );
  return { merged: { ...existing, ...updates }, changed };
}
```

(The `updates` construction is moved verbatim from `wikiCommandImpl`; adjust the `options.provider` cast to whatever the existing block compiles with — the current code assigns it directly.)

Replace the body of the persistence `if` block in `wikiCommandImpl` (keep its surrounding condition unchanged):

```ts
    const existing = await loadCLIConfig();
    const { merged, changed } = applyCliConfigOverrides(options!, existing);
    if (changed) {
      await saveCLIConfig(merged);
      console.log('  Config saved to ~/.gitnexus/config.json\n');
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npx vitest run test/unit/wiki-config-save.test.ts test/unit/wiki-flags.test.ts
```

Expected: PASS (new tests green; existing wiki-flags suite unaffected).

- [ ] **Step 5: Commit**

```bash
cd /Users/chriphus/Projects/GitNexus
git add gitnexus/src/cli/wiki.ts gitnexus/test/unit/wiki-config-save.test.ts
git commit -m "fix(wiki): persist CLI flag overrides only when they change the saved config

A watch loop passing identical --provider/--model flags every tick was
rewriting ~/.gitnexus/config.json (and printing 'Config saved') every run."
```

---

### Task 4: GitNexus — gates, version bump, push to main

**Files:**
- Modify: `gitnexus/package.json` (version bump only)

**Interfaces:**
- Consumes: Tasks 2–3 commits on `fix/wiki-lbug-keepalive`.
- Produces: `origin/main` containing the fix; a new `--version` so `omc update` reporting shows a visible old → new change.

- [ ] **Step 1: Bump the patch version**

```bash
cd /Users/chriphus/Projects/GitNexus/gitnexus
npm version patch --no-git-tag-version   # 1.6.7 → 1.6.8 (or current+1)
cd .. && git add gitnexus/package.json gitnexus/package-lock.json
git commit -m "chore: bump gitnexus to v1.6.8"
```

- [ ] **Step 2: Run the full gates**

```bash
cd /Users/chriphus/Projects/GitNexus/gitnexus
npx tsc --noEmit && npm test
```

Expected: both green. Any failure: fix before proceeding; do NOT push red.

- [ ] **Step 3: Merge to main and push (user-approved direct push)**

```bash
cd /Users/chriphus/Projects/GitNexus
git checkout main
git merge --ff-only fix/wiki-lbug-keepalive
git push origin main
```

Expected: fast-forward, push accepted.

---

### Task 5: omc — `update_gitnexus()` (TDD)

**Files:**
- Modify: `src/omc/gitnexus.py`
- Test: `tests/unit/test_gitnexus_update.py` (new)

**Interfaces:**
- Consumes: `ToolContext` (`ctx.run`, `ctx.home`, `ctx.git_bin`), existing `gitnexus_cli(ctx)`.
- Produces: `GITNEXUS_ORIGIN: str` constant and `update_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int` in `src/omc/gitnexus.py`. Task 6 imports `update_gitnexus`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_gitnexus_update.py` (conventions mirror `tests/unit/test_watch.py`: real git against a local bare origin, recording stubs on an isolated-ish PATH):

```python
import os
import stat
import subprocess

from omc.gitnexus import update_gitnexus
from omc.toolctx import ToolContext


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_ctx(tmp_path, home, *, npm_rc=0):
    """Real git on PATH + recording npm/node stubs."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    calls = bindir / "tool.calls"
    for name, out, rc in (("npm", "ok", npm_rc), ("node", "9.9.9", 0)):
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $@ [cwd=$PWD]" >> "{calls}"\necho "{out}"\nexit {rc}\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {
        "HOME": str(tmp_path),
        "OMC_HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    return ToolContext.from_env(env), calls


def _seed_clone(tmp_path, home):
    """Local bare 'approved origin' + managed clone at home/dependencies/gitnexus."""
    origin = tmp_path / "gitnexus-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    (seed / "gitnexus-shared").mkdir()
    (seed / "gitnexus-shared" / "package.json").write_text("{}")
    (seed / "gitnexus").mkdir()
    (seed / "gitnexus" / "package.json").write_text("{}")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c1", cwd=seed)
    _git("branch", "-M", "main", cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)
    dest = home / "dependencies" / "gitnexus"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True)
    cli = dest / "gitnexus" / "dist" / "cli" / "index.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// built")
    return origin, seed, dest


def _advance_origin(seed):
    (seed / "new.txt").write_text("new\n")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "c2", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)


def test_skips_when_not_installed(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home)
    assert update_gitnexus(ctx) == 0
    assert "/omc:index" in capsys.readouterr().err


def test_refuses_wrong_origin(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    _seed_clone(tmp_path, home)
    # approved origin deliberately differs from the clone's actual origin
    assert update_gitnexus(ctx, approved_origin="https://example.com/other.git") == 1
    assert "refusing" in capsys.readouterr().err
    assert not calls.exists()  # never built


def test_up_to_date_short_circuits(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, _, _ = _seed_clone(tmp_path, home)
    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    err = capsys.readouterr().err
    assert "up to date" in err
    recorded = calls.read_text() if calls.exists() else ""
    assert "npm" not in recorded  # no build on the short-circuit path


def test_moved_pulls_builds_and_verifies(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, calls = _make_ctx(tmp_path, home)
    origin, seed, dest = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    assert update_gitnexus(ctx, approved_origin=str(origin)) == 0
    # clone fast-forwarded to origin/main
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "origin/main"], capture_output=True, text=True
    ).stdout.strip()
    assert head == remote
    recorded = calls.read_text()
    lines = [l for l in recorded.splitlines() if l.startswith("npm")]
    assert "install" in lines[0] and "gitnexus-shared" in lines[0]
    assert lines[1].startswith("npm ci") and "gitnexus-shared" not in lines[1]
    assert "run build" in lines[2]
    assert "9.9.9" in capsys.readouterr().err  # new version reported


def test_build_failure_is_nonzero(tmp_path, capsys):
    home = tmp_path / "home"
    ctx, _ = _make_ctx(tmp_path, home, npm_rc=1)
    origin, seed, _ = _seed_clone(tmp_path, home)
    _advance_origin(seed)
    assert update_gitnexus(ctx, approved_origin=str(origin)) == 1
    assert "failed" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_gitnexus_update.py -v
```

Expected: FAIL — `ImportError: cannot import name 'update_gitnexus'`.

- [ ] **Step 3: Implement `update_gitnexus` in `src/omc/gitnexus.py`**

Append (new imports at top: `import re`, `import sys`; extend the module docstring's charter sentence to "…and updates an existing managed clone (`omc update`); first install stays in skill prose"):

```python
# The ONLY source ever updated — mirrors the gitnexus-ensure skill's rule.
GITNEXUS_ORIGIN = "https://github.com/chris-husse/GitNexus.git"


def gitnexus_root(ctx: ToolContext) -> Path:
    return ctx.home / "dependencies" / "gitnexus"


def _redact_userinfo(url: str) -> str:
    # Never echo credentials embedded in a remote URL.
    return re.sub(r"//[^/@]*@", "//[REDACTED]@", url)


def _cli_version(ctx: ToolContext) -> str | None:
    cli = gitnexus_cli(ctx)
    if not cli.is_file():
        return None
    cp = ctx.run(["node", str(cli), "--version"])
    return (cp.stdout or "").strip() or None if cp.returncode == 0 else None


def update_gitnexus(ctx: ToolContext, *, approved_origin: str = GITNEXUS_ORIGIN) -> int:
    """Deterministic update of the managed GitNexus clone (`omc update`).

    Forces main — the clone is not a dev workspace. First install stays in
    the gitnexus-ensure skill; a missing clone is a skip, not an error.
    """
    root = gitnexus_root(ctx)
    git = ctx.git_bin
    if not (root / ".git").exists():
        print(
            "GitNexus not installed — /omc:index installs it on first use; skipping.",
            file=sys.stderr,
        )
        return 0
    cp = ctx.run([git, "-C", str(root), "remote", "get-url", "origin"])
    origin = (cp.stdout or "").strip()
    if cp.returncode != 0 or origin != approved_origin:
        shown = _redact_userinfo(origin) or "<unknown>"
        print(
            f"error: {root} origin is {shown!r}, not the approved GitNexus source — "
            "refusing to update",
            file=sys.stderr,
        )
        return 1
    old = _cli_version(ctx)
    cp = ctx.run([git, "-C", str(root), "fetch", "origin", "--prune"])
    if cp.returncode != 0:
        print(f"error: GitNexus fetch failed: {(cp.stderr or '').strip()[:400]}", file=sys.stderr)
        return 1
    head = ctx.run([git, "-C", str(root), "rev-parse", "HEAD"])
    remote = ctx.run([git, "-C", str(root), "rev-parse", "origin/main"])
    if (
        head.returncode == 0
        and remote.returncode == 0
        and head.stdout.strip() == remote.stdout.strip()
    ):
        print(f"✓ GitNexus up to date{f' ({old})' if old else ''}", file=sys.stderr)
        return 0
    print("→ updating GitNexus…", file=sys.stderr)
    for argv in (
        [git, "-C", str(root), "checkout", "main"],
        [git, "-C", str(root), "merge", "--ff-only", "origin/main"],
    ):
        cp = ctx.run(argv)
        if cp.returncode != 0:
            print(
                f"error: GitNexus {' '.join(argv[3:])} failed: "
                f"{(cp.stderr or '').strip()[:400]}",
                file=sys.stderr,
            )
            return 1
    # Two-step build; order matters (gitnexus-shared is a plain sibling package
    # compiled by the main build with its own node_modules).
    for argv, cwd in (
        (["npm", "install", "--no-audit", "--no-fund"], root / "gitnexus-shared"),
        (["npm", "ci"], root / "gitnexus"),
        (["npm", "run", "build"], root / "gitnexus"),
    ):
        cp = ctx.run(argv, cwd=str(cwd))
        if cp.returncode != 0:
            print(
                f"error: {' '.join(argv)} in {cwd.name}/ failed:\n"
                f"{(cp.stderr or cp.stdout or '').strip()[:800]}",
                file=sys.stderr,
            )
            return 1
    new = _cli_version(ctx)
    if new is None:
        print(
            "error: GitNexus built but the CLI won't report --version — not claiming success",
            file=sys.stderr,
        )
        return 1
    print(
        f"✓ GitNexus updated{f': {old} → {new}' if old and old != new else f' ({new})'}",
        file=sys.stderr,
    )
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_gitnexus_update.py -v
```

Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add src/omc/gitnexus.py tests/unit/test_gitnexus_update.py
git commit -m "feat: update_gitnexus - deterministic managed-clone refresh (forces main)"
```

---

### Task 6: omc — wire into `omc update`, help text, ensure-skill note

**Files:**
- Modify: `src/omc/installer.py:54-56` (`run_update`)
- Modify: `src/omc/cli.py` (the `sub.add_parser("update", ...)` line)
- Modify: `skills/gitnexus-ensure/SKILL.md` (Step 1)
- Test: `tests/unit/test_installer.py` (extend)

**Interfaces:**
- Consumes: `update_gitnexus` from Task 5.
- Produces: `omc update` = uv self-upgrade + dependency refresh; non-zero if either fails.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_installer.py` (match its existing style for faking `_uv`; monkeypatch both steps to isolate the combination logic):

```python
def test_run_update_combines_uv_and_dependency_refresh(monkeypatch, tmp_path):
    from omc import installer
    from omc.toolctx import ToolContext

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "home")})
    seen = []
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: seen.append(("uv", a)) or 0)
    monkeypatch.setattr(
        "omc.gitnexus.update_gitnexus", lambda ctx: seen.append(("dep",)) or 0
    )
    assert installer.run_update(ctx) == 0
    assert ("uv", ("tool", "upgrade", "omc")) in seen and ("dep",) in seen


def test_run_update_fails_if_dependency_refresh_fails(monkeypatch, tmp_path):
    from omc import installer
    from omc.toolctx import ToolContext

    ctx = ToolContext.from_env({"HOME": str(tmp_path), "OMC_HOME": str(tmp_path / "home")})
    monkeypatch.setattr(installer, "_uv", lambda ctx, *a: 0)
    monkeypatch.setattr("omc.gitnexus.update_gitnexus", lambda ctx: 1)
    assert installer.run_update(ctx) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_installer.py -v -k run_update
```

Expected: FAIL — `run_update` never calls the dependency refresh (`("dep",)` missing).

- [ ] **Step 3: Implement**

In `src/omc/installer.py` replace `run_update`:

```python
def run_update(ctx: ToolContext) -> int:
    print("Updating omc via uv…", file=sys.stderr)
    rc = _uv(ctx, "tool", "upgrade", "omc")
    from . import gitnexus

    dep_rc = gitnexus.update_gitnexus(ctx)
    return 1 if (rc != 0 or dep_rc != 0) else 0
```

(Import via module attribute so the tests' `monkeypatch.setattr("omc.gitnexus.update_gitnexus", ...)` takes effect.)

In `src/omc/cli.py` change the update parser line to:

```python
    sub.add_parser("update", help="Update omc + managed dependencies (GitNexus)")
```

In `skills/gitnexus-ensure/SKILL.md`, at the end of "## Step 1 — healthy already?" add:

```markdown
(Updating an already-healthy install is `omc update`'s job — deterministic,
forces `main`. This skill only installs/repairs.)
```

- [ ] **Step 4: Run the full omc unit suite**

```bash
uv run pytest tests/unit -q
```

Expected: PASS (including any help-text assertions in `test_cli.py` — if one asserts the old update help string, update it to the new string in the same commit).

- [ ] **Step 5: Commit**

```bash
git add src/omc/installer.py src/omc/cli.py skills/gitnexus-ensure/SKILL.md tests/unit/test_installer.py
git commit -m "feat: omc update refreshes the managed GitNexus dependency"
```

---

### Task 7: Live proof (real repos, real Claude)

**Files:** none (verification only). Requires Task 4 pushed and Tasks 5–6 committed.

- [ ] **Step 1: Refresh the worktree venv, then update the real managed clone**

Worktree venvs copied from the primary run PRIMARY code until resynced:

```bash
cd /Users/chriphus/OpenSource-Projects/oh-my-clanker.feature-fix-wiki-ladybugdb-not-initialized
uv sync --reinstall
uv run omc update
```

Expected: "Updating omc via uv…" (may no-op), then GitNexus fetch → ff → two npm builds → `✓ GitNexus updated: 1.6.7 → 1.6.8`.

- [ ] **Step 2: Reproduce the original failing command**

```bash
cd /Users/chriphus/Projects/hummingbird-wt
uv run --project /Users/chriphus/OpenSource-Projects/oh-my-clanker.feature-fix-wiki-ladybugdb-not-initialized omc watch --once --enable-documentation
```

Expected (LLM-heavy, can take tens of minutes): `✓ index refreshed`, then `→ regenerating documentation via claude (LLM-heavy)` completing WITHOUT the LadybugDB error, ending with `✓ documentation refreshed → .omc/docs/gitnexus/docs`. Also confirm the run does NOT print "Config saved to ~/.gitnexus/config.json" (flags unchanged from the previous run).

- [ ] **Step 3: Report results** — paste the tail of the output into the session; if the wiki step fails, STOP and debug before finishing the branch.

---

### Task 8: Ship the omc branch

Handled by the conductor: `/omc:finish` (rebase → squash → build/verify/review stages → push). Not a subagent task.

## Self-Review Notes

- Spec coverage: keepalive → Task 2; config-save → Task 3; direct push → Task 4; `omc update` refresh → Tasks 5–6; ensure-skill note + help text → Task 6; live proof → Task 7; plan-skill presentation rule was already committed during brainstorming (ec1c133).
- Types consistent: `update_gitnexus(ctx, *, approved_origin)` used identically in Tasks 5–6; `applyCliConfigOverrides(options, existing) → {merged, changed}` defined and consumed only in Task 3.
- Known judgment points for implementers: exact line numbers may drift a few lines; anchor on the quoted code, not the numbers. The `options.provider` cast in Task 3 should match whatever the existing block compiles with.
- Post-review note: Task 5 was implemented with an added `_run_tool` optional-tool wrapper (npm/node missing-binary → clean stderr + rc 1, not a traceback) that this plan's inline code lacked — review-driven, spec-consistent.
