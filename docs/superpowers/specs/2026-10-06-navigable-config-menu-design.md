# Navigable, schema-driven `omc configure` menu

Date: 2026-10-06 · Branch: `feature/navigable-config-menu` · Status: design record

Supersedes the interactive-flow parts of
`2026-07-23-make-omc-configure-project-local-design.md` ("`omc configure`
flow", interactive bullets) and
`2026-10-01-configure-claude-api-key-1password-design.md` (§3.3). Everything
those records say about `--set`, `--defaults`, validation, probes, secrets and
file layout stays in force.

Rebase integration (2026-10-06): main retired custom notification backends.
The menu exposes `ProviderConfig.notifications` as Native notifications under
each provider, preserving main's default of true; no global Notifications
section is retained.

## 1. Problem and goal

`omc configure` without flags walks a fixed questionnaire
(`configure._walkthrough_global`, `_walkthrough_project`): provider checkbox,
one model pick per provider, default provider, documentation provider,
backend, API key, documentation model, notifications on/off, notification
backend, branch prefix, base branch. Every question fires on every run. To
change one value the user confirms ten others, and the two documentation
probes run even when nothing about documentation changed.

Goal: a menu built from the configuration schema itself. Each field has its
nested place; the menu line shows the current value; `Enter` on a field edits
that field only, with the current value as the default, and returns to the
menu; `Esc` goes one level up and, at the root, leaves. The dataclasses in
`src/omc/config/schema.py` remain the single source of truth — the menu is
derived from them, never a second description of the config. Scripted use
(`--defaults`, `--set KEY=VALUE`) is untouched.

## 2. Decisions taken during brainstorm

| # | Decision | Reason |
|---|----------|--------|
| 1 | Each field is **saved immediately** after its edit, into the file that owns it | User: no accumulate-and-confirm; "simple and reliable" over optimized |
| 2 | The documentation **probes run on field change**, inside that field's validation | User: probe on change; a failed probe must never keep a failed value (2026-10-01 §3.3) |
| 3 | **Menu always**, including first run on an empty config, defaults filled in | User; the dataclass defaults are the sane defaults |
| 4 | **No second source of truth**: the schema dataclasses build the menu; labels and help are declared on the fields, clap-derive style; an existing library renders them | User: "search for existing solutions instead of rolling your own" |
| 5 | Library: **mininterface**, bare install (text interface only) | Only schema-driven terminal form found; its text interface is exactly the asked-for menu; bare install adds two small pure-Python packages |
| 6 | Providers are listed individually under LLM, each with its own **configured toggle**; no multi-select leaf | User: "multi-select leafs are weird UX" |
| 7 | **Esc** goes one level up; **Ctrl-C** aborts; Backspace only if the menu library allows it — it does not (§6), so `Esc` only | User accepted Esc-only as the fallback |
| 8 | Leaving the menu on a machine with no global config yet **writes the defaults** | User; otherwise `omc design` keeps refusing after a first run that changed nothing |
| 9 | Non-TTY stdin without flags keeps today's `Refusal` (rc 2) | CLI-first contract; scripts use `--set`/`--defaults` |

## 3. Approach: labels on the schema, rendered by mininterface

The config dataclasses carry their human-facing metadata on the field itself,
through the standard library's designated extension point for exactly this,
`dataclasses.field(metadata=...)`, next to the existing default:

```python
@dataclass
class ProviderConfig:
    notifications: bool = field(
        default=True,
        metadata={"label": "Native notifications", "help": "Use this provider's native session alerts."},
    )
```

Two metadata keys, `label` and `help`, nothing else. The schema module stays
import-free, which matters: `schema.py` is imported by `cli/__init__.py` on
every `omc` invocation, including `omc title` on every fish prompt (about
110 ms wall today). Importing mininterface's `Tag` there would cost a measured
14 ms per prompt for the sake of labels — so the library is imported only by
the configure path. `metadata` is invisible to everything that reads the
schema today: `fields()`, `asdict()`, the strict loader `store._hydrate` and
the YAML round trip see the same fields, types and defaults.

mininterface (CZ.NIC, PyPI `mininterface` 1.4.0, LGPL-3.0-or-later, Python
3.10–3.14 declared) renders a form as a terminal menu. Installed bare it
depends on `annotated-types` and `simple-term-menu` (MIT, zero dependencies,
macOS and Linux). The `basic`/`tui`/`gui` extras (Textual, tyro, tkinter
helpers) are not installed. OSV lists no vulnerabilities for any of the three
packages. `simple-term-menu` has had no release since 2024-12 and is accepted
as mature rather than abandoned.

Its text interface renders one line per field — `label: value | help` — and
nested dicts as submenus with an appended "ok" entry. `Enter` on a field asks
for that field alone (text input showing the current value, `[y]/n` for
booleans, an options menu for closed sets, masked input for secrets) and
returns to the menu. `Esc` inside a submenu returns to the parent; at the root
it ends the form. omc contributes the field metadata, the composition of the
menu (§4) and one per-field hook (§5); mininterface contributes everything the
user sees and types into.

The interface is constructed explicitly with
`mininterface.interfaces.get_interface("text", settings=...)`, never through
`mininterface.run`, so argv and the `MININTERFACE_INTERFACE` variable are not
consulted. Verified during design: the text interface requires a controlling
terminal (it opens `/dev/tty` when stdin is redirected) and raises
`InterfaceNotAvailable` otherwise; omc maps that onto the existing non-TTY
`Refusal`. Also verified: asking `run()` for `"text"` without a TTY silently
hands back the base interface, which auto-confirms every question — one more
reason to construct the class directly.

Implementation note (audit 2026-10-06). Four of the library behaviours
above did not hold in mininterface 1.4.0 and are bridged by small adapters in
`configure.py`, all library-facing and none visible to the user: the text
interface's `SecretTag` input echoes, so secrets are read through `getpass`
with the masked value shown only on the menu line; `SelectTag` validation
skips the per-field callback, so a select subclass runs it; the renderer
replays each accepted edit once through `update`, so the replay is consumed
instead of re-applying the edit; the text reader stalls on complete lines
and treats a lost terminal as a dismissed menu, so stdin is wrapped in an
unbuffered one-byte reader for the session and a lost terminal becomes a
`Refusal` ("lost its terminal", rc 2). `get_interface("text")` also hands
back the base auto-confirming interface without a controlling terminal, so
the returned type is checked and mapped onto the non-TTY `Refusal`.

Alternatives rejected: a hand-written walker over `dataclasses.fields()`
driving questionary with custom Esc bindings (rolling our own, Decision 4);
Textual-based YAML editors (`configui`, `ConfigTUI`, `pymenu-cli`) which edit
raw files rather than a schema; `tyro`/`simple-parsing` which derive CLIs,
not menus.

## 4. Menu composition

mininterface builds a form either from one dataclass instance or from a dict
whose values are `Tag`s or nested dicts. Verified during design: a dataclass
instance placed inside a dict root becomes a single opaque value, not a
submenu. omc's config is three objects (global, project, secrets) plus
runtime facts — the provider set comes from `providers.registry`, the `api`
documentation backend exists only for providers with an API base URL, the API
key lives in `SecretsConfig` — so the root is composed as a dict of dicts of
`Tag`s by one function in `configure.py`:

- For each schema section it iterates `dataclasses.fields()` and makes one
  `Tag` per field: value from the loaded object, `label` and `help` from the
  field metadata, the per-field hook from §5 keyed by the field's dotted config
  path (the same key `--set` accepts). This is the whole mapping; there is no
  per-field code.
- Closed sets become option menus (`SelectTag`) whose options are computed at
  build time: default provider and documentation provider offer the
  configured providers; documentation backend offers `cli`, plus `api` when
  the documentation provider has an API base URL; session and documentation
  model offer the provider's known models plus "other (type a model id)", as
  the walkthrough does today.
- The API key is a masked `SecretTag` shown through `docsllm.mask_key`
  (asterisks plus the last four characters); the value itself is never on the
  screen.

| Menu path | Backed by | Leaves |
|---|---|---|
| LLM | `GlobalConfig.llm` | default provider; one submenu per registry provider |
| LLM › `<provider>` | `ProviderConfig` entry + `SecretsConfig.api_keys[name]` | configured (yes/no); session model; native notifications; documentation model; API key (only for providers with an API base URL) |
| Documentation | `LLMConfig.docs` | provider (blank = default provider); backend |
| Worktree (project) | `ProjectConfig.worktree` | branch prefix; base branch — present only inside a git repository |

The **configured toggle** replaces today's provider checkbox and is a plain
yes/no leaf: on creates `llm.providers[name] = ProviderConfig()` (what `--set
llm.providers.<name>.model=` does today); off removes the entry. No extra rule
guards removal: a default or documentation provider without an entry is a
valid state today (the loader checks provider names against the registry, not
against the entries, and a unit test asserts it), and the save-time validation
stays the only gate, as for `--set`. Removal is the one operation the store
does not have yet and is added beside `set_key`. A provider's other leaves
are always shown; editing one of them on an unconfigured provider configures
it implicitly, and the configured line reads yes when the user returns to the
submenu. For the model leaves this is exactly what `--set` does; for the API
key leaf, which `--set` routes to secrets only, the menu additionally creates
the provider entry in the same transaction (global and secrets written
together), so a key typed under a provider never lands on a provider that is
not configured. A key edit on an already configured provider stays
secrets-only. The menu is never rebuilt mid-session.

## 5. Field lifecycle: one hook, the `--set` pipeline

The `--set` branch of `run_configure` already does, per key: routing to the
owning file (`worktree.*` → project, `llm.providers.<name>.api_key` → secrets,
else global), mutation through `store.set_key`, the cross-field
`validate_llm`, `_probe_docs` against the pre-change config (which probes only
when the documentation tuple or the key changed), secrets-first writes, and
the `Saved …` lines. It is unit-tested in `tests/unit/test_configure.py`. That
branch is extracted, as a pure move, into a function that applies a list of
`KEY=VALUE` pairs and returns which files it wrote; `run_configure --set` calls
it and then runs the post-steps exactly as before.

The menu's per-field hook is that function applied to **one pair**. It is
installed as the `Tag`'s validation callback, which mininterface runs before
it writes the accepted value into the `Tag`, so the sequence per edit is:

1. The user types a value. Blank means "keep the current value" (mininterface
   submits the empty string; the hook returns the current value unchanged —
   this also preserves the key prompt's "blank keeps" semantics).
2. If the value equals what is persisted, the hook is a no-op. This matters
   because mininterface runs the validation twice per edit and once more for
   every field of a level when the user leaves that level; unchanged values
   must cost nothing and never re-probe.
3. Otherwise the pair is applied: validate → probe when the documentation
   tuple or key changed → write the owning file → `Saved <path>`. A
   `ConfigError`/`Refusal`/`ProbeFailed` becomes the validation message:
   mininterface prints it and asks the field again, the stored value is
   untouched, so a failed probe never keeps a failed value. `Ctrl-C` during the
   edit abandons it; the field then shows a `*` marker and the message in the
   menu until it is next edited successfully (library behaviour, accepted).
4. The value the hook returns is what mininterface writes into the `Tag` and
   shows in the menu — for an `api` documentation model that is the resolved
   full model id, exactly what `--set` stores today.

The `before` config for the probe trigger is the last persisted state, kept by
the hook, so a session that edits several documentation fields probes once per
changed field and never for unchanged ones. Because every edit is one write,
there is no batching and no ordering logic. The one cross-field rule that
depended on write order — `backend: api` without a key — is refused by the
existing api-without-key check with a message telling the user to set the API
key first.

Errors never travel through the library: the hook catches omc's own
exceptions and returns their message, because `cli.main`'s single error
boundary catches `OmcError` only and an exception raised inside mininterface's
validation would surface as a traceback. Menu and prompts are terminal I/O on
stdout, where configure already prints its `Saved …` lines (it has no
machine-readable stdout); probe narration keeps its `→`/`✓` lines on stderr.
Nothing in this flow prints an API key, a stack trace or a file's secret
content (2026-10-01 §3.4 continues to apply).

## 6. Session lifecycle: entry, exit, keys

**Entry.** `run_configure` without flags: non-TTY → today's `Refusal`.
Otherwise load global (or legacy JSON, or defaults), secrets, and the project
config when inside a repository; compose the root (§4); construct the text
interface (`InterfaceNotAvailable` → the same `Refusal`); run the form.
mininterface's `Cancelled` at the root (a `SystemExit` subclass) is caught: it
means "leave", not "abort".

**Exit.** Whatever path left the root — `Esc`, the "ok" entry, or `Ctrl-C`
in a menu — the post-steps run once, as today: `_migrate_legacy` when the
global file was written during the session (the hook reports which files it
wrote; the flags accumulate), `_ensure_instructions`, `_ensure_plugins`, then
`_PLUGIN_HINTS`. If no global config file exists at exit (first run, nothing
edited), the in-memory defaults are written first (Decision 8); the project
file is seeded likewise when inside a repository and absent, mirroring
`--defaults`.

**Terminal loss.** EOF or EIO on the controlling terminal (settled during
implementation) is not an exit: it raises the `Refusal` "interactive
configure lost its terminal" (rc 2) and the post-steps do not run; saved
edits stay saved, and no defaults are written on a first run.

**Keys.** `Esc` = one level up (root: leave). `Enter` = edit / confirm; on a
menu it selects the pointed entry, on the "ok" entry it leaves the level.
`Ctrl-C` inside an edit = abandon that edit; in a menu = leave (saved edits
stay saved — there is nothing unsaved by construction). Verified during
design: mininterface does not pass quit keys through to `simple-term-menu`, so
Backspace cannot be bound without a library change; `Esc` only (Decision 7),
with an upstream request as a possible follow-up. Boolean fields are asked
as `[y]/n` with yes as the Enter default regardless of the current value
(library behaviour, accepted; the menu line shows the result at once).

## 7. What stays, what goes

Unchanged: `--defaults`, `--set KEY=VALUE` and their routing, every
validator and save function in `config/store.py`, every probe in `docsllm.py`,
legacy migration, instruction-section refresh, plugin install, hints, and the
two secrets rules (never in `config.yaml`, never echoed).

Removed: `_walkthrough_global` and `_walkthrough_project` (about 200 lines).
`configure.py` is the only user of questionary (verified), so questionary and
its prompt-toolkit dependency leave `pyproject.toml` in the same change.

Added: `mininterface` (bare) and, transitively, `simple-term-menu` and
`annotated-types`; `label`/`help` metadata on the schema fields; the
composition function, the single-pair hook and the provider-removal operation.
Net dependency count is unchanged: two in, two out. (The E2E image id is a
content hash of the whole working tree, so this change re-keys it like any
other; the lockfile is not special.)

One test rides on questionary's presence for a different reason:
`tests/e2e/test_e2e_dependency.py` uses it as the sample runtime dependency
that `/omc:explain-dependency` must resolve from `pyproject.toml`, and its
judge rubric names questionary and its `select` prompt. That fixture moves to
mininterface (git-hosted at github.com/CZ-NIC/mininterface) with a matching
question and rubric; the test's purpose — resolve a declared dependency,
index it, answer from its graph — is unchanged.

## 8. Testing

The text interface needs a controlling terminal, so the menu loop is driven
under a pty on the pattern of `tests/unit/_fishpty.py` (that helper itself is
fish-specific; the menu test gets its own small driver that sends each line
only after the next prompt is visible), with mininterface's
`TextSettings(plain_menu=True)` so the menu is a deterministic numbered list
read line by line instead of `simple-term-menu`'s redrawn screen. Verified
during design: this works and renders `label: value | help` lines, `[y]/n`
prompts and the appended `[0] ok`. Input must be sent only after a prompt is
visible — the interface reads the raw file descriptor and drops typed-ahead
bytes.

Unit tests, independent of the renderer:

- Composition: one `Tag` per schema field with the metadata label and help; a
  provider without an API base URL has no API key leaf and no `api` backend
  choice; the worktree section appears only inside a repository;
  default-provider choices equal the configured providers.
- Configured toggle: on creates the entry; off removes it and the file no
  longer lists the provider.
- Hook: unchanged value is a no-op (no validator, no probe, no write); blank
  keeps the current value; a changed value writes exactly the owning file; the
  API key goes to `secrets.yaml` with mode 0600; a probe runs only for the
  documentation tuple and the key; a failed probe leaves the stored value
  unchanged and returns the message; a resolved model id is returned and
  stored.
- The extracted `--set` application function keeps every existing `--set`
  test green unchanged.
- Exit: defaults written when no global file existed; post-steps run once.
- Non-TTY: `Refusal` rc 2, unchanged.
- `test_configure_interactive_mode_writes_global_sections` stops patching
  `_walkthrough_global` and patches the menu runner instead; its assertion
  (global instruction sections written after an interactive run) is unchanged.

One pty test drives the real menu end to end on a temporary home: open,
descend into Worktree, change the base branch, leave, and assert the file on
disk. Per the project's testing policy it never skips: a machine without a
usable pty fails the test naming the prerequisite. E2E container tests use `--set` only and keep passing; no new E2E stage.

## 9. Documentation

- README `omc configure` paragraph: describe the menu (navigate, `Enter` to
  edit one field, `Esc` back/leave, every edit saved at once) and keep the
  scripted equivalents.
- The earlier records are left as they are; the "Supersedes" line at the top
  of this one is the pointer.
- `.omc/docs/gitnexus/docs/src.md` is regenerated by `/omc:document` as usual.

## 10. Verified during design, and what remains open

Checked against mininterface 1.4.0 source and a scratch install:

- A dict root with nested dicts of `Tag`s renders as nested menus; a
  dataclass instance inside a dict root does **not** become a submenu — hence
  the composition in §4.
- `get_interface("text", settings)` constructs the text interface without
  argv or environment; it needs a controlling terminal; `run()` must not be
  used.
- Validation runs before write-back and receives the candidate value; it may
  return a replacement value; it runs twice per edit and once per field on
  leaving a level — hence the no-op rule in §5.
- Blank input submits the empty string, not the current value — hence step 1
  of §5.
- `Ctrl-C` in an edit abandons the edit and returns to the menu.
- Backspace is not bindable through mininterface; `Esc` cancels one level
  in both the `simple-term-menu` menu and the plain fallback's raw reader
  (the fallback also accepts `Enter`/`0`/`ok` on the "ok" entry).
- Import cost of mininterface's `Tag`: about 14 ms; kept off the schema module.

Settled during implementation (both were open here): the `simple-term-menu`
renderer is driven under a pty as well as the plain menu
(`tests/unit/test_configure_pty.py`); EOF or EIO at any level is a `Refusal`
(§6), not an exit.

## 11. Risks

- **License.** mininterface is LGPL-3.0-or-later inside an MIT tool. omc
  imports it unmodified, which the LGPL permits; the record states it so the
  choice is visible.
- **Dependency vitality.** `simple-term-menu` is quiet. It is small and pure
  Python; if it breaks on a future Python, replacing mininterface's menu
  backend is the library's concern, and omc's fallback is the hand-written
  walker rejected in §3.
- **Slow probes inside validation.** A documentation edit blocks on a network
  or CLI probe before the menu returns. Accepted by Decision 2; the probe
  prints its `→ checking …` lines as today so the wait is visible.
- **Immediate save of half-finished intent.** Toggling a provider on writes
  a provider entry with blank models at once. That state is valid (blank =
  provider default) and is what `--set llm.providers.codex.model=` produces
  today.
- **Library UX quirks we inherit.** Yes-default on booleans, the `*` marker
  after an abandoned edit, no Backspace. All visible, none unsafe.

## Implementation review

Auditor: Claude Code (Fable 5.1) · 2026-10-06 · branch diff against `origin/main`.

| Severity | Kind | Record section | Location | Disposition |
|---|---|---|---|---|
| Important | record is stale | §3, §5, §6, §10, Deliberate complexity | `src/omc/configure.py:310-361` (tag subclasses), `:574-594` (getpass adaptor), `:617-643` (unbuffered stdin), `:660-677` (terminal loss) | Record amended: §3 implementation note names the five adapters, §6 states terminal loss is a `Refusal`, §10 marks both open items settled, Deliberate complexity lists the adapters. Decisions 4 and 5 untouched. |
| Minor | record is stale | §4 (configured-toggle paragraph) | `src/omc/configure.py:422-425` | Record amended: the API key leaf on an unconfigured provider also creates the provider entry; `--set` alone does not. |
| Minor | record is stale | §4 ("added beside `set_key`") | `src/omc/config/store.py:188` | Listed only: `remove_provider` sits beside `set_api_key`, two functions below `set_key`. |

Everything else conforms: decisions 1–9, the §4 menu table, the §5 hook
sequence, the §6 entry/exit flow, the §7 dependency swap (lockfile holds
`mininterface`, `simple-term-menu`, `annotated-types`; no `questionary`,
`prompt-toolkit`, Textual or tyro), the §8 test list including the pty test
on the project base branch, and the §9 README paragraph.

## Deliberate complexity

The library adapters listed in the §3 implementation note (secret input via
`getpass`, select validation bridge, replay consumption, unbuffered stdin
reader, lost-terminal translation). Each exists because a mininterface 1.4.0
behaviour verified during design did not hold for the omc use; each is a few
lines against a documented library hook, is covered by a renderer-level test,
and is the price of Decision 4 (use the existing library) over the rejected
hand-written walker. A mininterface upgrade is the moment to delete any that
upstream has made unnecessary.
