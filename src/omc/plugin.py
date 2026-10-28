"""Plugin self-heal: the seeded /omc:* commands must actually resolve.

`omc design` seeds the session with `/omc:start`; if the omc plugin is missing
— or installed but refused by Claude ("failed to load") — the session opens on
"Unknown command", the worst possible first-run. So start/update/configure
ensure the plugin is installed AND healthy, installing or reinstalling it
automatically. No consent prompt: the plugin is omc's own pinned repo, the
same trust model as the CLI install itself (chicken precedent for
pinned-source dependencies).

superpowers is handled here too. omc's start skill hands off to it, but the
manifest deliberately declares NO plugin dependency: Claude Code resolves a
dependency by its exact marketplace-qualified id, so a manifest pinned to
`superpowers@superpowers-marketplace` made Claude refuse to load omc on every
machine whose superpowers came from `claude-plugins-official` — and Claude
never installs a dependency for you anyway. Verified 2026-09-02 against
claude 2.1.x: with the dependency dropped and superpowers installed from the
official marketplace, `claude plugin list --json` reports omc with no errors.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import OmcError
from .installsrc import install_source
from .providers.codex import CodexProvider
from .toolctx import ToolContext
from .wtconfig import repo_root

PLUGIN_REF = "omc@oh-my-clanker"
MARKETPLACE_NAME = "oh-my-clanker"
SUPERPOWERS_REF = "superpowers@claude-plugins-official"
_OFFICIAL_MARKETPLACE = "anthropics/claude-plugins-official"
_FALLBACK_SOURCE = "chris-husse/oh-my-clanker"
_CODEX_SKILLS = "./skills/"


def marketplace_source(env) -> str:
    """Where `claude plugin marketplace add` should pull omc from: the same
    place uv installed the CLI from — a directory checkout stays local, a
    GitHub install uses owner/repo, anything else falls back to the canonical
    public repo."""
    src, is_remote = install_source(env)
    if is_remote:
        m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", src)
        if m:
            return m.group(1)
    elif src != "unknown" and not src.endswith("(PyPI)"):
        return src
    return _FALLBACK_SOURCE


def _say(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


def _list_plugins(ctx: ToolContext) -> list[dict]:
    """`claude plugin list --json`: one entry per installed plugin with ``id``,
    ``enabled`` and — for a plugin Claude refused to load — an ``errors`` list.
    The human listing only carries the same facts as prose; the JSON is the
    contract (verified 2026-09-02, claude 2.1.x)."""
    argv = ["claude", "plugin", "list", "--json"]
    try:
        cp = ctx.run(argv)
    except OSError as exc:
        raise OmcError(f"could not run `{' '.join(argv)}`: {exc}") from exc
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise OmcError(f"`{' '.join(argv)}` failed (exit {cp.returncode}): {detail}")
    try:
        data = json.loads(cp.stdout or "")
    except json.JSONDecodeError as exc:
        raise OmcError(
            f"could not parse the output of `{' '.join(argv)}`: {exc}\n"
            f"  output was: {(cp.stdout or '').strip()[:200]!r}"
        ) from exc
    if not isinstance(data, list):
        raise OmcError(f"unexpected `{' '.join(argv)}` output (expected a JSON array)")
    return [e for e in data if isinstance(e, dict)]


def _find(entries: list[dict], name: str) -> dict | None:
    """The installed plugin named ``name`` from ANY marketplace (ids are
    ``name@marketplace``)."""
    return next((e for e in entries if str(e.get("id", "")).startswith(name + "@")), None)


def _problems(entry: dict) -> list[str]:
    """Why Claude won't serve this plugin's skills: load errors, or disabled."""
    problems = [str(e) for e in (entry.get("errors") or [])]
    if entry.get("enabled") is False:
        problems.append("the plugin is disabled")
    return problems


def _install(ctx: ToolContext, ref: str, *, manual_fix: str) -> None:
    cp = ctx.run(["claude", "plugin", "install", ref, "--scope", "user"])
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise OmcError(
            f"could not install {ref} for claude automatically.\n"
            f"  {detail}\n"
            f"  fix manually: {manual_fix}"
        )


def _checked(ctx: ToolContext, argv: list[str]) -> str:
    try:
        cp = ctx.run(argv)
    except OSError as exc:
        raise OmcError(f"could not run `{shlex.join(argv)}`: {exc}") from exc
    if cp.returncode:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise OmcError(f"`{shlex.join(argv)}` failed (exit {cp.returncode}): {detail}")
    return cp.stdout or ""


def _claude_json(ctx: ToolContext, argv: list[str]):
    output = _checked(ctx, argv)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise OmcError(f"could not parse `{shlex.join(argv)}` output as JSON") from exc


def _codex_json(ctx: ToolContext, argv: list[str]) -> dict:
    output = _checked(ctx, argv)
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OmcError(f"could not parse `{shlex.join(argv)}` output as JSON") from exc
    if not isinstance(data, dict):
        raise OmcError(f"unexpected `{shlex.join(argv)}` output (expected a JSON object)")
    return data


def _codex_skill_files(plugin_root: Path) -> dict[Path, bytes]:
    """Read every skill and support file; reject incomplete or ambiguous trees."""
    skills_root = plugin_root / "skills"
    files: dict[Path, bytes] = {}

    def visit(directory: Path) -> None:
        try:
            children = sorted(directory.iterdir())
            for child in children:
                if child.is_symlink():
                    raise OmcError(f"Codex skill tree at {skills_root} contains a symlink: {child}")
                if child.is_dir():
                    visit(child)
                elif child.is_file():
                    files[child.relative_to(plugin_root)] = child.read_bytes()
                else:
                    raise OmcError(
                        f"Codex skill tree at {skills_root} contains a non-file: {child}"
                    )
        except OSError as exc:
            raise OmcError(f"cannot read Codex skill tree at {directory}: {exc}") from exc

    if skills_root.is_symlink():
        raise OmcError(f"Codex skill tree at {skills_root} is a symlink")
    visit(skills_root)
    return files


def _codex_resolve(path: Path, *, purpose: str, strict: bool = False) -> Path:
    try:
        return path.resolve(strict=strict)
    except (OSError, RuntimeError, ValueError) as exc:
        raise OmcError(f"cannot resolve Codex {purpose} at {path}: {exc}") from exc


def _codex_offer(root: Path) -> tuple[str, dict[Path, bytes]]:
    """Read the omc payload offered by a Codex marketplace root."""
    marketplace_manifest = root / ".claude-plugin" / "marketplace.json"
    try:
        marketplace = json.loads(marketplace_manifest.read_text())
    except (OSError, ValueError) as exc:
        raise OmcError(
            f"cannot read Codex marketplace manifest at {marketplace_manifest}: {exc}"
        ) from exc
    if not isinstance(marketplace, dict) or marketplace.get("name") != MARKETPLACE_NAME:
        raise OmcError(f"Codex marketplace at {root} is not {MARKETPLACE_NAME}")
    plugins = marketplace.get("plugins")
    offered = (
        next((p for p in plugins if isinstance(p, dict) and p.get("name") == "omc"), None)
        if isinstance(plugins, list)
        else None
    )
    if offered is None:
        raise OmcError(f"Codex marketplace at {root} does not offer {PLUGIN_REF}")
    relative = offered.get("source")
    if not isinstance(relative, str) or not relative:
        raise OmcError(f"Codex marketplace at {root} has no source for {PLUGIN_REF}")
    plugin_root = _codex_resolve(root / relative, purpose="plugin root")
    try:
        plugin_root.relative_to(_codex_resolve(root, purpose="marketplace root"))
    except ValueError as exc:
        raise OmcError(f"Codex marketplace at {root} points outside its root") from exc
    plugin_manifest = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(plugin_manifest.read_text())
    except (OSError, ValueError) as exc:
        raise OmcError(f"cannot read Codex plugin manifest at {plugin_manifest}: {exc}") from exc
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("name") != "omc"
        or manifest.get("skills") != _CODEX_SKILLS
        or not isinstance(version, str)
        or not version
        or Path(version).name != version
        or offered.get("version") != version
    ):
        raise OmcError(f"Codex plugin manifest at {plugin_manifest} disagrees with the marketplace")
    skills = _codex_skill_files(plugin_root)
    if not any(path.name == "SKILL.md" for path in skills):
        raise OmcError(f"Codex plugin at {plugin_root} offers no skills")
    return version, skills


def _codex_source_matches(entry: dict, source: str) -> bool:
    record = entry.get("marketplaceSource")
    if not isinstance(record, dict) or not isinstance(record.get("source"), str):
        return False
    observed = record["source"]
    if Path(source).is_absolute():
        return record.get("sourceType") == "local" and _codex_resolve(
            Path(observed), purpose="marketplace source"
        ) == _codex_resolve(Path(source), purpose="desired source")
    if observed == source:
        return True
    if not source.startswith(("https://", "http://", "ssh://", "git@")):
        return observed.removesuffix(".git") == f"https://github.com/{source}"
    return False


def _codex_registration(ctx: ToolContext, provider: CodexProvider, source: str) -> dict:
    data = _codex_json(ctx, provider.plugin_marketplace_list_argv())
    entries = data.get("marketplaces")
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise OmcError("Codex marketplace listing has no valid marketplaces array")
    matches = [entry for entry in entries if entry.get("name") == MARKETPLACE_NAME]
    if len(matches) != 1 or not _codex_source_matches(matches[0], source):
        raise OmcError(f"{MARKETPLACE_NAME} marketplace is not registered from {source}")
    root = matches[0].get("root")
    if not isinstance(root, str) or not root:
        raise OmcError(f"{MARKETPLACE_NAME} marketplace has no root")
    if Path(source).is_absolute() and (
        not Path(root).is_absolute()
        or _codex_resolve(Path(root), purpose="marketplace root")
        != _codex_resolve(Path(source), purpose="desired source")
    ):
        raise OmcError(f"{MARKETPLACE_NAME} marketplace root {root} differs from {source}")
    return matches[0]


def _codex_preflight(ctx: ToolContext, provider: CodexProvider, source: str) -> None:
    if Path(source).is_absolute():
        _codex_offer(Path(source))
        return
    # A remote replacement is fetched into a disposable Codex home before
    # removing the live registration, so an invalid desired source leaves the
    # installed plugin in place. Codex owns this scratch registry as well.
    try:
        with TemporaryDirectory(prefix="omc-codex-marketplace-") as scratch:
            codex_home = Path(scratch) / "codex"
            # Codex 0.156.1 refuses CODEX_HOME when the directory does not exist.
            codex_home.mkdir()
            # Keep the caller's HOME so Git sees its credential helpers and
            # URL rewrites; CODEX_HOME alone isolates Codex's scratch registry.
            env = {
                **ctx.child_env(),
                "CODEX_HOME": str(codex_home),
                "OMC_HOME": str(Path(scratch) / "omc"),
                "UV_TOOL_DIR": str(Path(scratch) / "uv"),
            }
            probe = ToolContext.from_env(env)
            added = _codex_json(probe, provider.plugin_marketplace_add_argv(source))
            if added.get("marketplaceName") != MARKETPLACE_NAME:
                raise OmcError(
                    f"desired Codex source {source} is not the {MARKETPLACE_NAME} marketplace"
                )
            registration = _codex_registration(probe, provider, source)
            _codex_offer(Path(registration["root"]))
    except OSError as exc:
        raise OmcError(f"Codex marketplace preflight scratch for {source} failed: {exc}") from exc


def _codex_verify_install(
    ctx: ToolContext,
    provider: CodexProvider,
    source: str,
    added: dict,
    version: str,
    skills: dict[Path, bytes],
) -> None:
    listing = _codex_json(ctx, provider.plugin_list_argv())
    entries = listing.get("installed")
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise OmcError("Codex plugin listing has no valid installed array")
    matches = [entry for entry in entries if entry.get("pluginId") == PLUGIN_REF]
    if (
        len(matches) != 1
        or matches[0].get("installed") is not True
        or matches[0].get("enabled") is not True
    ):
        raise OmcError(f"{PLUGIN_REF} is not installed and enabled exactly once")
    entry = matches[0]
    if (
        entry.get("version") != version
        or not _codex_source_matches(entry, source)
        or added.get("pluginId") != PLUGIN_REF
        or added.get("version") != version
    ):
        raise OmcError(f"{PLUGIN_REF} installation metadata is stale or mismatched")
    codex_home = Path(
        ctx.env.get("CODEX_HOME") or Path(ctx.env.get("HOME", "~")).expanduser() / ".codex"
    )
    expected = codex_home / "plugins" / "cache" / MARKETPLACE_NAME / "omc" / version
    raw_path = added.get("installedPath")
    if not isinstance(raw_path, str):
        raise OmcError(f"{PLUGIN_REF} add result has no installedPath")
    installed = _codex_resolve(Path(raw_path), purpose="installed artifact", strict=True)
    canonical = _codex_resolve(expected, purpose="canonical installed path", strict=True)
    cache = _codex_resolve(codex_home / "plugins" / "cache", purpose="installed cache", strict=True)
    if installed != canonical or not installed.is_dir() or not installed.is_relative_to(cache):
        raise OmcError(f"{PLUGIN_REF} installed artifact is outside its canonical Codex cache path")
    manifest_path = installed / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise OmcError(f"{PLUGIN_REF} installed manifest is unreadable: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("name") != "omc"
        or manifest.get("version") != version
        or manifest.get("skills") != _CODEX_SKILLS
    ):
        raise OmcError(f"{PLUGIN_REF} installed manifest is stale or mismatched")
    installed_files = _codex_skill_files(installed)
    missing = sorted(skills.keys() - installed_files.keys())
    extra = sorted(installed_files.keys() - skills.keys())
    if missing or extra:
        raise OmcError(
            f"{PLUGIN_REF} installed skill file set is stale: missing={missing}, extra={extra}"
        )
    for relative, expected_bytes in skills.items():
        if installed_files[relative] != expected_bytes:
            raise OmcError(f"{PLUGIN_REF} installed skill {relative} is stale")


def update_codex_plugin(ctx: ToolContext, source: str) -> str:
    """Reconcile Codex's omc registration and verify installed skill content."""
    provider = CodexProvider()
    try:
        _codex_preflight(ctx, provider, source)
        try:
            data = _codex_json(ctx, provider.plugin_marketplace_list_argv())
        except OmcError as initial_error:
            # Codex 0.158.0 cannot list a registered marketplace whose local
            # manifest vanished, but remove by known name still works.
            if MARKETPLACE_NAME not in str(initial_error):
                raise
            try:
                _codex_json(ctx, provider.plugin_marketplace_remove_argv())
            except OmcError as exc:
                raise OmcError(
                    f"initial Codex marketplace listing failed ({initial_error}); "
                    f"repair failed: {exc}"
                ) from exc
            current = None
        else:
            entries = data.get("marketplaces")
            if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
                raise OmcError("Codex marketplace listing has no valid marketplaces array")
            matches = [entry for entry in entries if entry.get("name") == MARKETPLACE_NAME]
            if len(matches) > 1:
                raise OmcError(f"Codex has multiple {MARKETPLACE_NAME} registrations")
            current = matches[0] if matches else None
            if current is not None and not _codex_source_matches(current, source):
                _codex_json(ctx, provider.plugin_marketplace_remove_argv())
                current = None
        if current is None:
            added = _codex_json(ctx, provider.plugin_marketplace_add_argv(source))
            if added.get("marketplaceName") != MARKETPLACE_NAME:
                raise OmcError(f"Codex registered the wrong marketplace from {source}")
        if not Path(source).is_absolute():
            _codex_json(ctx, provider.plugin_marketplace_upgrade_argv())
        registered = _codex_registration(ctx, provider, source)
        version, skills = _codex_offer(Path(registered["root"]))
        installed = _codex_json(ctx, provider.plugin_add_argv())
        _codex_verify_install(ctx, provider, source, installed, version, skills)
    except OmcError as exc:
        raise OmcError(f"Codex plugin update from {source}: {exc}") from exc
    return "updated and verified"


def _available(ctx: ToolContext) -> None:
    """Prove the marketplace offers omc — PROBE CONFIG ONLY.

    `plugin list --available --json` lists plugins that are NOT installed
    (claude 2.1.286, verified 2026-10-01: the installed set and the available
    set were disjoint for all eight installed plugins). Inside the isolated
    probe omc is not installed yet, so its presence here is a valid proof.
    On the live config, where omc IS installed, this list can never contain
    it — see _offered for the live-config proof.
    """
    data = _claude_json(ctx, ["claude", "plugin", "list", "--available", "--json"])
    available = data.get("available") if isinstance(data, dict) else None
    if not isinstance(available, list) or not any(
        isinstance(p, dict) and p.get("pluginId") == PLUGIN_REF for p in available
    ):
        raise OmcError(f"refusing plugin replacement: marketplace does not offer {PLUGIN_REF}")


def _offered(ctx: ToolContext) -> str | None:
    """Prove the registered oh-my-clanker marketplace offers omc on the LIVE
    config, and return the version it offers (None when the manifest entry
    carries no version).

    Reads the registration's installLocation from `claude plugin marketplace
    list --json` — Claude's clone for a github source, the directory itself
    for a directory source (older registrations carry only `path`) — then
    `.claude-plugin/marketplace.json` there. Deliberately NOT `plugin list
    --available`: that list excludes installed plugins (see _available), so
    using it here made every `omc update` on an installed plugin abort
    before `plugin update` ran — the installed plugin sat at 0.1.11 while
    the marketplace offered 0.1.13 (2026-10-01).
    """
    entries = _claude_json(ctx, ["claude", "plugin", "marketplace", "list", "--json"])
    current = None
    if isinstance(entries, list):
        current = next(
            (e for e in entries if isinstance(e, dict) and e.get("name") == MARKETPLACE_NAME),
            None,
        )
    if current is None:
        raise OmcError(f"the {MARKETPLACE_NAME} marketplace is not registered")
    location = current.get("installLocation") or current.get("path")
    if not location:
        raise OmcError(f"the {MARKETPLACE_NAME} marketplace registration has no install location")
    manifest = Path(str(location)) / ".claude-plugin" / "marketplace.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise OmcError(f"cannot read the marketplace manifest at {manifest}: {exc}") from exc
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entry = next(
        (p for p in (plugins or []) if isinstance(p, dict) and p.get("name") == "omc"), None
    )
    if entry is None:
        raise OmcError(
            f"refusing plugin replacement: marketplace at {location} does not offer {PLUGIN_REF}"
        )
    version = entry.get("version")
    return str(version) if version else None


def _skew(ctx: ToolContext, omc: dict) -> tuple[str, str] | None:
    """(installed, offered) when both versions are known and differ — the
    plugin loads but is stale. None otherwise, including when the marketplace
    is not registered or its manifest is unreadable: omc never fails over its
    own plumbing, and a missing version on either side (older Claude output
    without `version`, a manifest without one) is not skew. Plain string
    inequality: the installed snapshot always came from the registered
    marketplace, so "different" means "stale" in practice, and after a heal
    "equal" is the only honest success criterion.
    """
    installed = omc.get("version")
    if not installed:
        return None
    try:
        offered = _offered(ctx)
    except OmcError:
        return None
    if not offered or offered == str(installed):
        return None
    return str(installed), offered


def _source_matches(entry: dict, source: str) -> bool:
    if entry.get("source") == "directory" and source.startswith("/"):
        return Path(str(entry.get("path", ""))).resolve() == Path(source).resolve()
    return entry.get("source") == "github" and entry.get("repo") == source


def _check_project_source(ctx: ToolContext, source: str) -> None:
    root = repo_root(ctx)
    if root is None:
        return
    declaration = None
    for name in ("settings.json", "settings.local.json"):
        path = Path(root) / ".claude" / name
        try:
            settings = json.loads(path.read_text())
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            raise OmcError(f"cannot inspect project marketplace settings at {path}: {exc}") from exc
        declarations = (
            settings.get("extraKnownMarketplaces", {}) if isinstance(settings, dict) else {}
        )
        entry = declarations.get(MARKETPLACE_NAME) if isinstance(declarations, dict) else None
        if entry is not None:
            declaration = (path, entry.get("source") if isinstance(entry, dict) else None)
    if declaration is not None:
        path, declared = declaration
        if not isinstance(declared, dict) or not _source_matches(declared, source):
            raise OmcError(
                f"project marketplace declaration in {path} conflicts with {source}; "
                "update that declaration before replacing the user marketplace"
            )


def _prepare_marketplace(ctx: ToolContext, source: str) -> bool:
    """Refresh the intended source; return whether removal also removed plugins.

    Claude 2.1.281 rejects `add` when settings declare a different source.
    `marketplace remove` also UNINSTALLS its plugins, so prove the replacement
    can be fetched and installed in a disposable config before doing that.
    Never edit Claude's registry/settings files ourselves.

    The probe proves offering through `plugin list --available` (valid there:
    omc is not installed in the scratch config); the live config proves it
    through the marketplace manifest (`_offered`).
    """
    prefix = ["claude", "plugin", "marketplace"]
    entries = _claude_json(ctx, [*prefix, "list", "--json"])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise OmcError("unexpected `claude plugin marketplace list --json` output")
    current = next((e for e in entries if e.get("name") == MARKETPLACE_NAME), None)
    replaced = current is not None and not _source_matches(current, source)
    if replaced:
        _say(f"validating replacement marketplace from {source}…")
        with TemporaryDirectory(prefix="omc-marketplace-") as scratch:
            # Isolate user settings/cache, but retain cwd: project declarations
            # must constrain the preflight just as they constrain the live add.
            probe = ToolContext.from_env({**ctx.child_env(), "CLAUDE_CONFIG_DIR": scratch})
            _checked(probe, [*prefix, "add", source])
            _available(probe)
            _checked(
                probe,
                ["claude", "plugin", "install", PLUGIN_REF, "--scope", "user"],
            )
            plugins = _claude_json(probe, ["claude", "plugin", "list", "--json"])
            plugin = _find(plugins, "omc") if isinstance(plugins, list) else None
            if plugin is None or _problems(plugin):
                raise OmcError("replacement omc plugin failed its isolated load check")
        _say("replacing the stale oh-my-clanker marketplace registration…")
        _checked(ctx, [*prefix, "remove", MARKETPLACE_NAME, "--scope", "user"])
    if current is None or replaced:
        _checked(ctx, [*prefix, "add", source])
        actual = _claude_json(ctx, [*prefix, "list", "--json"])
        if not isinstance(actual, list) or not any(
            isinstance(e, dict) and e.get("name") == MARKETPLACE_NAME and _source_matches(e, source)
            for e in actual
        ):
            raise OmcError("oh-my-clanker marketplace still points at a different source")
    if not replaced:
        _checked(ctx, [*prefix, "update", MARKETPLACE_NAME])
    _offered(ctx)  # live-config proof: the manifest, never the available list
    return replaced


def ensure_plugin(
    ctx: ToolContext, provider: str, *, check_only: bool = False, update: bool = False
) -> str:
    """Ensure the omc plugin (and superpowers) is installed and healthy for
    ``provider``.

    Missing → install. Installed but failed to load / disabled → refresh the
    marketplace snapshot and reinstall. Healthy and ``update`` → run the
    provider's update sequence. Installed, loadable, but behind the
    marketplace's offered version (skew) → run the update sequence, and report
    the version transition. Every mutating path re-probes afterwards and
    raises ``OmcError`` (carrying Claude's own error text) if the plugin still
    doesn't load — a broken plugin must never be reported as "ok".

    Returns a short status string for the progress/plan output. Only claude
    has a verified scriptable probe + install today; other providers are left
    alone. ``check_only`` (the --dry-run path) never installs anything.
    """
    if provider != "claude":
        return "unverified (no scriptable check for this provider yet)"

    entries = _list_plugins(ctx)
    omc = _find(entries, "omc")
    superpowers = _find(entries, "superpowers")
    omc_problems = _problems(omc) if omc is not None else []
    # Skew is judged HERE only — never inside _problems, which also serves the
    # probe's load check in _prepare_marketplace and would route a stale but
    # loadable plugin through the destructive uninstall/reinstall path.
    # _find prefix-matches so a fork-installed omc still counts as installed and
    # loadable; only the canonical id is compared with (or updated from) the
    # oh-my-clanker manifest.
    canonical = omc is not None and omc.get("id") == PLUGIN_REF
    skew = _skew(ctx, omc) if canonical and not omc_problems else None

    if check_only:
        if omc is None:
            return "missing (omc design will install it)"
        if omc_problems:
            return f"failed to load: {omc_problems[0]} (omc design will reinstall it)"
        if skew is not None:
            return f"stale ({skew[0]} → {skew[1]} offered; omc design will update it)"
        if superpowers is None:
            return "ok; superpowers missing (omc design will install it)"
        return "ok"

    source = marketplace_source(ctx.env)
    refresh = omc is None or bool(omc_problems) or update or skew is not None
    if refresh:
        _check_project_source(ctx, source)
    omc_fix = (
        f"claude plugin marketplace add {shlex.quote(source)} && "
        f"claude plugin install {PLUGIN_REF} --scope user"
    )
    actions: list[str] = []

    if superpowers is None:
        _say("installing superpowers (omc's start skill hands off to it)…")
        # Best-effort: the official marketplace is usually pre-registered.
        ctx.run(["claude", "plugin", "marketplace", "add", _OFFICIAL_MARKETPLACE])
        _install(
            ctx,
            SUPERPOWERS_REF,
            manual_fix=(
                f"claude plugin marketplace add {_OFFICIAL_MARKETPLACE} && "
                f"claude plugin install {SUPERPOWERS_REF}"
            ),
        )
        actions.append("installed superpowers")

    replaced = False
    if refresh:
        try:
            replaced = _prepare_marketplace(ctx, source)
        except OmcError as exc:
            raise OmcError(
                f"{exc}\n  if the old marketplace is still registered, remove it first: "
                f"claude plugin marketplace remove {MARKETPLACE_NAME} --scope user\n"
                f"  fix manually: {omc_fix}"
            ) from exc

    before = str(omc.get("version") or "") if omc is not None else ""
    if omc is None:
        _say(f"installing the omc plugin from {source}…")
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("installed")
    elif omc_problems:
        _say(
            f"the omc plugin is installed but failed to load ({omc_problems[0]}) "
            f"— reinstalling from {source}…"
        )
        if not replaced:
            _checked(ctx, ["claude", "plugin", "uninstall", PLUGIN_REF])
        _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        actions.append("repaired")
    elif update or skew is not None:
        if skew is not None and not update:
            _say(f"the omc plugin is stale ({skew[0]} installed, {skew[1]} offered) — updating…")
        if replaced:
            _install(ctx, PLUGIN_REF, manual_fix=omc_fix)
        else:
            _checked(ctx, ["claude", "plugin", "update", PLUGIN_REF])
        actions.append("updated")

    if not actions:
        return "ok"

    omc = _find(_list_plugins(ctx), "omc")
    if omc is None:
        raise OmcError(
            "the omc plugin is still missing after an apparently successful install — "
            "check `claude plugin list` and the plugin's Status line"
        )
    problems = _problems(omc)
    if problems:
        joined = "\n  ".join(problems)
        raise OmcError(
            "the omc plugin still fails to load after reinstalling it:\n"
            f"  {joined}\n"
            "  check `claude plugin list`; if the error names a missing dependency, "
            "your marketplace snapshot may predate the fix — retry after `omc update`"
        )
    if actions[-1] == "updated":
        # An update that left the version behind is not an update. Say so with
        # both versions and the manual command, instead of "✓ updated".
        still = _skew(ctx, omc)
        if still is not None:
            raise OmcError(
                f"the omc plugin is still {still[0]} after updating ({still[1]} offered) — "
                f"run: claude plugin update {PLUGIN_REF}"
            )
        after = str(omc.get("version") or "")
        if before and after and before != after:
            return f"updated ({before} → {after})"
    return actions[-1]
