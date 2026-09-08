"""Write fake executables onto an isolated PATH dir for probe/argv tests."""

from __future__ import annotations

import shlex
import stat
from pathlib import Path


def make_stub(
    bindir: Path,
    name: str,
    *,
    stdout: str = "",
    stderr: str = "",
    rc: int = 0,
    argv_log: Path | None = None,
    env_log: Path | None = None,
) -> Path:
    # Quoted heredoc so stdout survives verbatim — JSON verdicts contain double quotes.
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / name
    # Each optional block is emitted ONLY when asked, so the default script stays
    # byte-identical for every existing caller: an unconditional stderr block would
    # put a stray newline on stderr and could flip a "stderr is empty" assertion.
    # printf over echo — a dash /bin/sh expands backslash escapes in echo's args.
    log = f"printf '%s\\n' \"$*\" > {shlex.quote(str(argv_log))}\n" if argv_log else ""
    # Absolute /usr/bin/env: stub_env's PATH holds only bindir, so a bare `env`
    # would not resolve. This records what the CHILD was handed, which is the only
    # way to prove a secret reached one subprocess and not the other.
    envlog = f"/usr/bin/env > {shlex.quote(str(env_log))}\n" if env_log else ""
    err = f"/bin/cat >&2 <<'OMC_STUB_ERR_EOF'\n{stderr}\nOMC_STUB_ERR_EOF\n" if stderr else ""
    path.write_text(
        f"#!/bin/sh\n{log}{envlog}/bin/cat <<'OMC_STUB_EOF'\n{stdout}\nOMC_STUB_EOF\n"
        f"{err}exit {rc}\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def stub_env(bindir: Path, **extra: str) -> dict[str, str]:
    """A minimal env whose PATH contains ONLY the stub dir."""
    return {"HOME": str(bindir.parent), "PATH": str(bindir), **extra}


def make_claude_stub(
    bindir: Path,
    *,
    plugins: list[dict] | None = None,
    stdout: str = "",
    rc: int = 0,
    install_rc: int = 0,
    install_errors: list[str] | None = None,
) -> Path:
    """A stateful `claude` stub for plugin-management tests.

    ``plugins`` seeds `claude plugin list --json` (each entry: ``id``, optional
    ``errors``/``enabled``). `plugin install X` adds X (healthy unless
    ``install_errors`` is set, or fails with ``install_rc``); `plugin uninstall
    X` removes it; `plugin marketplace …` / `plugin update …` succeed silently;
    `--version` answers like the real CLI. Every other invocation prints
    ``stdout`` and exits ``rc`` (the slug/verdict path). Every argv line is
    appended to the returned calls file.
    """
    import json
    import sys

    bindir.mkdir(parents=True, exist_ok=True)
    state = bindir / "claude.plugins.json"
    calls = bindir / "claude.calls"
    entries = [{"enabled": True, **e} for e in (plugins or [])]
    state.write_text(json.dumps(entries))
    script = f"""#!{sys.executable}
import json, sys
from pathlib import Path
state, calls = Path({str(state)!r}), Path({str(calls)!r})
args = sys.argv[1:]
with calls.open("a") as fh:
    fh.write(" ".join(args) + "\\n")
if args[:1] == ["--version"]:
    print("2.1.0 (Claude Code)"); sys.exit(0)
if args[:2] == ["plugin", "list"]:
    entries = json.loads(state.read_text())
    if "--json" in args:
        print(json.dumps(entries))
    else:
        print("Installed plugins:")
        for e in entries:
            print("  " + e["id"])
    sys.exit(0)
if args[:2] == ["plugin", "install"]:
    if {install_rc} != 0:
        print("install failed", file=sys.stderr); sys.exit({install_rc})
    pid = args[2]
    entries = [e for e in json.loads(state.read_text()) if e["id"] != pid]
    entry = {{"id": pid, "enabled": True}}
    if pid.startswith("omc@") and {install_errors!r}:
        entry["errors"] = {install_errors!r}
    entries.append(entry)
    state.write_text(json.dumps(entries)); print("installed " + pid); sys.exit(0)
if args[:2] == ["plugin", "uninstall"]:
    pid = args[2]
    state.write_text(json.dumps([e for e in json.loads(state.read_text()) if e["id"] != pid]))
    print("uninstalled " + pid); sys.exit(0)
if args[:2] in (["plugin", "marketplace"], ["plugin", "update"]):
    print("ok"); sys.exit(0)
sys.stdout.write({stdout!r} + "\\n"); sys.exit({rc})
"""
    path = bindir / "claude"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return calls


HEALTHY_PLUGINS = [{"id": "omc@oh-my-clanker"}, {"id": "superpowers@claude-plugins-official"}]


def make_codex_stub(
    bindir: Path,
    *,
    plugins: list[dict] | None = None,
    marketplaces: list[dict] | None = None,
    add_rc: int = 0,
    blank_plugin_list: bool = False,
) -> Path:
    """A stateful `codex` stub for plugin-management tests.

    ``plugins`` seeds `codex plugin list --json`'s ``installed`` array (each
    entry: ``pluginId``, ``name``, ``marketplaceName``, optional ``enabled``);
    ``marketplaces`` seeds `codex plugin marketplace list --json`. `plugin add
    X` installs X; `plugin remove X` uninstalls it; `plugin marketplace add
    SRC` registers it — and REFUSES with exit 1 when a same-named marketplace
    already exists from a different source, exactly like the real CLI
    (0.153.4); `plugin marketplace remove NAME` drops it AND its installed
    plugins (no zombie state). ``blank_plugin_list`` makes `plugin list --json`
    exit 0 printing NOTHING — a silent CLI, which must be treated as a broken
    probe rather than an empty state. Every argv line is appended to the
    returned calls file, so a test can prove which commands did NOT run.
    """
    import json
    import sys

    bindir.mkdir(parents=True, exist_ok=True)
    pstate = bindir / "codex.plugins.json"
    mstate = bindir / "codex.marketplaces.json"
    calls = bindir / "codex.calls"
    # Seed defaults FIRST so a caller's explicit `enabled: False` overrides them.
    pstate.write_text(
        json.dumps([{"enabled": True, "installed": True, **e} for e in (plugins or [])])
    )
    mstate.write_text(json.dumps(marketplaces or []))
    # A Python stub (not /bin/sh) because this one is stateful: it reads, mutates
    # and rewrites JSON between invocations, which shell cannot do readably.
    script = f"""#!{sys.executable}
import json, sys
from pathlib import Path
pstate, mstate = Path({str(pstate)!r}), Path({str(mstate)!r})
calls = Path({str(calls)!r})
args = sys.argv[1:]
with calls.open("a") as fh:
    fh.write(" ".join(args) + "\\n")
if args[:1] == ["--version"]:
    print("codex-cli 0.153.4"); sys.exit(0)
plugins, markets = json.loads(pstate.read_text()), json.loads(mstate.read_text())
if args[:4] == ["plugin", "marketplace", "list", "--json"]:
    print(json.dumps({{"marketplaces": markets}})); sys.exit(0)
if args[:3] == ["plugin", "list", "--json"]:
    if not {blank_plugin_list!r}:
        print(json.dumps({{"installed": plugins, "available": []}}))
    sys.exit(0)
if args[:3] == ["plugin", "marketplace", "add"]:
    if {add_rc} != 0:
        print("add failed", file=sys.stderr); sys.exit({add_rc})
    src = args[3]
    # codex does NOT echo the source back: owner/repo is normalised to a full
    # git URL, and `root` becomes a local CACHE path. Local paths are echoed
    # verbatim. Measured against 0.153.4 - see _canonical_source in codex.py.
    if src.startswith(("/", "./", "~")):
        stored, kind, root = src, "local", src
    else:
        stored = src if src.startswith(("http", "git@", "ssh://")) else (
            "https://github.com/" + src + ".git")
        kind, root = "git", "/codex-home/.tmp/marketplaces/"
    name = "superpowers-marketplace" if "superpowers" in src else "oh-my-clanker"
    if kind == "git":
        root = root + name
    same = [m for m in markets if m["name"] == name]
    if same and (same[0].get("marketplaceSource") or {{}}).get("source") != stored:
        print("Error: marketplace '" + name + "' is already added from a "
              "different source; remove it before adding this source",
              file=sys.stderr)
        sys.exit(1)
    if not same:
        markets.append({{"name": name, "root": root,
                         "marketplaceSource": {{"sourceType": kind, "source": stored}}}})
        mstate.write_text(json.dumps(markets))
    print("Added marketplace `" + name + "` from " + stored); sys.exit(0)
if args[:3] == ["plugin", "marketplace", "remove"]:
    name = args[3]
    markets = [m for m in markets if m["name"] != name]
    mstate.write_text(json.dumps(markets))
    # removing a marketplace also removes its plugins - verified, no zombies
    plugins = [p for p in plugins if p.get("marketplaceName") != name]
    pstate.write_text(json.dumps(plugins))
    print("Removed marketplace `" + name + "`"); sys.exit(0)
if args[:3] == ["plugin", "marketplace", "upgrade"]:
    print("Upgraded 0 marketplace(s)."); sys.exit(0)
if args[:2] == ["plugin", "add"]:
    pid = args[2]
    name, _, mkt = pid.partition("@")
    plugins = [p for p in plugins if p.get("pluginId") != pid]
    plugins.append({{"pluginId": pid, "name": name, "marketplaceName": mkt,
                     "installed": True, "enabled": True, "version": "0.1.7"}})
    pstate.write_text(json.dumps(plugins))
    print("Added plugin `" + name + "`"); sys.exit(0)
if args[:2] == ["plugin", "remove"]:
    pid = args[2]
    pstate.write_text(json.dumps([p for p in plugins if p.get("pluginId") != pid]))
    print("Removed plugin"); sys.exit(0)
sys.exit(0)
"""
    path = bindir / "codex"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return calls


CODEX_OMC = {"pluginId": "omc@oh-my-clanker", "name": "omc", "marketplaceName": "oh-my-clanker"}
CODEX_SUPERPOWERS = {
    "pluginId": "superpowers@superpowers-marketplace",
    "name": "superpowers",
    "marketplaceName": "superpowers-marketplace",
}
# The registered-from-somewhere-else case: omc resolves the fallback repo, so
# this seeds the different-source marketplace conflict.
CODEX_MKT_LOCAL = {
    "name": "oh-my-clanker",
    "root": "/old/checkout",
    "marketplaceSource": {"sourceType": "local", "source": "/old/checkout"},
}
# The no-conflict marketplace — the DEFAULT GitHub-installed user. This is what
# codex actually stores after `marketplace add chris-husse/oh-my-clanker`
# (measured 0.153.4): the source normalised to a full git URL, and a `root` that
# is a local cache path rather than the source. marketplace_source(env) resolves
# to the bare `chris-husse/oh-my-clanker` under stub_env, so these two agree only
# after _canonical_source() — which is exactly the regression this fixture pins.
CODEX_MKT_FALLBACK = {
    "name": "oh-my-clanker",
    "root": "/codex-home/.tmp/marketplaces/oh-my-clanker",
    "marketplaceSource": {
        "sourceType": "git",
        "source": "https://github.com/chris-husse/oh-my-clanker.git",
    },
}
