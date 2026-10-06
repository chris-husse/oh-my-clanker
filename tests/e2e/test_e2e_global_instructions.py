"""The installed CLI delivers its section without touching project root files."""

from __future__ import annotations

import json

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in
from .lifecycle_helpers import _codex_model

pytestmark = pytest.mark.e2e

_USER_TEXT = "# My global instructions\nKeep this exact line.\n"
_HEADING_PROMPT = (
    "Quote only the Markdown heading immediately after the omc:begin marker "
    "in your preloaded global instructions. Do not use tools."
)
_NONFATAL_CODEX_FEATURE_WARNING = "Ignoring unknown `features` requirement `ultrafast_mode`"


def _run(container, argv, *, cwd=None, timeout=300):
    rc, out = run_in(container, argv, cwd=cwd, timeout=timeout)
    assert rc == 0, f"{argv!r} exited {rc}:\n{out}"
    return out


def test_configure_preserves_user_text_is_idempotent_and_uninstall_removes_section(container):
    _run(
        container,
        [
            "python3",
            "-c",
            "from pathlib import Path; p=Path('/root/.claude/CLAUDE.md'); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            "p.write_bytes(__import__('sys').argv[1].encode())",
            _USER_TEXT,
        ],
    )
    _run(container, ["omc", "configure", "--defaults"])
    _run(
        container,
        [
            "python3",
            "-c",
            """from pathlib import Path
import subprocess, sys
p = Path('/root/.claude/CLAUDE.md')
data = p.read_bytes()
user = sys.argv[1].encode()
installed = Path(subprocess.check_output(['omc', 'print-install-path'], text=True).strip())
body = (installed / 'distribution/AGENTS.md').read_bytes()
assert data.startswith(user + b'\\n'), data[:len(user) + 10]
assert data.count(b'<!-- omc:begin ') == 1
assert data.count(b'<!-- omc:end ') == 1
assert b'\\n' + body + b'<!-- omc:end ' in data
assert b'omc behavior layer' in body
""",
            _USER_TEXT,
        ],
    )
    _run(container, ["cp", "/root/.claude/CLAUDE.md", "/tmp/global-before.md"])
    _run(container, ["omc", "configure", "--defaults"])
    _run(container, ["cmp", "/tmp/global-before.md", "/root/.claude/CLAUDE.md"])

    _run(container, ["omc", "uninstall"])
    remaining = _run(container, ["cat", "/root/.claude/CLAUDE.md"])
    assert remaining == _USER_TEXT


def test_committed_root_claude_file_is_untouched_by_configure(container):
    _run(
        container,
        [
            "bash",
            "-c",
            "set -e; mkdir -p /tmp/committed-root; cd /tmp/committed-root; "
            "git init -q; printf '# Project-owned instructions\\n' > CLAUDE.md; "
            "git add CLAUDE.md; git commit -qm seed",
        ],
    )
    _run(container, ["omc", "configure", "--defaults"], cwd="/tmp/committed-root")
    _run(
        container,
        [
            "bash",
            "-c",
            "set -e; git diff --quiet; git diff --cached --quiet; "
            "test \"$(cat CLAUDE.md)\" = '# Project-owned instructions'; "
            "test ! -e AGENTS.md; test ! -e .gitignore; "
            "test -f .omc/config/AGENTS.md; "
            "test -f .omc/config.yaml",
        ],
        cwd="/tmp/committed-root",
    )


def test_installed_internal_cli_restores_stale_section(container):
    _run(container, ["omc", "configure", "--defaults"])
    path = "/root/.claude/CLAUDE.md"
    _run(container, ["cp", path, "/tmp/current-global.md"])
    _run(
        container,
        [
            "python3",
            "-c",
            """from pathlib import Path
p = Path('/root/.claude/CLAUDE.md')
data = p.read_bytes()
assert data.count(b'# omc behavior layer') == 1
p.write_bytes(data.replace(b'# omc behavior layer', b'# stale omc layer', 1))
""",
        ],
    )
    _run(container, ["omc", "internal", "global-instructions", "claude"])
    _run(container, ["cmp", "/tmp/current-global.md", path])


def test_claude_reads_global_section_in_headless_session(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container, "/work/global-claude")
    _run(container, ["omc", "configure", "--set", "llm.default=claude"], cwd=repo)
    answer = _run(
        container,
        [
            "claude",
            "-p",
            _HEADING_PROMPT,
            "--output-format",
            "text",
            "--strict-mcp-config",
            "--tools",
            "",
        ],
        cwd=repo,
        timeout=300,
    )
    assert "omc behavior layer" in answer.lower(), answer


@pytest.mark.e2e_provider("codex")
def test_codex_reads_global_section_in_headless_session(container):
    require_token("codex")
    configure_omc(container, "codex")
    repo = make_work_repo(container, "/work/global-codex")
    _run(
        container,
        ["omc", "configure", "--set", "llm.default=codex"],
        cwd=repo,
    )
    _run(
        container,
        [
            "python3",
            "-c",
            """import os, tomllib
from pathlib import Path
home = Path(os.environ['CODEX_HOME'])
data = (home / 'AGENTS.md').read_bytes()
assert data.count(b'<!-- omc:begin ') == 1
assert b'# omc behavior layer' in data
assert not (home / 'AGENTS.override.md').exists()
config = home / 'config.toml'
settings = tomllib.loads(config.read_text()) if config.exists() else {}
assert settings.get('project_doc_max_bytes', 32768) >= len(data)
""",
        ],
    )
    prompt = (
        "In the global AGENTS instructions provided at session start, quote "
        "the first Markdown heading. Reply with just that heading. Do not use tools."
    )
    output = _run(
        container,
        [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-m",
            _codex_model(),
            prompt,
        ],
        cwd=repo,
        timeout=300,
    )
    events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    items = [event["item"] for event in events if isinstance(event.get("item"), dict)]
    assert items, output
    assert all(
        item.get("type") in {"agent_message", "reasoning"}
        or (
            item.get("type") == "error"
            and isinstance(item.get("message"), str)
            and item["message"].startswith(_NONFATAL_CODEX_FEATURE_WARNING)
        )
        for item in items
    ), items
    answer = "\n".join(item["text"] for item in items if item["type"] == "agent_message")
    assert "omc behavior layer" in answer.lower(), output
