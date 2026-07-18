"""Chain v2 in a clean container: create from nothing, migrate from v1."""

from __future__ import annotations

import pytest

from .harness import run_in

pytestmark = pytest.mark.e2e

SETUP = """
set -e
git init -q /tmp/proj && cd /tmp/proj
git config user.email t@t && git config user.name t
"""

CREATE_CHECK = """
set -e
cd /tmp/proj
omc configure --defaults >/dev/null 2>&1 || true
test -L AGENTS.md && test -L CLAUDE.md
readlink AGENTS.md | grep -q 'distribution/AGENTS.md'
grep -q '^/AGENTS.md$' .gitignore && grep -q '^/CLAUDE.md$' .gitignore
test -f .omc/config/AGENTS.md
test ! -e .omc/internal/AGENTS.md
head -1 "$(omc print-install-path)/distribution/AGENTS.md" | grep -q 'omc behavior layer'
"""

MIGRATE_SETUP = """
set -e
git init -q /tmp/v1 && cd /tmp/v1
git config user.email t@t && git config user.name t
mkdir -p .omc/internal .omc/config
echo '# omc behavior layer (generated)' > .omc/internal/AGENTS.md
echo '# mine' > .omc/config/AGENTS.md
ln -s .omc/internal/AGENTS.md AGENTS.md
ln -s .omc/internal/AGENTS.md CLAUDE.md
"""

MIGRATE_CHECK = """
set -e
cd /tmp/v1
omc configure --defaults >/dev/null 2>&1 || true
readlink AGENTS.md | grep -q 'distribution/AGENTS.md'
test ! -e .omc/internal
grep -q '# mine' .omc/config/AGENTS.md
"""


def test_chain_creates_and_migrates_in_container(container):
    for script in (SETUP, CREATE_CHECK, MIGRATE_SETUP, MIGRATE_CHECK):
        rc, out = run_in(container, ["bash", "-c", script])
        assert rc == 0, out
