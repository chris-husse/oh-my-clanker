"""Live sibling lookup (spec 2026-11-16 §7.3): two omc-managed fixture repos
side by side under /work, both indexed with the real GitNexus. A question asked
in alpha hinges on a marker symbol defined only in beta; /omc:explain must
reach beta as a sibling primary checkout (`.omc/` plus a `.git` DIRECTORY) and
cite beta's file and value. A worktree of beta (`/work/beta.feature`, whose
`.git` is a file) is the decoy whose files must never be cited. Nothing is
cloned or indexed from here: `/work`'s entries and both repos' index metadata
are unchanged by the explain run. This is the real-tool test that replaces the
deleted dependency E2Es."""

from __future__ import annotations

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in
from .judge import judge

pytestmark = pytest.mark.e2e

_ALPHA = "/work/alpha"
_BETA = "/work/beta"
_DECOY = "/work/beta.feature"

# `ls -A /work` plus each repo's index metadata bytes (GitNexus writes
# gitnexus.json, older builds meta.json; the same fallback test_e2e_gitnexus.py
# reads), as one JSON line: the explain run must leave all of it unchanged.
_SNAPSHOT = (
    "import json, os; from pathlib import Path; "
    "metas = {}\n"
    f"for repo in ({_ALPHA!r}, {_BETA!r}):\n"
    "    root = Path(repo) / '.gitnexus'; meta = root / 'gitnexus.json'\n"
    "    meta = meta if meta.exists() else root / 'meta.json'\n"
    "    metas[repo] = meta.read_bytes().hex()\n"
    "print(json.dumps({'work': sorted(os.listdir('/work')), 'metas': metas}))"
)

_QUOKKA = '''"""Quokka ledger settlement."""

from datetime import date, timedelta

QUOKKA_SETTLEMENT_WINDOW_DAYS = 37


def settle_quokka_ledger(entries):
    """Drop entries older than the settlement window."""
    cutoff = date.today() - timedelta(days=QUOKKA_SETTLEMENT_WINDOW_DAYS)
    return [entry for entry in entries if entry["date"] >= cutoff]
'''

_CLIENT = '''"""Alpha's ledger client."""

from beta.ledger.quokka import settle_quokka_ledger


def close_day(entries):
    """Settle the day's entries through beta's ledger."""
    return settle_quokka_ledger(entries)
'''


def _explain_context(name: str) -> str:
    return f"""---
name: explain-context
description: Where {name} keeps its truth.
---

# explain-context ({name})

{name} is a small fixture repository; its committed code is its only source of truth.
"""


def _commit_files(container, repo: str, files: dict[str, str], message: str) -> None:
    """Write each file through a quoted heredoc, commit, and push to origin."""
    # Newline-joined under `set -e`: a heredoc terminator must stand alone on its line.
    script = ["set -e", f"cd {repo}"]
    for rel, text in files.items():
        if "/" in rel:
            script.append(f"mkdir -p {rel.rpartition('/')[0]}")
        script.append(f"cat > {rel} <<'OMC_FIXTURE_EOF'\n{text}OMC_FIXTURE_EOF")
    script.append(f"git add -A && git commit -qm '{message}' && git push -q origin main")
    rc, out = run_in(container, ["bash", "-c", "\n".join(script) + "\n"], timeout=30)
    assert rc == 0, f"seeding {repo} failed:\n{out}"


@pytest.mark.expensive
@pytest.mark.timeout(2100)
def test_explain_answers_from_a_sibling_omc_repo_graph(container):
    """Every run_in below carries an explicit budget; they sum to 1800 s —
    2 x 120 (configure_omc's two `omc configure` runs) + 2 x 30 (make_work_repo)
    + 2 x 30 (seed commits) + 30 (decoy worktree) + 2 x 240 (refresh, each
    embedding a fresh index) + 2 x 5 (test -d) + 2 x 10 (before/after
    snapshots) + 600 (explain) + 300 (the judge's own run_in). configure_omc's
    plugin setup exec has no budget of its own: at test time it is a
    no-network verification pass. The 2100 s ceiling leaves headroom over that
    sum so a hung step fails at its own budget, not deep inside docker-py's
    socket read. Expensive tier: the always-on embedding makes each repo's
    index step slow, past the 300 s ceiling."""
    require_token("claude")
    configure_omc(container, "claude", timeout=120)
    make_work_repo(container, _ALPHA, timeout=30)
    make_work_repo(container, _BETA, timeout=30)

    _commit_files(
        container,
        _BETA,
        {
            "ledger/quokka.py": _QUOKKA,
            ".omc/skills/explain-context/SKILL.md": _explain_context("beta"),
        },
        "add the quokka ledger",
    )
    _commit_files(
        container,
        _ALPHA,
        {
            "app/client.py": _CLIENT,
            ".omc/skills/explain-context/SKILL.md": _explain_context("alpha"),
        },
        "add the ledger client",
    )

    # The decoy: a worktree of beta carries the same .omc/ and quokka.py, but
    # its .git is a file, so the sibling rule must skip it.
    rc, out = run_in(
        container,
        ["git", "-C", _BETA, "worktree", "add", "-q", _DECOY, "-b", "feature"],
        timeout=30,
    )
    assert rc == 0, out

    for repo in (_BETA, _ALPHA):
        rc, out = run_in(
            container, ["omc", "internal", "gitnexus", "refresh"], cwd=repo, timeout=240
        )
        assert rc == 0, out
        rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"], timeout=5)
        assert rc == 0, f"refresh produced no .gitnexus/ index in {repo}:\n{out[-2000:]}"

    rc, before = run_in(container, ["python3", "-c", _SNAPSHOT], timeout=10)
    assert rc == 0, before

    question = (
        "How many days is the settlement window settle_quokka_ledger applies "
        "(alpha's app/client.py calls it), and where is it defined?"
    )
    rc, answer = run_in(
        container,
        [
            "claude",
            "-p",
            f"/omc:explain {question}",
            "--output-format",
            "text",
            "--allowed-tools",
            "Bash",
            "Skill",
            "Read",
            # The sibling checkouts live beside alpha, outside its directory;
            # headless Claude may only read them when /work is granted.
            "--add-dir",
            "/work",
        ],
        cwd=_ALPHA,
        timeout=600,
    )
    assert rc == 0, answer
    assert "37" in answer, answer
    assert "quokka.py" in answer, answer
    # Naming the skipped worktree is fine; citing a file inside it is not.
    assert f"{_DECOY}/" not in answer, answer

    # Never clone, nothing indexed from here: no new checkout beside the
    # siblings, and neither repo's index was rewritten by the explain run.
    rc, after = run_in(container, ["python3", "-c", _SNAPSHOT], timeout=10)
    assert rc == 0, after
    assert after == before, f"explain changed /work or an index:\n{before}\n{after}"

    verdict = judge(
        container,
        "claude",
        scenario=f"/omc:explain was asked {question!r} from the omc-managed repo alpha "
        f"({_ALPHA}). settle_quokka_ledger is defined only in the sibling omc-managed repo "
        f"beta ({_BETA}), in ledger/quokka.py, where QUOKKA_SETTLEMENT_WINDOW_DAYS = 37 "
        "and the function drops entries older than that window; alpha's app/client.py only "
        "imports and calls it. Both repos were already indexed; explain should have queried "
        "beta's own graph from beta's checkout, without cloning or indexing anything.",
        rubric=[
            "attributes the definition to the sibling repo beta (ledger/quokka.py), not to alpha",
            "states the 37-day window",
            "does not claim to have cloned or indexed anything",
            "is not a refusal or an error dump",
        ],
        artifacts=answer,
    )
    assert verdict["passed"], verdict["reasons"]
