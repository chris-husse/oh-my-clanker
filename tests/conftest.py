"""Root test configuration: hermetic git for every subprocess the suites spawn.

The unit suite creates hundreds of throwaway repos. Inheriting the host's
global git config made them depend on it: on a host with `commit.gpgsign`,
sixteen parallel workers exhausted gpg-agent ("Cannot allocate memory") and
`git commit` failed with exit 128 (observed 2026-10-01). A private global
config, pointed at by GIT_CONFIG_GLOBAL before any test runs, takes the host
out of the picture for the test process and everything it spawns — the
native `tests/local` iTerm2 tier included, whose real TUIs then commit as
"omc tests" too.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_GIT_CONFIG = Path(tempfile.mkdtemp(prefix="omc-tests-git-")) / "gitconfig"
_GIT_CONFIG.write_text(
    "[user]\n\tname = omc tests\n\temail = tests@omc.invalid\n"
    "[commit]\n\tgpgsign = false\n"
    "[tag]\n\tgpgsign = false\n"
    "[init]\n\tdefaultBranch = main\n"
)
os.environ["GIT_CONFIG_GLOBAL"] = str(_GIT_CONFIG)
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
# The same for the host's shell startup config. NixOS shells re-apply the system
# environment (resetting PATH) unless __NIXOS_SET_ENVIRONMENT_DONE is set, and its
# generated /etc/fish/config.fish runs `fenv` (bash + a bare `env`) at every fish
# start — "env: command not found" on a restricted PATH — unless its once-per-shell
# markers are set (`set -q` honors them exported). No fish flag skips the system
# config without also skipping conf.d. On every other host nothing reads these.
HOST_SHELL_MARKERS = (
    "__NIXOS_SET_ENVIRONMENT_DONE",
    "__fish_nixos_general_config_sourced",
    "__fish_nixos_login_config_sourced",
    "__fish_nixos_interactive_config_sourced",
)
for _marker in HOST_SHELL_MARKERS:
    os.environ[_marker] = "1"
atexit.register(shutil.rmtree, _GIT_CONFIG.parent, True)
