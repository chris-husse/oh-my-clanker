from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_UV_KEYS = ("UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_CACHE_DIR")


@dataclass
class ToolContext:
    home: Path
    env: Mapping[str, str]
    uv_bin: str = "uv"
    uv_env: dict[str, str] = field(default_factory=dict)
    wt_bin: str = "wt"
    git_bin: str = "git"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ToolContext:
        env = dict(os.environ if env is None else env)
        home = Path(env.get("OMC_HOME") or (Path(env.get("HOME") or str(Path.home())) / ".omc"))
        return cls(
            home=home,
            env=env,
            uv_bin=env.get("OMC_UV_BIN", "uv"),
            uv_env={k: env[k] for k in _UV_KEYS if k in env},
            wt_bin=env.get("OMC_WT_BIN", "wt"),
            git_bin=env.get("OMC_GIT_BIN", "git"),
        )

    def uv_argv(self, *args: str) -> list[str]:
        return [self.uv_bin, *args]

    def child_env(self) -> dict[str, str]:
        return {**self.env, **self.uv_env}

    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        capture: bool = True,
        timeout: float | None = None,
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run argv (text mode) under child_env(); the single subprocess boundary.

        A captured subprocess gets stdin=DEVNULL: a tool that prompts would write the
        prompt into the captured pipe (invisible) and hang; with DEVNULL it gets EOF
        and proceeds or fails fast.
        """
        kwargs: dict[str, object] = {
            "env": {**self.child_env(), **(extra_env or {})},
            "check": check,
            "timeout": timeout,
        }
        if cwd is not None:
            kwargs["cwd"] = cwd
        if capture:
            kwargs["capture_output"] = True
            kwargs["text"] = True
            kwargs["stdin"] = subprocess.DEVNULL
        return subprocess.run(list(argv), **kwargs)  # noqa: S603 - argv list, no shell


def tool_version(ctx: ToolContext, argv: Sequence[str], *, timeout: float = 5) -> tuple[bool, str]:
    """Probe a tool's --version. Returns (present, detail); never raises."""
    try:
        cp = ctx.run(argv, capture=True, timeout=timeout)
    except FileNotFoundError:
        return False, f"not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as exc:
        return False, str(exc)
    if cp.returncode != 0:
        reason = (cp.stderr or cp.stdout or "").strip() or f"exit code {cp.returncode}"
        return False, reason
    return True, (cp.stdout or cp.stderr or "").strip()
