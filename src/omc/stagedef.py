"""Stage definition: the `.omc/bin/<stage>` hook, its states, and its instructions.

A hook answers `--show-instructions` with Markdown in the fixed shape of HEADINGS;
`## Limits` is the only section omc reads as data. Every way a stage definition can
be wrong is a StageBroken refusal whose message is the whole CRITICAL STOP sentence.
"""

from __future__ import annotations

import dataclasses
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import Refusal
from .fsutil import write_atomic
from .toolctx import ToolContext

Stage = Literal["check", "build", "verify"]
STAGES: tuple[Stage, ...] = ("check", "build", "verify")

HEADINGS = ("Limits", "Done when", "Passed when", "Fixing failures", "Rerunning a subset")
_OPTIONAL = frozenset({"Rerunning a subset"})

SECTION_MAX = 5120  # UTF-8 bytes
TOTAL_MAX = 20480  # UTF-8 bytes
SHOW_WARN_S = 1.0
SHOW_STOP_S = 10.0
# omc's default bound: the layer's ten fix cycles per conductor run, each needing
# progress or a new idea. A stage's `Fix cycles:` may only lower it for itself.
DEFAULT_FIX_CYCLES = 10
MAX_FIX_CYCLES = DEFAULT_FIX_CYCLES

HookState = Literal["present", "absent", "unmigrated", "not-executable"]

_HEADING = re.compile(r"^## (.+?)\s*$")
_DURATION = re.compile(r"([0-9]+)(s|m|h)")
_UNIT_S = {"s": 1, "m": 60, "h": 3600}
_TEMPLATE_NAME = "INSTRUCTIONS.template.md"


class StageBroken(Refusal):
    """A stage definition omc cannot run; goes to the user, never to the fix loop.

    Built from the detail; the message is the whole `CRITICAL STOP: <detail>` sentence."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"CRITICAL STOP: {detail}")


@dataclass(frozen=True)
class Limits:
    timeout_s: float | None
    quiet_s: float | None
    fix_cycles: int | None  # the stage's own bound; None: absent or `none` (omc's default)


@dataclass(frozen=True)
class Instructions:
    text: str
    limits: Limits
    subset: str | None
    warnings: tuple[str, ...]


# --- the hook (§6.2) ---


def hook_path(root: Path, stage: str) -> Path:
    """The hook is extensionless and run by path, so its shebang picks the interpreter (U14)."""
    return root / ".omc" / "bin" / stage


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _sh_hook(root: Path, stage: str) -> Path:
    """The hook's pre-U14 `.sh` name: never run, only refused."""
    return root / ".omc" / "bin" / f"{stage}.sh"


def hook_state(root: Path, stage: str) -> HookState:
    """Which row of the §6.2 table `root` is in for `stage`; a hook wins over an old skill."""
    hook = hook_path(root, stage)
    if _exists(hook):
        return "present" if hook.is_file() and os.access(hook, os.X_OK) else "not-executable"
    if _exists(_sh_hook(root, stage)) or (root / ".omc" / "skills" / stage / "SKILL.md").exists():
        return "unmigrated"
    return "absent"


def check_hook(root: Path, stage: str) -> None:
    """Refuse a stage that is unmigrated or whose hook is not executable."""
    state = hook_state(root, stage)
    if state == "unmigrated" and _exists(_sh_hook(root, stage)):
        raise StageBroken(
            f"rename .omc/bin/{stage}.sh to .omc/bin/{stage} (stage hooks have no extension)"
        )
    if state == "unmigrated":
        raise StageBroken(
            f"migrate this stage with /omc:integrate "
            f"(.omc/skills/{stage}/SKILL.md exists, .omc/bin/{stage} does not)"
        )
    if state == "not-executable":
        raise StageBroken(f".omc/bin/{stage} is not executable")


# --- the instructions (§6.3) ---


def parse_duration(value: str) -> float | None:
    """`90s`, `45m`, `2h` as seconds, `none` as None; anything else is a ValueError."""
    if value == "none":
        return None
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError(f"not a duration: {value!r}")
    return float(int(match[1]) * _UNIT_S[match[2]])


def format_duration(seconds: float) -> str:
    """The largest whole unit: `2700.0 → "45m"`, `7200.0 → "2h"`, `90.0 → "90s"`."""
    if seconds and seconds % 3600 == 0:
        return f"{int(seconds // 3600)}h"
    if seconds and seconds % 60 == 0:
        return f"{int(seconds // 60)}m"
    return f"{seconds:g}s"


def _split_sections(text: str) -> dict[str, str]:
    """Heading → stripped body, after the structure checks; `## ` counts inside fences too."""
    bodies: dict[str, list[str]] = {}
    current: list[str] | None = None
    last = -1
    for line in text.split("\n"):
        match = _HEADING.match(line)
        if match is None:
            if current is not None:
                current.append(line)
            elif line.strip():
                raise StageBroken(f'text before the first heading: "{line.strip()}"')
            continue
        heading = match[1]
        if heading not in HEADINGS:
            raise StageBroken(f'unknown heading "## {heading}"')
        if heading in bodies:
            raise StageBroken(f'repeated heading "## {heading}"')
        index = HEADINGS.index(heading)
        if index < last:
            raise StageBroken(f'heading "## {heading}" out of order')
        last = index
        current = bodies[heading] = []
    for heading in HEADINGS:
        if heading not in bodies and heading not in _OPTIONAL:
            raise StageBroken(f'required heading "## {heading}" missing')
    return {heading: "\n".join(lines).strip() for heading, lines in bodies.items()}


def _parse_limits(body: str) -> Limits:
    values: dict[str, str] = {}
    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep or key not in ("Timeout", "Quiet timeout", "Fix cycles") or key in values:
            raise StageBroken(f'## Limits has an unknown line "{line}"')
        value = value.strip()
        if key == "Fix cycles":
            if value != "none" and not (
                re.fullmatch(r"[0-9]+", value) and 1 <= int(value) <= MAX_FIX_CYCLES
            ):
                raise StageBroken(
                    f'## Limits: Fix cycles must be 1 to {MAX_FIX_CYCLES} in "{line}"'
                )
        else:
            try:
                parse_duration(value)
            except ValueError:
                raise StageBroken(f'## Limits: duration does not parse in "{line}"') from None
        values[key] = value
    for key in ("Timeout", "Quiet timeout"):
        if key not in values:
            raise StageBroken(f'## Limits is missing "{key}:"')
    cycles = values.get("Fix cycles", "none")
    return Limits(
        timeout_s=parse_duration(values["Timeout"]),
        quiet_s=parse_duration(values["Quiet timeout"]),
        fix_cycles=None if cycles == "none" else int(cycles),
    )


def parse_instructions(text: str) -> Instructions:
    """Validate `--show-instructions` output (§6.3); every finding is a StageBroken."""
    text = text.removeprefix("\ufeff").replace("\r\n", "\n")
    size = len(text.encode("utf-8"))
    if size > TOTAL_MAX:
        raise StageBroken(f"instructions are {size} bytes, over the {TOTAL_MAX}-byte limit")
    sections = _split_sections(text)
    for heading, body in sections.items():
        size = len(body.encode("utf-8"))
        if size > SECTION_MAX:
            raise StageBroken(
                f'section "## {heading}" is {size} bytes, over the {SECTION_MAX}-byte limit'
            )
    return Instructions(
        text=text,
        limits=_parse_limits(sections["Limits"]),
        subset=sections.get("Rerunning a subset"),
        warnings=(),
    )


def show_instructions(ctx: ToolContext, root: Path, stage: str) -> Instructions:
    """Ask the hook for its instructions and validate them; slow, failing or bad is refused."""
    warn_s, stop_s = SHOW_WARN_S, SHOW_STOP_S
    hook = hook_path(root, stage)
    name = f".omc/bin/{stage} --show-instructions"
    started = time.monotonic()
    try:
        done = ctx.run_bounded([str(hook), "--show-instructions"], timeout=stop_s, cwd=root)
    except TimeoutError:
        raise StageBroken(f"{name} did not answer within {stop_s:g}s") from None
    except OSError as exc:  # no shebang (ENOEXEC), a missing interpreter (ENOENT), …
        raise StageBroken(f"{name} could not start: {exc.strerror}") from None
    took = time.monotonic() - started
    if done.returncode != 0:
        raise StageBroken(f"{name} exited {done.returncode}")
    if not done.stdout.strip():
        raise StageBroken(f"{name} printed nothing")
    parsed = parse_instructions(done.stdout)
    if took > warn_s:
        warning = f"{name} took {took:.1f}s (over {warn_s:g}s)"
        parsed = dataclasses.replace(parsed, warnings=(*parsed.warnings, warning))
    return parsed


# --- the template (U11, C2) ---

STAGE_TEMPLATE = """\
## Limits
Timeout: 45m
Quiet timeout: 5m
Fix cycles: 4

Delete this paragraph: omc reads this section as data, and nothing else may be in
it. `Timeout:` and `Quiet timeout:` (no log growth for that long) are required, as
durations such as `90s`, `45m` or `2h`; `none` switches a limit off. `Fix cycles:` is
optional, 1 to 10: it lowers omc's default of 10 fix cycles for this stage; leave it
out, or write `none`, to keep the default.

## Done when
For the agent, in the project's terms: what makes a run end (the hook exits, or it
writes `passed` or `failed` to `$OMC_STAGE_RESULT`) and, for a verify that can be held
after a failure, what the held system offers for inspection. The hook writes the word
in one write (`echo failed > "$OMC_STAGE_RESULT"`): omc reads it as soon as the file
holds one.

## Passed when
For the agent, in the project's terms: what a pass means and what it covers.

## Fixing failures
For the agent that drives the fix loop: how to read the log, and what the fix agent
must never fix (flaky infrastructure, the stage itself).

## Rerunning a subset
Optional, the only heading that may be left out. For the agent: the arguments that
rerun only named pieces.
"""


def refresh_template(root: Path) -> bool:
    """Write `.omc/bin/INSTRUCTIONS.template.md` when `.omc/bin/` exists and it is stale."""
    bindir = root / ".omc" / "bin"
    if not bindir.is_dir():
        return False
    path = bindir / _TEMPLATE_NAME
    try:
        if path.read_bytes() == STAGE_TEMPLATE.encode("utf-8"):
            return False
    except FileNotFoundError:
        pass
    write_atomic(path, STAGE_TEMPLATE)
    return True
