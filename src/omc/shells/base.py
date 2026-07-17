from __future__ import annotations

import os
import shlex
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path

TMPDIR_PLACEHOLDER = "{omc_tmpdir}"


class Shell(ABC):
    """An interactive shell we can hand off to inside a worktree.

    build_invocation is the pure unit-tested seam: (argv, extra_files) where
    extra_files maps a RELATIVE filename -> contents for init files; argv refers
    to them via TMPDIR_PLACEHOLDER. exec_interactive performs all side effects
    (mkdtemp, write, substitute, chdir, execvp) and is E2E-only.
    """

    name: str

    @classmethod
    @abstractmethod
    def detect(cls, env: Mapping[str, str]) -> bool: ...

    @abstractmethod
    def build_invocation(
        self, *, cwd: str, title: str, startup_argv: list[str], title_seq: str
    ) -> tuple[list[str], dict[str, str]]: ...

    def exec_env_overrides(self, tmpdir: str | None) -> dict[str, str]:
        return {}

    def exec_interactive(
        self, *, cwd: str, title: str, startup_argv: list[str], title_seq: str
    ) -> None:  # pragma: no cover - effectful exec, E2E-verified
        argv, extra_files = self.build_invocation(
            cwd=cwd, title=title, startup_argv=startup_argv, title_seq=title_seq
        )
        tmpdir: str | None = None
        if extra_files:
            tmpdir = tempfile.mkdtemp(prefix=f"omc-{self.name}-")
            for relname, contents in extra_files.items():
                Path(tmpdir, relname).write_text(contents)
            argv = [arg.replace(TMPDIR_PLACEHOLDER, tmpdir) for arg in argv]
        os.environ.update(self.exec_env_overrides(tmpdir))
        os.chdir(cwd)
        os.execvp(argv[0], argv)  # noqa: S606 - argv is array-based, no shell


def joined_startup(startup_argv: list[str]) -> str:
    return shlex.join(startup_argv) if startup_argv else ""
