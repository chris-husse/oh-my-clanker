# Fix the false documentation stall — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** omc never kills a healthy `gitnexus wiki` child again, a hung LLM call still fails in bounded time, and the user sees grouping progress — in both the GitNexus fork and omc.

**Architecture:** GitNexus's wiki command prints one `GITNEXUS_PROGRESS {…}` JSON line on stderr per progress change when stderr is not a TTY, and heartbeats every 30 s while an LLM call is in flight. omc's `run_supervised` gains an `on_line` callback that feeds a small `GitNexusProgress` holder; both wiki call sites report from it (dependency docs via `OMC_PROGRESS`, project wiki via a TTY bar or `·` narration) and pass `--timeout 300` so GitNexus bounds hung calls itself. One constant in `wikirun.py` is both the stall window and the timeout.

**Tech Stack:** omc: Python 3 (`uv run`, pytest, `just check`). GitNexus fork: TypeScript (Node, vitest, `npm run build`). GitHub via `gh`.

**Spec:** `docs/superpowers/specs/2026-10-01-fix-doc-false-stall-gitnexus-progress-design.md` — read it first; this plan argues from it.

## Global Constraints

- Red → green for EVERY change: write the test, RUN it and watch it fail for the expected reason, then implement. Commit test + implementation together.
- Never `pytest.skip` / `skipif`. A missing prerequisite is a `pytest.fail` naming the fix.
- Stub scripts run on a restricted PATH: shell builtins (`echo`, `printf`, `case`, `sleep` is `/bin/sleep` on macOS but the existing stubs use bare `sleep` successfully — keep to what existing stubs in the same file already use), absolute paths, quoted heredocs.
- Assert on artifacts and CLI stderr/stdout of omc's own processes, never on `claude -p` transcripts.
- `ToolContext` (`src/omc/toolctx.py`) is the only subprocess boundary. No new `subprocess` imports anywhere else.
- Gate after every omc task: `just check` (exit 0). Gate after every fork task: `npm run test:unit` in the fork's `gitnexus/` directory (exit 0).
- Never run `omc install` / `uv tool install`. Never edit the managed clone at `~/.omc/dependencies/gitnexus` — fork work happens in a fresh clone under the scratchpad.
- The shared constant is **300** seconds. `_WIKI_STALL_SECONDS` stays `300.0`, `_WIKI_POLL_SECONDS` stays `1.0`, both names unchanged (pinned by `tests/unit/test_wikirun.py`).
- GitNexus lines go to **stderr** only; omc's `OMC_PROGRESS` / `OMC_DEPENDENCY` stdout contracts are untouched.
- `wikirun.py` must NOT import `omc.cli.progress_bar` at module top level: it creates an import cycle (`omc.cli` → `start` → `gitnexus` → `wikirun`). Verified during planning. Import lazily inside the method that needs it.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Model-tier policy: every task carries a `Model:` line (top tier / heavy coding tier / standard coding tier). The cheap tier is never used.

## Review Focus

Inputs the spec implies but no single task's happy path exercises, most likely to bite first. Each has a pinning test in the owning task.

1. A `GITNEXUS_PROGRESS` line whose JSON is valid but `percent` is a boolean (`true`) or a float (`17.5`) — must be ignored, not stored or crash (Task 6).
2. A GitNexus failure whose stderr is long progress/pino noise followed by the real error — the user must see the error, so the excerpt is the tail (Tasks 8, 9).
3. A heartbeat line arriving after a phase line with no new phase — the project wiki must not narrate it, and the bar must not regress (Task 9).
4. An older GitNexus that prints no progress lines at all — `run_document` must still emit the page-count percent exactly as today (Task 8, existing tests kept green).
5. A raising `on_line` callback on a healthy, chatty child — the child must finish and its output must still be captured in full (Task 5).

---

## Part A — GitNexus fork (lands first)

### Task 1: Fresh clone of the fork and a green baseline

**Model:** standard coding tier

**Files:**
- Create (outside the omc repo): `/private/tmp/claude-501/-Users-chriphus-OpenSource-Projects-oh-my-clanker-feature-fix-doc-false-stall-gitnexus-progress/8619e37d-c49c-4f21-bb6e-2048cb218a07/scratchpad/GitNexus` (clone)

**Interfaces:**
- Produces: `$FORK` = that clone path, branch `feature/wiki-progress-lines-and-heartbeat` from `origin/main` (`8de99dc9`). Later fork tasks run inside `$FORK/gitnexus`.

- [ ] **Step 1: Clone and branch**

```bash
SCRATCH=/private/tmp/claude-501/-Users-chriphus-OpenSource-Projects-oh-my-clanker-feature-fix-doc-false-stall-gitnexus-progress/8619e37d-c49c-4f21-bb6e-2048cb218a07/scratchpad
git clone https://github.com/chris-husse/GitNexus.git "$SCRATCH/GitNexus"
cd "$SCRATCH/GitNexus"
git rev-parse --short origin/main        # expected: 8de99dc9
git checkout -b feature/wiki-progress-lines-and-heartbeat origin/main
```

- [ ] **Step 2: Install in the order omc's installer uses**

```bash
cd "$SCRATCH/GitNexus/gitnexus-shared" && npm install --no-audit --no-fund
cd "$SCRATCH/GitNexus/gitnexus" && npm ci --no-audit --no-fund
```
Expected: both exit 0 (the host already builds this tree for the managed install, so native deps compile here too).

- [ ] **Step 3: Baseline — run the precedent test**

Run: `cd "$SCRATCH/GitNexus/gitnexus" && npx vitest run test/unit/wiki-keepalive.test.ts`
Expected: PASS (2 tests). If it fails, stop: the baseline is broken and nothing below is trustworthy.

No commit (nothing changed).

---

### Task 2: In-flight heartbeat in the generator's LLM wrapper

**Model:** heavy coding tier

**Files:**
- Modify: `$FORK/gitnexus/src/core/wiki/generator.ts` (constructor `onProgress` wrapper near line 151; the `invokeLLM` method near lines 213–248)
- Test: `$FORK/gitnexus/test/unit/wiki-heartbeat.test.ts` (create)

**Interfaces:**
- Consumes: `WikiGenerator`'s existing `invokeLLM(prompt, systemPrompt, options?)`, the single method every grouping/page call passes through (it dispatches to `callCursorLLM` / `callClaudeLLM` / `callCodexLLM` / `callOpenCodeLLM` / `callLLM`). Existing fields `lastPercent`, `onProgress`.
- Produces: `onProgress('heartbeat', <lastPercent>, '<label> (30s)')`, `'… (60s)'`, … every 30 s while a call is in flight; nothing before or after. Task 3 prints these like any other change.

- [ ] **Step 1: Write the failing test**

Create `test/unit/wiki-heartbeat.test.ts`:

```ts
/**
 * In-flight heartbeat: while an LLM call is in flight the generator reports
 * onProgress('heartbeat', …) every 30s, so a piped parent (omc's stall guard)
 * sees output during long calls. Scoped to LLM calls on purpose — outside one,
 * silence means a wedge the parent should catch.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import os from 'os';
import path from 'path';
import fs from 'fs/promises';

const SLOW_LLM_MS = 6 * 60_000;

type Event = { phase: string; percent: number; detail?: string };

describe('WikiGenerator heartbeat', () => {
  let tmpDir: string;

  beforeEach(async () => {
    vi.resetModules();
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'wiki-heartbeat-test-'));
  });

  afterEach(async () => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  async function makeGenerator() {
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval'] });
    vi.doMock('../../src/core/wiki/graph-queries.js', () => ({
      initWikiDb: vi.fn().mockResolvedValue(undefined),
      closeWikiDb: vi.fn().mockResolvedValue(undefined),
      touchWikiDb: vi.fn(),
      getFilesWithExports: vi
        .fn()
        .mockResolvedValue([{ filePath: 'src/a.ts', symbols: [{ name: 'a', type: 'function' }] }]),
      getAllFiles: vi.fn().mockResolvedValue(['src/a.ts']),
      getIntraModuleCallEdges: vi.fn().mockResolvedValue([]),
      getInterModuleCallEdges: vi.fn().mockResolvedValue({ incoming: [], outgoing: [] }),
      getProcessesForFiles: vi.fn().mockResolvedValue([]),
      getAllProcesses: vi.fn().mockResolvedValue([]),
      getInterModuleEdgesForOverview: vi.fn().mockResolvedValue([]),
    }));
    vi.doMock('child_process', () => ({
      execSync: vi.fn().mockImplementation(() => {
        throw new Error('not a git repo');
      }),
      execFileSync: vi.fn(),
    }));

    const llmClient = await import('../../src/core/wiki/llm-client.js');
    // Buffered provider simulation: nothing streams; the answer lands after 6 min.
    const callLLMSpy = vi.spyOn(llmClient, 'callLLM').mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve({ content: JSON.stringify({ All: ['src/a.ts'] }) }), SLOW_LLM_MS),
        ),
    );

    const { WikiGenerator } = await import('../../src/core/wiki/generator.js');
    const storagePath = path.join(tmpDir, 'storage');
    await fs.mkdir(path.join(storagePath, 'wiki'), { recursive: true });
    const repoPath = path.join(tmpDir, 'repo');
    await fs.mkdir(repoPath, { recursive: true });
    const events: Event[] = [];
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
      (phase, percent, detail) => events.push({ phase, percent, detail }),
    );
    return { gen, events, callLLMSpy };
  }

  it('reports a heartbeat every 30s while an LLM call is in flight, and not after', async () => {
    const { gen, events, callLLMSpy } = await makeGenerator();
    const run = gen.run();
    // Wait until the mocked call is actually in flight before touching the fake clock
    // (run() does real async fs I/O first; see wiki-keepalive.test.ts).
    while (callLLMSpy.mock.calls.length === 0) {
      await new Promise((resolve) => setImmediate(resolve));
    }
    for (let i = 0; i < 6; i++) {
      await vi.advanceTimersByTimeAsync(60_000);
    }
    await run;

    const beats = events.filter((e) => e.phase === 'heartbeat');
    expect(beats.length).toBeGreaterThanOrEqual(10); // 6 min / 30 s = 12, allow edge ticks
    expect(beats[0].detail).toBe('Grouping files into modules (LLM)... (30s)');
    expect(beats[1].detail).toBe('Grouping files into modules (LLM)... (60s)');
    // grouping is reported at 15 before the call and nothing else moves it during the call
    expect(beats.every((e) => e.percent === 15)).toBe(true);

    // After run() settles the interval is cleared — no further heartbeats
    const settled = beats.length;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(events.filter((e) => e.phase === 'heartbeat').length).toBe(settled);
  }, 60000);

  it('emits no heartbeat before the first LLM call', async () => {
    const { gen, events, callLLMSpy } = await makeGenerator();
    const run = gen.run();
    while (callLLMSpy.mock.calls.length === 0) {
      await new Promise((resolve) => setImmediate(resolve));
    }
    // Nothing has ticked yet: the interval starts with the call, not with run()
    expect(events.filter((e) => e.phase === 'heartbeat')).toEqual([]);
    for (let i = 0; i < 6; i++) {
      await vi.advanceTimersByTimeAsync(60_000);
    }
    await run;
  }, 60000);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd "$FORK/gitnexus" && npx vitest run test/unit/wiki-heartbeat.test.ts`
Expected: FAIL — first test: `expected 0 to be greater than or equal to 10` (no heartbeat events exist yet). Second test passes trivially; that is fine — it pins the "not before" half.

- [ ] **Step 3: Implement**

In `src/core/wiki/generator.ts`:

(a) Near the other constants (after `GROUPING_TOKEN_BUDGET`):
```ts
// Heartbeat cadence while an LLM call is in flight (see invokeLLM). omc's
// stall guard kills a silent child after 300 s; 30 s is a 10× margin.
const HEARTBEAT_SECONDS = 30;
```

(b) Add a field next to `private lastPercent = 0;`:
```ts
  private lastPercent = 0;
  private lastLabel = '';
```

(c) In the constructor, replace the `this.onProgress = …` wrapper with:
```ts
    const progressFn = onProgress || (() => {});
    this.onProgress = (phase, percent, detail) => {
      if (percent > 0) this.lastPercent = percent;
      // The label a heartbeat repeats: the last real phase, never a stream
      // token count and never a previous heartbeat.
      if (phase !== 'heartbeat' && phase !== 'stream') this.lastLabel = detail || phase;
      progressFn(phase, percent, detail);
    };
```

(d) Rename the existing `invokeLLM` method to `dispatchLLM` (body unchanged, same signature), and add a new `invokeLLM` in front of it:
```ts
  /**
   * Route one LLM call through the provider dispatch, heartbeating while it
   * is in flight. While a model call runs — retries and backoff included —
   * report onProgress('heartbeat', …) every HEARTBEAT_SECONDS so a piped
   * parent (omc's stall guard) sees activity. Scoped to LLM calls on purpose:
   * outside one, silence means a wedge the parent should catch; inside one,
   * the request timeout (--timeout) bounds a hang. Same shape as the
   * __wiki__ keepalive in run().
   */
  private async invokeLLM(
    prompt: string,
    systemPrompt: string,
    options?: CallLLMOptions,
  ): Promise<LLMResponse> {
    const label = this.lastLabel;
    let ticks = 0;
    const heartbeat = setInterval(() => {
      ticks += 1;
      this.onProgress('heartbeat', this.lastPercent, `${label} (${ticks * HEARTBEAT_SECONDS}s)`);
    }, HEARTBEAT_SECONDS * 1000);
    heartbeat.unref?.();
    try {
      return await this.dispatchLLM(prompt, systemPrompt, options);
    } finally {
      clearInterval(heartbeat);
    }
  }

  /**
   * Route LLM call to the appropriate provider.
   */
  private async dispatchLLM(
    prompt: string,
    systemPrompt: string,
    options?: CallLLMOptions,
  ): Promise<LLMResponse> {
    // … existing body of the old invokeLLM, unchanged …
  }
```
Callers of `invokeLLM` are unchanged (grep `this.invokeLLM(` to confirm none call `dispatchLLM`).

- [ ] **Step 4: Run the new test and the precedent test**

Run: `cd "$FORK/gitnexus" && npx vitest run test/unit/wiki-heartbeat.test.ts test/unit/wiki-keepalive.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Gate and commit**

Run: `cd "$FORK/gitnexus" && npm run test:unit`
Expected: exit 0.

```bash
cd "$FORK"
git add gitnexus/src/core/wiki/generator.ts gitnexus/test/unit/wiki-heartbeat.test.ts
git commit -m "$(cat <<'EOF'
feat(wiki): heartbeat progress every 30s while an LLM call is in flight

Piped parents (omc's stall guard) see no bar and nothing on disk during
grouping, so a phase of many silent calls reads as a wedge. The generator's
single LLM wrapper now reports onProgress('heartbeat', …) every 30s for the
duration of each call, retries included, and nothing outside a call.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Off-TTY `GITNEXUS_PROGRESS` lines from the wiki command

**Model:** standard coding tier

**Files:**
- Create: `$FORK/gitnexus/src/cli/wiki-progress.ts`
- Modify: `$FORK/gitnexus/src/cli/wiki.ts` (imports near line 20; the `new WikiGenerator(…, (phase, percent, detail) => {…})` callback near lines 520–533)
- Test: `$FORK/gitnexus/test/unit/wiki-progress-lines.test.ts` (create)

**Interfaces:**
- Consumes: `ProgressCallback` type exported from `src/core/wiki/generator.ts` (`(phase: string, percent: number, detail?: string) => void`).
- Produces: `makeProgressLineWriter(write: (line: string) => void): ProgressCallback` and `PROGRESS_LINE_PREFIX = 'GITNEXUS_PROGRESS '`. Each emitted line is `GITNEXUS_PROGRESS {"phase":…,"percent":…,"detail":…}` with no trailing newline (the caller adds `\n`). omc (Task 6) parses exactly this prefix and shape.

- [ ] **Step 1: Write the failing test**

Create `test/unit/wiki-progress-lines.test.ts`:

```ts
/**
 * Off-TTY progress lines: when stderr is piped, cli-progress renders nothing,
 * so the wiki command reports one GITNEXUS_PROGRESS JSON line per change
 * instead. Consumers (omc) match on the prefix.
 */
import { describe, it, expect } from 'vitest';
import { makeProgressLineWriter, PROGRESS_LINE_PREFIX } from '../../src/cli/wiki-progress.js';

function collect() {
  const lines: string[] = [];
  const report = makeProgressLineWriter((line) => lines.push(line));
  return { lines, report };
}

describe('makeProgressLineWriter', () => {
  it('writes one prefixed JSON line per changed (percent, detail) pair', () => {
    const { lines, report } = collect();
    report('grouping', 15, 'Grouping files into modules (LLM)...');
    report('grouping', 17, 'Grouping batch 1/3 (LLM)...');
    expect(lines).toEqual([
      `${PROGRESS_LINE_PREFIX}{"phase":"grouping","percent":15,"detail":"Grouping files into modules (LLM)..."}`,
      `${PROGRESS_LINE_PREFIX}{"phase":"grouping","percent":17,"detail":"Grouping batch 1/3 (LLM)..."}`,
    ]);
    expect(PROGRESS_LINE_PREFIX).toBe('GITNEXUS_PROGRESS ');
  });

  it('does not repeat an unchanged pair', () => {
    const { lines, report } = collect();
    report('modules', 40, 'auth');
    report('modules', 40, 'auth');
    expect(lines).toHaveLength(1);
  });

  it('skips stream chunk events entirely', () => {
    const { lines, report } = collect();
    report('grouping', 15, 'Grouping files into modules (LLM)...');
    report('stream', 16, 'Grouping files into modules (LLM)... (120 tok)');
    report('stream', 18, 'Grouping files into modules (LLM)... (900 tok)');
    expect(lines).toHaveLength(1);
  });

  it('writes every heartbeat tick because its detail changes', () => {
    const { lines, report } = collect();
    report('grouping', 15, 'Grouping files into modules (LLM)...');
    report('heartbeat', 15, 'Grouping files into modules (LLM)... (30s)');
    report('heartbeat', 15, 'Grouping files into modules (LLM)... (60s)');
    expect(lines).toHaveLength(3);
    expect(JSON.parse(lines[2].slice(PROGRESS_LINE_PREFIX.length))).toEqual({
      phase: 'heartbeat',
      percent: 15,
      detail: 'Grouping files into modules (LLM)... (60s)',
    });
  });

  it('falls back to the phase name when detail is absent', () => {
    const { lines, report } = collect();
    report('done', 100);
    expect(lines).toEqual([`${PROGRESS_LINE_PREFIX}{"phase":"done","percent":100,"detail":"done"}`]);
  });

  it('never embeds a newline (one line per event, the caller terminates it)', () => {
    const { lines, report } = collect();
    report('modules', 50, 'line\nbreak');
    expect(lines[0].includes('\n')).toBe(false); // JSON.stringify escapes it
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd "$FORK/gitnexus" && npx vitest run test/unit/wiki-progress-lines.test.ts`
Expected: FAIL — `Failed to resolve import "../../src/cli/wiki-progress.js"` (module does not exist).

- [ ] **Step 3: Implement the module**

Create `src/cli/wiki-progress.ts`:

```ts
/**
 * Off-TTY progress reporting for `gitnexus wiki`.
 *
 * cli-progress renders nothing when stderr is not a TTY, so a piped parent
 * (omc's stall guard, CI logs) saw total silence for the whole run. When
 * piped, the wiki command reports one newline-terminated JSON line per
 * progress change on stderr instead. Consumers match on the prefix — pino's
 * own JSON records share the stream.
 */
import type { ProgressCallback } from '../core/wiki/generator.js';

export const PROGRESS_LINE_PREFIX = 'GITNEXUS_PROGRESS ';

/**
 * Build a ProgressCallback that writes `GITNEXUS_PROGRESS {…}` lines.
 * - one line per changed (percent, detail) pair; an unchanged pair is not repeated
 * - `stream` chunk events are skipped: they only move a token-count label, and
 *   the in-flight heartbeat already carries liveness during a call
 * - `write` receives the line WITHOUT a trailing newline
 */
export function makeProgressLineWriter(write: (line: string) => void): ProgressCallback {
  let last = '';
  return (phase, percent, detail) => {
    if (phase === 'stream') return;
    const label = detail ?? phase;
    const key = `${percent}\u0000${label}`;
    if (key === last) return;
    last = key;
    write(PROGRESS_LINE_PREFIX + JSON.stringify({ phase, percent, detail: label }));
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd "$FORK/gitnexus" && npx vitest run test/unit/wiki-progress-lines.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Wire it into the wiki command**

In `src/cli/wiki.ts`:

(a) Imports — change the generator import and add the new module:
```ts
import { WikiGenerator, type WikiOptions, type ProgressCallback } from '../core/wiki/generator.js';
import { makeProgressLineWriter } from './wiki-progress.js';
```

(b) Replace the inline callback passed to `new WikiGenerator(…)` (the block that starts `(phase, percent, detail) => {` and ends with `bar.update(percent, { phase: label });`). Insert this just before `const generator = new WikiGenerator(`:
```ts
  // Piped stderr (omc, CI, redirected logs): cli-progress renders nothing off
  // a TTY, so report one GITNEXUS_PROGRESS JSON line per change instead. The
  // bar, its start/stop and the elapsed timer stay as they are — cli-progress
  // already makes them no-ops off a TTY.
  const piped = process.stderr.isTTY !== true;
  const report: ProgressCallback = piped
    ? makeProgressLineWriter((line) => {
        process.stderr.write(line + '\n');
      })
    : (phase, percent, detail) => {
        const label = detail || phase;
        if (label !== lastPhase) {
          lastPhase = label;
          phaseStart = Date.now();
        }
        bar.update(percent, { phase: label });
      };
```
and pass `report` as the last constructor argument:
```ts
  const generator = new WikiGenerator(
    repoPath,
    storagePath,
    lbugPath,
    llmConfig,
    wikiOptions,
    report,
  );
```

- [ ] **Step 6: Build and smoke the CLI**

Run: `cd "$FORK/gitnexus" && npm run build && node dist/cli/index.js wiki --help 2>&1 | head -3`
Expected: build exit 0; help text prints (proves the module resolves at runtime). The piped behaviour itself is exercised by omc's real-tool E2E (Part B) and the live acceptance.

- [ ] **Step 7: Gate and commit**

Run: `cd "$FORK/gitnexus" && npm run test:unit`
Expected: exit 0.

```bash
cd "$FORK"
git add gitnexus/src/cli/wiki-progress.ts gitnexus/src/cli/wiki.ts gitnexus/test/unit/wiki-progress-lines.test.ts
git commit -m "$(cat <<'EOF'
feat(wiki): report GITNEXUS_PROGRESS JSON lines on stderr when piped

cli-progress renders nothing off a TTY, so a piped parent saw silence for the
whole run. When stderr is not a TTY the wiki command now writes one
newline-terminated `GITNEXUS_PROGRESS {"phase","percent","detail"}` line per
progress change (stream chunk events skipped). The TTY bar is untouched.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Push the fork branch and open its PR

**Model:** standard coding tier

**Files:**
- Create: `$SCRATCH/gitnexus-pr.txt` (records the PR URL for Task 11)

- [ ] **Step 1: Final full gate**

Run: `cd "$FORK/gitnexus" && npm run test:unit && npm run build`
Expected: both exit 0.

- [ ] **Step 2: Push**

```bash
cd "$FORK" && git push -u origin feature/wiki-progress-lines-and-heartbeat
```

- [ ] **Step 3: Open the PR against main**

```bash
cd "$FORK" && gh pr create --repo chris-husse/GitNexus --base main \
  --title "wiki: progress lines when piped + heartbeat during LLM calls" \
  --body "$(cat <<'EOF'
## Why

Piped parents (omc's documentation runs) saw total silence from `gitnexus wiki` during the grouping phase: cli-progress renders nothing off a TTY and nothing reaches disk until `first_module_tree.json`. A phase of many sequential LLM calls therefore read as a wedge and was killed after 300 s of silence, even though every single call was short.

## What

- `src/cli/wiki-progress.ts` (new): when `process.stderr.isTTY !== true`, the wiki command writes one newline-terminated `GITNEXUS_PROGRESS {"phase","percent","detail"}` line per progress change on stderr instead of starting the bar. `stream` chunk events are skipped. TTY behaviour unchanged.
- `src/core/wiki/generator.ts`: the single LLM wrapper (`invokeLLM`) heartbeats `onProgress('heartbeat', …)` every 30 s while a call is in flight (retries included) and never outside one, so a wedge outside an LLM call still reads as silence. Same shape as the `__wiki__` keepalive.
- Tests: `test/unit/wiki-progress-lines.test.ts`, `test/unit/wiki-heartbeat.test.ts` (fake-clock pattern from `wiki-keepalive.test.ts`).

Design record (omc): `docs/superpowers/specs/2026-10-01-fix-doc-false-stall-gitnexus-progress-design.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: prints the PR URL.

- [ ] **Step 4: Record the PR URL**

```bash
gh pr view --repo chris-husse/GitNexus feature/wiki-progress-lines-and-heartbeat --json url -q .url > "$SCRATCH/gitnexus-pr.txt"
cat "$SCRATCH/gitnexus-pr.txt"
```

---

## Part B — omc (this worktree)

### Task 5: `run_supervised` gains `on_line`

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/toolctx.py` (`run_supervised`, lines ~120–237: signature, docstring, `pump`)
- Modify: `docs/superpowers/specs/2026-10-01-fix-doc-false-stall-gitnexus-progress-design.md` (§4.1.2 and §8: the generator wrapper is named `invokeLLM`, not `callLLM`)
- Test: `tests/unit/test_toolctx.py` (append after `test_supervised_kills_child_group_when_interrupted`)

**Interfaces:**
- Produces: `ToolContext.run_supervised(argv, *, heartbeat, stall_after=300.0, poll=1.0, cwd=None, extra_env=None, on_line: Callable[[str], None] | None = None)`. `on_line` receives every stdout and stderr line, newline stripped, serialized under one lock, from the pump threads. Exceptions it raises are contained. Default `None` is byte-identical to today.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_toolctx.py`:

```python
def test_supervised_on_line_receives_whole_lines_from_both_pipes_serialized(tmp_path):
    import threading

    ctx = _ctx(tmp_path)
    code = (
        "import sys\n"
        "for i in range(50):\n"
        "    print(f'out{i}', flush=True)\n"
        "    print(f'err{i}', file=sys.stderr, flush=True)\n"
    )
    seen: list[str] = []
    depth = {"cur": 0, "max": 0}
    guard = threading.Lock()

    def on_line(line: str) -> None:
        with guard:
            depth["cur"] += 1
            depth["max"] = max(depth["max"], depth["cur"])
        time.sleep(0.001)  # widen the window so concurrent delivery would show
        seen.append(line)
        with guard:
            depth["cur"] -= 1

    cp, stalled = ctx.run_supervised(
        [sys.executable, "-u", "-c", code],
        heartbeat=lambda: 0,
        stall_after=5,
        poll=0.05,
        on_line=on_line,
    )
    assert stalled is False and cp.returncode == 0
    assert sorted(seen) == sorted([f"out{i}" for i in range(50)] + [f"err{i}" for i in range(50)])
    assert depth["max"] == 1  # never reentered concurrently
    assert cp.stdout.count("out") == 50 and cp.stderr.count("err") == 50  # capture unchanged


def test_supervised_raising_on_line_never_kills_an_active_child(tmp_path):
    ctx = _ctx(tmp_path)

    def boom(line: str) -> None:
        raise RuntimeError(f"callback failed on {line}")

    script = "for i in 1 2 3 4 5 6; do echo tick; sleep 0.2; done"
    cp, stalled = ctx.run_supervised(
        ["sh", "-c", script], heartbeat=lambda: 0, stall_after=0.5, poll=0.05, on_line=boom
    )
    assert stalled is False  # a reporter bug is contained, unlike stream()'s contract
    assert cp.returncode == 0
    assert cp.stdout.count("tick") == 6  # every line still captured after the callback raised
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_toolctx.py -k "on_line_receives or raising_on_line" -v`
Expected: FAIL with `TypeError: ToolContext.run_supervised() got an unexpected keyword argument 'on_line'`.

- [ ] **Step 3: Implement**

In `src/omc/toolctx.py`, `run_supervised`:

(a) Signature — add the last parameter:
```python
    def run_supervised(
        self,
        argv: Sequence[str],
        *,
        heartbeat: Callable[[], object],
        stall_after: float = 300.0,
        poll: float = 1.0,
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
```

(b) Docstring — append after the heartbeat paragraph:
```python
        ``on_line``, when given, receives every stdout and stderr line (newline
        stripped) from the reader threads, serialized under one lock like
        ``stream()``. Unlike ``stream()``, a failing callback is CONTAINED, not
        re-raised: this method's job is liveness, and a reporter bug must
        neither kill a healthy child nor crash the supervisor — the same
        doctrine as heartbeat exceptions. Capture is unaffected either way.
```

(c) `pump` — replace the existing inner function with:
```python
        lock = threading.Lock()  # serializes on_line, as stream() does

        def pump(pipe, key: str) -> None:
            try:
                for raw in pipe:
                    chunks[key].append(raw)
                    activity[0] += len(raw)
                    if on_line is not None:
                        with lock, contextlib.suppress(Exception):
                            on_line(raw.rstrip("\n"))
            finally:
                pipe.close()
```
(`contextlib` and `threading` are already imported in this module.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_toolctx.py -v`
Expected: PASS, including the seven pre-existing `test_supervised_*` tests and the five `test_stream_*` tests.

- [ ] **Step 5: Spec touch-up**

In the spec, §4.1.2 heading sentence and §8 §4.1 note: replace "the generator's `callLLM` method" / "generator's single `callLLM` wrapper" with "`invokeLLM`" (the method is named `invokeLLM` in `generator.ts`; Task 2 wraps it and moves the old body to `dispatchLLM`). Two one-word edits; no other spec change.

- [ ] **Step 6: Gate and commit**

Run: `just check`
Expected: exit 0.

```bash
git add src/omc/toolctx.py tests/unit/test_toolctx.py docs/superpowers/specs/2026-10-01-fix-doc-false-stall-gitnexus-progress-design.md
git commit -m "$(cat <<'EOF'
feat(toolctx): run_supervised on_line callback for live child lines

Both pipes already read whole lines; hand each to an optional callback under
one lock, with exceptions contained (liveness doctrine, unlike stream()).
Default None keeps every caller byte-identical.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `GitNexusProgress` and the shared 300 s constant in `wikirun.py`

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/wikirun.py` (module docstring, constants at lines ~16–22, new class after `PageCountTracker`)
- Test: `tests/unit/test_wikirun.py`

**Interfaces:**
- Produces:
  - `LLM_CALL_SECONDS: int = 300`, `_WIKI_STALL_SECONDS: float = 300.0` (unchanged name/value), `WIKI_TIMEOUT_ARG: str = "300"`.
  - `PROGRESS_PREFIX = "GITNEXUS_PROGRESS "`.
  - `class GitNexusProgress` with `feed(line: str) -> None`, properties `percent: int | None`, `phase: str`, `detail: str`, and `render(now: float | None = None, width: int = 18) -> str`. Constructor `GitNexusProgress(clock: Callable[[], float] = time.monotonic)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_wikirun.py`:

```python
def test_one_llm_call_budget_feeds_both_the_stall_window_and_the_timeout_arg():
    import omc.wikirun as wr

    assert wr.LLM_CALL_SECONDS == 300
    assert wr._WIKI_STALL_SECONDS == float(wr.LLM_CALL_SECONDS)
    assert wr.WIKI_TIMEOUT_ARG == "300"  # gitnexus wiki --timeout takes whole seconds
    assert float(wr.WIKI_TIMEOUT_ARG) == wr._WIKI_STALL_SECONDS


def test_gitnexus_progress_feeds_prefixed_json_lines_only():
    from omc.wikirun import PROGRESS_PREFIX, GitNexusProgress

    gn = GitNexusProgress()
    assert gn.percent is None and gn.phase == "" and gn.detail == ""
    gn.feed('{"level":30,"msg":"pino record on the same stream"}')
    gn.feed("GITNEXUS_PROGRES {\"percent\": 5}")  # wrong prefix
    gn.feed(PROGRESS_PREFIX + "{not json")
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping"}')  # no percent
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":101}')
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":-1}')
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":true}')  # bool is not a percent
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":17.5}')
    gn.feed(PROGRESS_PREFIX + '[17]')
    assert gn.percent is None  # nothing above was accepted, nothing raised
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":17,"detail":"Grouping batch 2/23 (LLM)..."}')
    assert (gn.percent, gn.phase, gn.detail) == (17, "grouping", "Grouping batch 2/23 (LLM)...")
    gn.feed(PROGRESS_PREFIX + '{"phase":"done","percent":100}')
    assert (gn.percent, gn.phase, gn.detail) == (100, "done", "")


def test_gitnexus_progress_renders_through_the_shared_bar():
    from omc.cli.progress_bar import render_bar
    from omc.wikirun import PROGRESS_PREFIX, GitNexusProgress

    now = [1000.0]
    gn = GitNexusProgress(clock=lambda: now[0])
    assert gn.render() == render_bar(None, 0.0, spin=0)  # indeterminate, first spin slot
    assert gn.render() == render_bar(None, 0.0, spin=1)  # bounce advances per redraw
    gn.feed(PROGRESS_PREFIX + '{"phase":"modules","percent":40,"detail":"auth"}')
    now[0] = 1012.0
    assert gn.render() == render_bar(40, 12.0, spin=2)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_wikirun.py -v`
Expected: the three new tests FAIL with `AttributeError: module 'omc.wikirun' has no attribute 'LLM_CALL_SECONDS'` / `ImportError: cannot import name 'GitNexusProgress'`; the three existing tests still pass.

- [ ] **Step 3: Implement**

In `src/omc/wikirun.py`:

(a) Replace the two constants block with:
```python
# The longest a single LLM call may legitimately take. ONE number, two uses
# (spec 2026-10-01 fix-doc-false-stall-gitnexus-progress §4.2.3):
#  1. run_supervised's silence window — GitNexus heartbeats every 30 s while a
#     call is in flight (fork generator.ts/wiki.ts), so silence this long means
#     a wedge OUTSIDE any LLM call, which is exactly what the guard should kill.
#  2. the `--timeout` omc passes to `gitnexus wiki`, which bounds a hung call
#     INSIDE GitNexus (one budget across its retries on the API client).
# Not a per-model tuning knob: liveness no longer depends on model speed.
LLM_CALL_SECONDS = 300
_WIKI_STALL_SECONDS = float(LLM_CALL_SECONDS)
WIKI_TIMEOUT_ARG = str(LLM_CALL_SECONDS)  # `gitnexus wiki --timeout` takes whole seconds

# One disk poll per second drives BOTH the stall-guard heartbeat and progress
# reporting; monkeypatchable in tests.
_WIKI_POLL_SECONDS = 1.0

# Prefix of the progress lines the fork's wiki command prints on stderr when
# piped (fork src/cli/wiki-progress.ts): `GITNEXUS_PROGRESS {"phase","percent","detail"}`.
PROGRESS_PREFIX = "GITNEXUS_PROGRESS "
```

(b) Add `import time` and `from collections.abc import Callable` to the imports.

(c) Append the class after `PageCountTracker`:
```python
class GitNexusProgress:
    """GitNexus's own whole-run progress, read from the `GITNEXUS_PROGRESS {…}`
    lines its wiki command prints on stderr when piped (spec §4.1.1). Shaped
    like buildprogress.ProgressTracker so cli.progress_bar.BarThread can drive
    it: feed() / percent / render(). Single attribute writes under the GIL —
    fed on run_supervised's reader thread, read on the supervising or bar
    thread. Pure data source: no I/O, no rendering policy of its own.

    Malformed or foreign lines (pino records share the stream) are ignored,
    never fatal: progress plumbing must never crash a documentation run."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._percent: int | None = None
        self._phase = ""
        self._detail = ""
        self._spin = 0

    def feed(self, line: str) -> None:
        if not line.startswith(PROGRESS_PREFIX):
            return
        try:
            data = json.loads(line[len(PROGRESS_PREFIX) :])
            pct = data["percent"]
        except (ValueError, KeyError, TypeError):
            return  # not JSON, not an object, or no percent
        # bool is an int subclass; a float is not a percent either
        if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100:
            return
        self._percent = pct
        self._phase = str(data.get("phase", ""))
        self._detail = str(data.get("detail", ""))

    @property
    def percent(self) -> int | None:
        return self._percent

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def detail(self) -> str:
        return self._detail

    def render(self, now: float | None = None, width: int = 18) -> str:
        # Lazy import: a module-level `from .cli.progress_bar import render_bar`
        # is an import cycle (omc.cli → start → gitnexus → wikirun). Verified.
        from .cli.progress_bar import render_bar

        spin = self._spin
        if self._percent is None:
            self._spin += 1  # bounce advances one slot per redraw, like ProgressTracker
        elapsed = (self._clock() if now is None else now) - self._start
        return render_bar(self._percent, elapsed, width=width, spin=spin)
```

(d) Module docstring — append one sentence: "`GitNexusProgress` is the second progress source: GitNexus's own lines, once it speaks."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_wikirun.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Gate and commit**

Run: `just check`
Expected: exit 0.

```bash
git add src/omc/wikirun.py tests/unit/test_wikirun.py
git commit -m "$(cat <<'EOF'
feat(wikirun): GitNexusProgress holder and the shared LLM-call budget

One 300 s constant now derives both the stall window and the --timeout
argument. GitNexusProgress parses GITNEXUS_PROGRESS lines into
percent/phase/detail and renders through the shared bar (lazy import: a
top-level one is a cycle through omc.cli).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `--timeout` on both documentation backends

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/providers/registry.py` (`docs_llm_for`, lines ~49–95)
- Modify: `tests/unit/test_dependency.py` (two `endswith` assertions, lines ~714–717 and ~735)
- Test: `tests/unit/test_providers.py` (append)

**Interfaces:**
- Consumes: `WIKI_TIMEOUT_ARG` from Task 6.
- Produces: `DocsRun.wiki_args` ends with `("--timeout", "300")` for both backends. Every existing `"wiki --provider …"` substring assertion keeps passing; only the two `endswith` assertions change.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_providers.py`:

```python
def test_docs_llm_for_passes_the_shared_llm_call_budget_as_timeout():
    from omc.config.schema import Config, DocsConfig, LLMConfig, ProviderConfig, SecretsConfig
    from omc.providers.registry import docs_llm_for
    from omc.wikirun import WIKI_TIMEOUT_ARG

    cli = docs_llm_for(Config(llm=LLMConfig(default="claude")))
    assert cli.wiki_args == (
        "wiki", "--provider", "claude", "--model", "sonnet", "--timeout", WIKI_TIMEOUT_ARG
    )
    api = docs_llm_for(
        Config(
            llm=LLMConfig(
                default="claude",
                docs=DocsConfig(backend="api"),
                providers={"claude": ProviderConfig(docs_model="claude-sonnet-5-5")},
            ),
            secrets=SecretsConfig(api_keys={"claude": "sk-ant-test-0123456789abcdef"}),
        )
    )
    assert api.wiki_args == (
        "wiki", "--provider", "custom", "--base-url", "https://api.anthropic.com/v1/",
        "--model", "claude-sonnet-5-5", "--reasoning-model", "--timeout", WIKI_TIMEOUT_ARG,
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_providers.py -k shared_llm_call_budget -v`
Expected: FAIL — tuple mismatch, the actual `wiki_args` lacks `--timeout`.

- [ ] **Step 3: Implement**

In `src/omc/providers/registry.py`:

(a) Import: `from ..wikirun import WIKI_TIMEOUT_ARG` (next to the existing relative imports).

(b) Above `def docs_llm_for`:
```python
# `--timeout`: GitNexus's own per-call budget — one AbortSignal across its
# retries on the API client, a kill timer per spawn on the local CLI clients.
# The 2026-07-23 record (fix-dependency-watch-never-completes) left it off
# because omc's stall guard covered everything. Since GitNexus heartbeats
# through every LLM call (fork, 2026-10-01), the stall guard can no longer
# see a hung call — this flag is what catches it now. Same number as the
# stall window, by design (wikirun.LLM_CALL_SECONDS).
_TIMEOUT_ARGS = ("--timeout", WIKI_TIMEOUT_ARG)
```

(c) In `docs_llm_for`, the cli branch:
```python
        args = ("wiki", "--provider", name, *(("--model", model) if model else ()), *_TIMEOUT_ARGS)
```
and the api `args` tuple gains `*_TIMEOUT_ARGS` after `"--reasoning-model",`.

- [ ] **Step 4: Update the two `endswith` assertions**

In `tests/unit/test_dependency.py`:
- `test_document_api_backend_key_only_in_child_env`: the `argv_line.endswith(` string gains ` --timeout 300` after `--reasoning-model`.
- `test_document_cli_backend_with_a_stored_key_passes_no_key`: `lines[0].endswith("wiki --provider claude --model sonnet --timeout 300")`.

- [ ] **Step 5: Run the affected suites**

Run: `uv run pytest tests/unit/test_providers.py tests/unit/test_dependency.py tests/unit/test_gitnexus_refresh.py tests/unit/test_watch.py -q`
Expected: PASS.

- [ ] **Step 6: Gate and commit**

Run: `just check`
Expected: exit 0.

```bash
git add src/omc/providers/registry.py tests/unit/test_providers.py tests/unit/test_dependency.py
git commit -m "$(cat <<'EOF'
feat(docs): pass --timeout 300 to gitnexus wiki on both backends

GitNexus now heartbeats through every LLM call, so omc's silence guard can no
longer see a hung call; GitNexus's own request timeout bounds it instead.
Same number as the stall window — one LLM-call budget, two uses.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `run_document` reports GitNexus's percent and shows the error tail

**Model:** standard coding tier

**Files:**
- Modify: `src/omc/dependency.py` (`run_document`, lines ~324–419; the import at line 33)
- Test: `tests/unit/test_dependency.py` (append)

**Interfaces:**
- Consumes: `run_supervised(..., on_line=…)` (Task 5), `GitNexusProgress` (Task 6).
- Produces: `OMC_PROGRESS` lines unchanged in shape; percent source is GitNexus once it has spoken, else the page count. Failure excerpt is the last 400 chars after redaction.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dependency.py`:

```python
def test_document_reports_gitnexus_percent_over_the_page_count_once_it_speaks(
    tmp_path, capsys, monkeypatch
):
    import omc.dependency as dep

    ctx, _, _ = _ctx(tmp_path)
    dest = _seed_indexed(ctx, with_wiki=False)
    wiki = dest / ".gitnexus" / "wiki"
    _tree(wiki, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])  # 3 + overview = 4
    (wiki / "a.md").write_text("x")  # page count says 25 at start
    monkeypatch.setattr(dep, "_WIKI_POLL_SECONDS", 0.05)
    # The fake GitNexus speaks on stderr (restricted PATH: builtins only), and a
    # second page lands mid-run — once GitNexus has spoken, the disk count must
    # no longer drive OMC_PROGRESS. Foreign and malformed lines are ignored.
    node = tmp_path / "bin" / "node"
    node.write_text(
        "#!/bin/sh\n"
        "echo 'GITNEXUS_PROGRESS {\"phase\":\"grouping\",\"percent\":17,"
        "\"detail\":\"Grouping batch 1/3 (LLM)...\"}' >&2\n"
        "sleep 0.3\n"
        "printf x > .gitnexus/wiki/b.md\n"
        "echo 'GITNEXUS_PROGRESS {\"phase\":\"modules\",\"percent\":40,\"detail\":\"b\"}' >&2\n"
        "echo 'not a progress line' >&2\n"
        "echo 'GITNEXUS_PROGRESS {\"phase\":\"junk\",\"percent\":250}' >&2\n"
        "sleep 0.3\n"
        "exit 0\n"
    )
    assert run_document(ctx, f"github.com/foo/bar@{H}") == 0
    vals = _progress_values(capsys)
    assert vals[0] == 25  # disk count before GitNexus spoke
    assert 17 in vals and 40 in vals  # GitNexus's whole-run percent, in order
    assert vals.index(17) < vals.index(40)
    assert 50 not in vals  # b.md landing did not re-assert the disk count
    assert vals[-1] == 100  # deterministic completion signal unchanged


def test_document_failure_excerpt_is_the_tail_where_the_error_is(tmp_path, capsys):
    ctx, _, nodecalls = _ctx(tmp_path)
    _seed_indexed(ctx)
    # pino records and progress lines precede the error on stderr; a head cut
    # would show 400 chars of noise and no error.
    _env_echoing_node(tmp_path, nodecalls, rc=1, stderr="x" * 500 + " LLM API error: boom")
    assert run_document(ctx, "github.com/foo/bar") == 1
    err = capsys.readouterr().err
    assert "LLM API error: boom" in err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_dependency.py -k "gitnexus_percent_over or excerpt_is_the_tail" -v`
Expected: FAIL — first: `assert 17 in vals` fails (no GitNexus lines are read today); second: `"LLM API error: boom" in err` fails (head cut keeps only x's).

- [ ] **Step 3: Implement**

In `src/omc/dependency.py`:

(a) Line 33 import becomes:
```python
from .wikirun import (  # noqa: F401
    _WIKI_POLL_SECONDS,
    _WIKI_STALL_SECONDS,
    GitNexusProgress,
    PageCountTracker,
)
```

(b) In `run_document`, after `tracker = PageCountTracker(wiki)` and the resume announcement, replace the `last_pct`/`_beat` block and the `run_supervised` call with:
```python
    gn = GitNexusProgress()

    def _percent() -> int | None:
        # GitNexus's whole-run percent once it has spoken (fork prints
        # GITNEXUS_PROGRESS lines when piped); the disk page count until then —
        # and for an older GitNexus that never speaks, exactly today's behavior.
        return gn.percent if gn.percent is not None else tracker.percent

    last_pct = _percent()
    if last_pct is not None:
        _progress(last_pct)

    def _beat() -> object:
        # Heartbeat AND reporter: the same disk poll feeds the stall guard's
        # token and emits OMC_PROGRESS whenever the integer percent moves. The
        # supervising thread is the only one that prints to stdout.
        nonlocal last_pct
        token = tracker.beat()
        pct = _percent()
        if pct is not None and pct != last_pct:
            last_pct = pct
            _progress(pct)
        return token

    cp, stalled = ctx.run_supervised(
        gitnexus_argv(ctx, *run.wiki_args),
        cwd=dest,
        heartbeat=_beat,
        stall_after=_WIKI_STALL_SECONDS,
        poll=_WIKI_POLL_SECONDS,
        extra_env=run.extra_env,
        on_line=gn.feed,  # GitNexus's stderr lines, parsed on the reader thread
    )
```
(The existing `last_pct = tracker.percent` / `if last_pct is not None: _progress(last_pct)` lines are replaced by the above; keep `tracker.refresh()` and the resume print before it.)

(c) The failure print: change `[:400]` to `[-400:]` and extend its comment:
```python
        # Exact-key redaction FIRST, the userinfo heuristic second (…unchanged…),
        # and truncation last — from the TAIL: GitNexus prints the error after
        # its pino records and progress lines, so the head is noise.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_dependency.py -v`
Expected: PASS, including the pre-existing `test_document_stall_kill_exits_1_and_keeps_documented_false`, `test_document_emits_progress_as_pages_land`, `test_document_reports_only_final_100_on_fresh_run`, and `test_document_api_failure_tail_is_redacted_before_truncation` (its 395-char redacted line fits a tail window as well as a head one).

- [ ] **Step 5: Gate and commit**

Run: `just check`
Expected: exit 0.

```bash
git add src/omc/dependency.py tests/unit/test_dependency.py
git commit -m "$(cat <<'EOF'
feat(dependency): OMC_PROGRESS from GitNexus's own percent once it speaks

run_document feeds GitNexus's stderr lines into GitNexusProgress; the
one-second beat reports that percent over the disk page count as soon as
the first line arrives, and the page count otherwise (older GitNexus). The
failure excerpt is now the tail, where the error is.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `_run_wiki` — bar on a TTY, `·` phase narration off it, error tail

**Model:** heavy coding tier

**Files:**
- Modify: `src/omc/cli/progress_bar.py` (`BarThread`: add an `enabled` property)
- Modify: `src/omc/gitnexus.py` (imports at line 21; `_run_wiki`, lines ~338–365)
- Test: `tests/unit/test_gitnexus_refresh.py` (append)

**Interfaces:**
- Consumes: `run_supervised(..., on_line=…)`, `GitNexusProgress`, `BarThread`.
- Produces: `BarThread.enabled -> bool` (True only when its output stream is a TTY). `_run_wiki` behaviour: TTY → in-place bar, no `·` lines; non-TTY → one `· <detail>` line per GitNexus phase change, heartbeats excluded, no bar. Failure excerpt is the tail.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_gitnexus_refresh.py`:

```python
_GN_LINES = (
    "echo 'GITNEXUS_PROGRESS {\"phase\":\"grouping\",\"percent\":15,"
    "\"detail\":\"Grouping files into modules (LLM)...\"}' >&2; "
    "echo 'GITNEXUS_PROGRESS {\"phase\":\"heartbeat\",\"percent\":15,"
    "\"detail\":\"Grouping files into modules (LLM)... (30s)\"}' >&2; "
    "echo 'GITNEXUS_PROGRESS {\"phase\":\"grouping\",\"percent\":28,"
    "\"detail\":\"Created 3 modules\"}' >&2; "
    "echo 'GITNEXUS_PROGRESS {\"phase\":\"grouping\",\"percent\":28,"
    "\"detail\":\"Created 3 modules\"}' >&2; "
)


def _wiki_behind_with_speaking_stub(tmp_path, *, extra_shell=""):
    """A wiki-behind repo whose fake GitNexus prints progress lines on stderr."""
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))  # wiki-unknown
    ctx, calls = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    arm = '*" wiki --provider"*) '
    text = node.read_text()
    assert arm in text
    node.write_text(text.replace(arm, arm + _GN_LINES + extra_shell))
    return ctx, repo


def test_wiki_off_tty_narrates_gitnexus_phase_changes_but_not_heartbeats(tmp_path):
    ctx, repo = _wiki_behind_with_speaking_stub(tmp_path)
    v, said = _run(ctx, repo, documentation=True, reset=False)  # pytest: stderr is not a TTY
    assert v.fresh
    assert "· Grouping files into modules (LLM)..." in said
    assert said.count("· Created 3 modules") == 1  # repeated identical line narrated once
    assert not any("(30s)" in s for s in said)  # heartbeats are not news
    # narration lands between the start line and the success line
    assert said.index("· Created 3 modules") > said.index(
        next(s for s in said if s.startswith("→ regenerating documentation via"))
    )


def test_wiki_on_tty_drives_the_bar_and_does_not_narrate(tmp_path, monkeypatch):
    import io

    import omc.cli.progress_bar as pb
    import omc.gitnexus as gitnexus_mod

    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    out = FakeTTY()
    # The established seam (tests/unit/test_progress_bar.py): a bar draws on the
    # `out=` stream it is given. Hand _run_wiki a BarThread bound to a fake TTY
    # instead of touching the global sys.stderr.
    monkeypatch.setattr(gitnexus_mod, "BarThread", lambda tracker: pb.BarThread(tracker, out=out))
    assert pb.BarThread(object(), out=out).enabled is True
    assert pb.BarThread(object(), out=io.StringIO()).enabled is False
    # linger so the 1 s redraw thread paints at least once before stop
    ctx, repo = _wiki_behind_with_speaking_stub(tmp_path, extra_shell="sleep 1.3; ")
    v, said = _run(ctx, repo, documentation=True, reset=False)
    assert v.fresh
    assert not any(s.startswith("· ") for s in said)
    painted = out.getvalue()
    assert "\r[" in painted and "%" in painted  # an in-place bar redraw
    assert painted.endswith("\r\x1b[K")  # cleared before narration resumed


def test_wiki_failure_excerpt_is_the_tail_where_the_error_is(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    w = repo / ".gitnexus" / "wiki"
    w.mkdir()
    (w / "meta.json").write_text(json.dumps({"fromCommit": "b" * 40}))
    ctx, _ = _ctx_with_node_stub(tmp_path, tmp_path / "home")
    node = tmp_path / "bin" / "node"
    arm = '*" wiki --provider"*) '
    noise = "x" * 500 + " LLM API error: boom"
    node.write_text(node.read_text().replace(arm, f"{arm}printf '%s' '{noise}' >&2; exit 1; "))
    v, said = _run(ctx, repo, documentation=True, reset=False)
    assert not v.fresh
    failed = next(s for s in said if s.startswith("✗ wiki failed: "))
    assert "LLM API error: boom" in failed
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_gitnexus_refresh.py -k "off_tty_narrates or on_tty_drives or excerpt_is_the_tail" -v`
Expected: FAIL — narration test: `"· Grouping files into modules (LLM)..." in said` is False; TTY test: `AttributeError: module 'omc.gitnexus' has no attribute 'BarThread'` (monkeypatch refuses to set a missing attribute); tail test: `"LLM API error: boom" in failed` is False.

- [ ] **Step 3: Implement `BarThread.enabled`**

In `src/omc/cli/progress_bar.py`, inside `BarThread` after `__init__`:
```python
    @property
    def enabled(self) -> bool:
        """True when the output stream is a TTY and redraws will happen.
        Callers that narrate instead of drawing off a TTY key off this."""
        return self._enabled
```

- [ ] **Step 4: Implement `_run_wiki`**

In `src/omc/gitnexus.py`:

(a) Imports:
```python
from .cli.progress_bar import BarThread
from .wikirun import _WIKI_POLL_SECONDS, _WIKI_STALL_SECONDS, GitNexusProgress, PageCountTracker
```
(`gitnexus.py` importing `omc.cli.progress_bar` is safe — `buildprogress.py` already does; the cycle exists only from `wikirun.py`, see Task 6.)

(b) Replace the body of `_run_wiki` from `tracker = PageCountTracker(...)` to the end with:
```python
    tracker = PageCountTracker(root / ".gitnexus" / "wiki")
    gn = GitNexusProgress()
    bar = BarThread(gn)  # TTY-gated: constructing on a piped stderr yields a no-op
    narrated = ""

    def on_line(line: str) -> None:
        # Runs on run_supervised's reader thread. Off a TTY the bar is silent,
        # so narrate GitNexus's phase changes instead (a silent minute is a
        # bug; depwatch narrates headless runs the same way). Heartbeats repeat
        # the phase and are not news; an unchanged detail is not either.
        nonlocal narrated
        gn.feed(line)
        if bar.enabled or gn.phase in ("", "heartbeat") or gn.detail == narrated:
            return
        narrated = gn.detail
        say(f"· {gn.detail}")

    bar.start()
    try:
        cp, stalled = ctx.run_supervised(
            gitnexus_argv(ctx, *run.wiki_args),
            cwd=str(root),
            heartbeat=tracker.beat,
            stall_after=_WIKI_STALL_SECONDS,
            poll=_WIKI_POLL_SECONDS,
            extra_env=run.extra_env,  # the key reaches exactly this node child
            on_line=on_line,
        )
    finally:
        bar.stop()  # clear the bar line before any ✓/✗ narration
    if stalled:
        say(f"✗ wiki stalled — no progress for {int(_WIKI_STALL_SECONDS)}s; killed")
        return False
    if cp.returncode != 0:
        # redact BEFORE truncating: a key cut in half still leaks its prefix.
        # Tail, not head: GitNexus prints the error after its pino records and
        # progress lines.
        say(f"✗ wiki failed: {run.redact((cp.stderr or cp.stdout or '').strip())[-400:]}")
    return True  # the recomputed verdict, not the exit code, decides
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_gitnexus_refresh.py tests/unit/test_watch.py -v`
Expected: PASS, including the pre-existing `test_wiki_api_failure_tail_is_redacted_before_truncation` and every `test_watch.py` documentation test (the stubs there print no progress lines, so nothing is narrated and nothing changes for them).

- [ ] **Step 6: Gate and commit**

Run: `just check`
Expected: exit 0.

```bash
git add src/omc/cli/progress_bar.py src/omc/gitnexus.py tests/unit/test_gitnexus_refresh.py
git commit -m "$(cat <<'EOF'
feat(gitnexus): project-wiki progress — bar on a TTY, phase narration off it

_run_wiki feeds GitNexus's stderr lines into GitNexusProgress and drives the
existing TTY-gated BarThread; when stderr is piped it narrates each phase
change as a `·` line instead (heartbeats excluded). Failure excerpt is now
the tail, where the error is.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Real-tool E2E asserts a GitNexus phase line reached omc

**Model:** standard coding tier

**Files:**
- Modify: `tests/e2e/test_e2e_gitnexus.py` (`test_document_api_backend_generates_wiki_docs`, after the `→ regenerating documentation via` assertion near line 208)

**Interfaces:**
- Consumes: the non-TTY narration from Task 9 and the fork's progress lines (Tasks 2–3), through the real GitNexus baked into the E2E image at `GITNEXUS_REF` (Task 11).

- [ ] **Step 1: Verify the marker situation**

Run: `grep -n "expensive" tests/e2e/test_e2e_gitnexus.py pyproject.toml`
Expected: only the marker's definition in `pyproject.toml`; nothing in the test file. So this test runs under `just e2e-tests` (`-m "e2e and not expensive"`) and in CI's `e2e.yml`. Record that in the commit message.

- [ ] **Step 2: Add the assertion**

After `assert "→ regenerating documentation via claude api (claude-sonnet-" in _redacted(out)`:
```python
    # GitNexus's progress lines flowed through run_supervised's on_line into
    # omc's narration: off a TTY the refresh narrates each GitNexus phase change
    # as a `·` line (spec 2026-10-01 fix-doc-false-stall §4.2.5). The final
    # phase is deterministic, so assert on it rather than on a module name.
    assert "· Wiki generation complete" in _redacted(out), _redacted(out)[:2000]
```

- [ ] **Step 3: Confirm the file still imports and collects**

Run: `uv run pytest tests/e2e/test_e2e_gitnexus.py --collect-only -q`
Expected: the test is listed; no errors. (Running it needs Docker and `ANTHROPIC_API_KEY` in `.env` — that is `/omc:verify`'s job at finish time, not this task's.)

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_e2e_gitnexus.py
git commit -m "$(cat <<'EOF'
test(e2e): api-backend documentation run narrates a GitNexus phase line

Pins reporter → on_line → narration end to end on the real tool with no
extra LLM run. The test carries no `expensive` marker, so it runs in CI.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Pin the E2E image to the merged fork commit

**Model:** standard coding tier

**Files:**
- Modify: `docker/Dockerfile.e2e` (lines ~48–50: the comment and `ARG GITNEXUS_REF=…`)

**Interfaces:**
- Consumes: the fork PR URL from `$SCRATCH/gitnexus-pr.txt` (Task 4).

- [ ] **Step 1: Check whether the fork PR has merged**

```bash
PR=$(cat "$SCRATCH/gitnexus-pr.txt")
gh pr view "$PR" --json state,mergeCommit -q '.state + " " + (.mergeCommit.oid // "")'
```

- [ ] **Step 2a: If the output starts with `MERGED <sha>` — bump the pin**

Edit `docker/Dockerfile.e2e`:
```dockerfile
# The fork commit the E2E image indexes with — must carry the wiki-regroup and
# copied-index fixes (chris-husse/GitNexus PR #4) and the piped progress lines
# + LLM heartbeat (PR for fix-doc-false-stall-gitnexus-progress) this suite
# relies on.
ARG GITNEXUS_REF=<the full 40-char merge sha>
```
Then:
```bash
git add docker/Dockerfile.e2e
git commit -m "$(cat <<'EOF'
build(e2e): bake the GitNexus fork at the progress-lines merge commit

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 2b: If the output is anything else (`OPEN`, `CLOSED`, empty) — do NOT edit the Dockerfile**

Pointing `GITNEXUS_REF` at a branch head is not acceptable on main. Leave the file untouched and report to the conductor, verbatim:

> Fork PR `<url>` is not merged. `docker/Dockerfile.e2e` still pins `8de99dc9`, so the E2E image will test the old GitNexus and Task 10's assertion will fail there until the pin is bumped. This is a follow-up that needs the user's go-ahead: merge the fork PR, then bump `ARG GITNEXUS_REF` to its merge commit.

The conductor surfaces this to the user; the omc branch can still be finished with the follow-up stated. Nothing else in this plan depends on it.

---

### Task 12: Build ledger entry

**Model:** standard coding tier

**Files:**
- Modify: `.superpowers/sdd/progress.md` (append)

- [ ] **Step 1: Append one ledger paragraph**

```markdown
FEATURE fix-doc-false-stall-gitnexus-progress (2026-10-01): false stall diagnosed as per-PHASE silence (grouping = many sequential LLM calls, nothing on disk, cli-progress silent off-TTY), not slow models. Two repos: fork GitNexus prints `GITNEXUS_PROGRESS {…}` lines on stderr when piped + heartbeats every 30s inside `invokeLLM`; omc `run_supervised` gains `on_line`, `wikirun.GitNexusProgress` holds GitNexus's percent (lazy render import — top-level is a cycle via omc.cli), `--timeout 300` from the shared `LLM_CALL_SECONDS`, run_document reports GitNexus's percent once it speaks, _run_wiki drives BarThread on a TTY and narrates `·` phase lines off it, failure excerpts are tails. Fork PR: <url from gitnexus-pr.txt>. GITNEXUS_REF bump: <done at <sha> | FOLLOW-UP pending fork merge>. Live acceptance (hummingbird api+opus; `omc dependency document`) pending `omc update` on the host.
```
Fill the two `<…>` from Tasks 4 and 11 — never leave the angle brackets in the file.

- [ ] **Step 2: Gate and commit**

Run: `just check`
Expected: exit 0.

```bash
git add .superpowers/sdd/progress.md
git commit -m "$(cat <<'EOF'
chore(ledger): record the false-stall fix build

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

## Not in this plan

- Running `/omc:finish` (rebase, squash, stages, push, PR) — the `/omc:implement` conductor does that after Task 12.
- Running the Docker E2E suite — `/omc:finish`'s verify stage.
- `omc update` on the host and the live acceptance run on hummingbird — the user runs both after the omc PR merges (and after Ctrl-C on the stale Sep 30 `omc watch` in the oh-my-clanker primary).
