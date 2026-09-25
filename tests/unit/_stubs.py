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
    marketplaces: list[dict] | None = None,
    marketplace_failures: dict[str, str] | None = None,
    available_omc: bool = True,
) -> Path:
    """A stateful `claude` stub for plugin-management tests.

    ``plugins`` seeds `claude plugin list --json` (each entry: ``id``, optional
    ``errors``/``enabled``). `plugin install X` adds X (healthy unless
    ``install_errors`` is set, or fails with ``install_rc``); `plugin uninstall
    X` removes it. Marketplace registration models Claude's source conflict
    and removal cascades; failures can be injected by operation name.
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
    markets = bindir / "claude.marketplaces.json"
    markets.write_text(json.dumps(marketplaces or []))
    script = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
state, calls = Path({str(state)!r}), Path({str(calls)!r})
markets = Path({str(markets)!r})
isolated = os.environ.get("CLAUDE_CONFIG_DIR")
if isolated:
    root = Path(isolated)
    root.mkdir(parents=True, exist_ok=True)
    state, markets = root / "plugins.json", root / "marketplaces.json"
    for path in (state, markets):
        if not path.exists(): path.write_text("[]")
args = sys.argv[1:]
with calls.open("a") as fh:
    fh.write(("isolated " if isolated else "") + " ".join(args) + "\\n")
if args[:1] == ["--version"]:
    print("2.1.0 (Claude Code)"); sys.exit(0)
if args[:2] == ["plugin", "list"]:
    entries = json.loads(state.read_text())
    if "--json" in args:
        if "--available" in args:
            available = [{{"pluginId": "omc@oh-my-clanker"}}] if {available_omc!r} else []
            print(json.dumps({{"installed": entries, "available": available}}))
        else:
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
if args[:2] == ["plugin", "marketplace"]:
    op = args[2]
    failures = {marketplace_failures or {}!r}
    if op in failures:
        print(failures[op], file=sys.stderr); sys.exit(1)
    entries = json.loads(markets.read_text())
    if op == "list":
        print(json.dumps(entries)); sys.exit(0)
    if op == "add":
        source = args[3]
        if source.startswith("/"):
            new = {{"name": "oh-my-clanker", "source": "directory",
                   "path": source, "installLocation": source}}
        else:
            name = "oh-my-clanker" if source.endswith("oh-my-clanker") else source.split("/")[-1]
            new = {{"name": name, "source": "github", "repo": source,
                   "installLocation": "/cache/" + name}}
        old = next((m for m in entries if m["name"] == new["name"]), None)
        if old and any(old.get(k) != new.get(k) for k in ("source", "path", "repo")):
            print("source differs from the one declared in settings", file=sys.stderr); sys.exit(1)
        entries = [m for m in entries if m["name"] != new["name"]] + [new]
        markets.write_text(json.dumps(entries))
    if op == "remove":
        name = args[3]
        markets.write_text(json.dumps([m for m in entries if m["name"] != name]))
        plugins = [p for p in json.loads(state.read_text()) if not p["id"].endswith("@" + name)]
        state.write_text(json.dumps(plugins))
    print("ok"); sys.exit(0)
if args[:2] == ["plugin", "update"]:
    print("ok"); sys.exit(0)
sys.stdout.write({stdout!r} + "\\n"); sys.exit({rc})
"""
    path = bindir / "claude"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return calls


HEALTHY_PLUGINS = [{"id": "omc@oh-my-clanker"}, {"id": "superpowers@claude-plugins-official"}]
