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
