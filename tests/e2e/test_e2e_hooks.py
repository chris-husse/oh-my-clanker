"""Live project hooks (spec 2026-11-16 §6): the MR gate's `needs_verify` and
`pre_push`, and `rebase-main`'s `post_rebase`, each driven by a real hook skill
in a real repository over a local bare origin. One model run per test; every
assertion reads artifacts (origin refs, pushed trees, gate state, marker
files), never the transcript."""

from __future__ import annotations

import posixpath
import shlex

import pytest

from .harness import add_hook, add_stage, configure_omc, make_work_repo, require_token, run_in
from .lifecycle_helpers import NEEDS_VERIFY_HOOK

pytestmark = pytest.mark.e2e

BRANCH = "feature/hooked"
_VERIFY_MARKER = "/tmp/omc-verify-ran"
_CHECK_MARKER = "/tmp/omc-check-ran"
_POST_REBASE_MARKER = "/tmp/omc-post-rebase-ran"
_CHANGELOG_LINE = "- pushed through the MR gate"


def _claude(container, repo, command, *tools):
    rc, out = run_in(
        container,
        ["claude", "-p", command, "--output-format", "text", "--allowed-tools", *tools],
        cwd=repo,
        timeout=900,
    )
    return rc, out


def _git(container, repo, *args):
    rc, out = run_in(container, ["git", "-C", repo, *args], stdout_only=True)
    assert rc == 0, f"git {' '.join(args)} failed: {out}"
    return out.strip()


def _commit_all(container, repo, message):
    rc, out = run_in(
        container, ["bash", "-c", f"git add -A && git commit -qm {shlex.quote(message)}"], cwd=repo
    )
    assert rc == 0, out


def _write(container, repo, rel, text):
    script = (
        "import sys; from pathlib import Path; p = Path(sys.argv[1]); "
        "p.parent.mkdir(parents=True, exist_ok=True); p.write_text(sys.argv[2], encoding='utf-8')"
    )
    rc, out = run_in(container, ["python3", "-c", script, posixpath.join(repo, rel), text])
    assert rc == 0, out


def _verified_tree(container, repo):
    path = _git(container, repo, "rev-parse", "--git-path", "omc/verified-tree")
    rc, tree = run_in(container, ["cat", posixpath.join(repo, path)], stdout_only=True)
    assert rc == 0, f"no green verify was recorded: {tree}"
    return tree.strip()


def _pushed_tree(container, repo):
    return _git(container, f"{repo}-origin", "rev-parse", f"{BRANCH}^{{tree}}")


def _feature_with_verify_stage(container):
    """A feature branch with a product commit, a verify stage that leaves a
    marker, and a verbatim copy of this repo's needs_verify hook, all committed;
    then the gate records the branch's tree as verified."""
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["git", "switch", "-qc", BRANCH], cwd=repo)
    assert rc == 0, out
    _write(container, repo, "greeting.py", "def greeting():\n    return 'Hello, world!'\n")
    add_stage(
        container,
        repo,
        "verify",
        f"echo verified >> {_VERIFY_MARKER} && echo VERIFY-OK",
        "Exit 0, after VERIFY-OK is printed. Any other exit is failed.",
    )
    _write(
        container,
        repo,
        ".omc/hooks/needs_verify/SKILL.md",
        NEEDS_VERIFY_HOOK.read_text(encoding="utf-8"),
    )
    _commit_all(container, repo, "add greeting, verify stage and needs_verify")
    rc, out = run_in(container, ["omc", "internal", "gate", "record", "verify"], cwd=repo)
    assert rc == 0, f"gate record verify failed: {out}"
    return repo


def test_docs_only_change_skips_verify_by_needs_verify(container):
    repo = _feature_with_verify_stage(container)
    recorded = _verified_tree(container, repo)
    _write(container, repo, "docs/notes.md", "# Notes\n\nA documentation-only change.\n")
    _commit_all(container, repo, "docs: add notes")

    rc, out = _claude(container, repo, "/omc:finish", "Bash", "Read", "Skill")
    assert rc == 0, out

    rc, _ = run_in(
        container, ["git", "-C", f"{repo}-origin", "cat-file", "-e", f"{BRANCH}:docs/notes.md"]
    )
    assert rc == 0, f"{BRANCH} is not on origin with docs/notes.md\ntranscript:\n{out[-2000:]}"
    rc, _ = run_in(container, ["test", "-e", _VERIFY_MARKER])
    assert rc != 0, f"verify ran on a docs-only change\ntranscript:\n{out[-2000:]}"
    assert _verified_tree(container, repo) == recorded, "a verify was recorded without running"


def test_product_change_runs_verify_by_needs_verify(container):
    repo = _feature_with_verify_stage(container)
    _write(container, repo, "greeting.py", "def greeting():\n    return 'Hello, there!'\n")
    _commit_all(container, repo, "greeting: say hello there")

    rc, out = _claude(container, repo, "/omc:finish", "Bash", "Read", "Skill")
    assert rc == 0, out

    rc, _ = run_in(container, ["test", "-e", _VERIFY_MARKER])
    assert rc == 0, f"verify did not run on a product change\ntranscript:\n{out[-2000:]}"
    assert _verified_tree(container, repo) == _pushed_tree(container, repo), (
        f"the pushed tree is not the verified tree\ntranscript:\n{out[-2000:]}"
    )


_PRE_PUSH_PATH = ".omc/hooks/pre_push/SKILL.md"
_PRE_PUSH_BODY = (
    "From the project root run `grep -qxF -- '" + _CHANGELOG_LINE + "' CHANGELOG.md`.\n\n"
    "- Exit 0 → answer\n"
    '  `OMC_HOOK {"hook": "pre_push", "ok": true, '
    '"reason": "CHANGELOG.md records the push"}`.\n'
    "- Any other exit → answer\n"
    '  `OMC_HOOK {"hook": "pre_push", "ok": false, '
    '"reason": "CHANGELOG.md must contain the line ' + _CHANGELOG_LINE + '"}`.\n\n'
    "End with exactly that one OMC_HOOK line, plain text, no backticks or code fences."
)


def test_red_pre_push_is_fixed_then_pushed(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["git", "switch", "-qc", BRANCH], cwd=repo)
    assert rc == 0, out
    _write(container, repo, "greeting.py", "def greeting():\n    return 'Hello, world!'\n")
    _write(container, repo, "CHANGELOG.md", "# Changelog\n\n- add greeting()\n")
    add_stage(
        container,
        repo,
        "verify",
        f"echo verified >> {_VERIFY_MARKER} && echo VERIFY-OK",
        "Exit 0, after VERIFY-OK is printed. Any other exit is failed.",
    )
    add_hook(container, repo, "pre_push", _PRE_PUSH_BODY)
    _commit_all(container, repo, "add greeting, changelog, verify stage and pre_push")
    original_hook = _git(container, repo, "show", f"HEAD:{_PRE_PUSH_PATH}")

    rc, out = _claude(
        container, repo, "/omc:finish", "Bash", "Read", "Edit", "Write", "Skill", "Agent", "Task"
    )
    assert rc == 0, out

    origin = f"{repo}-origin"
    rc, changelog = run_in(
        container, ["git", "-C", origin, "show", f"{BRANCH}:CHANGELOG.md"], stdout_only=True
    )
    assert rc == 0, f"{BRANCH} never reached origin\ntranscript:\n{out[-2000:]}"
    assert _CHANGELOG_LINE in changelog.splitlines(), (
        f"the pushed CHANGELOG.md lacks the pre_push line: {changelog!r}"
    )
    assert _git(container, origin, "show", f"{BRANCH}:{_PRE_PUSH_PATH}") == original_hook, (
        "the fix edited the pre_push hook instead of the code"
    )
    assert _verified_tree(container, repo) == _pushed_tree(container, repo), (
        f"the pushed tree is not the verified tree\ntranscript:\n{out[-2000:]}"
    )


_POST_REBASE_BODY = (
    f"From the project root run `touch {_POST_REBASE_MARKER}`. Then answer\n"
    '`OMC_HOOK {"hook": "post_rebase", "ok": true, "before_implementation": true, '
    '"reason": "nothing is implemented on this branch yet"}`.\n\n'
    "End with exactly that one OMC_HOOK line, plain text, no backticks or code fences."
)


# Spec §6's "conflicted start rebase" folds into a standalone /omc:rebase-main
# (plan decision 3): /omc:start's freshness gate enters the same skill. A
# standalone run in a primary checkout has nothing to rebase, so the branch
# lives in a linked worktree, as `omc design` makes it.
def test_post_rebase_before_implementation_skips_the_check(container):
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    worktree = f"{repo}-hooked"
    rc, out = run_in(container, ["git", "worktree", "add", "-q", "-b", BRANCH, worktree], cwd=repo)
    assert rc == 0, out
    _write(container, worktree, "README.md", "hi from the branch\n")
    add_stage(
        container,
        worktree,
        "check",
        f"echo checked > {_CHECK_MARKER} && echo CHECK-OK",
        "Exit 0, after CHECK-OK is printed. Any other exit is failed.",
    )
    add_hook(container, worktree, "post_rebase", _POST_REBASE_BODY)
    _commit_all(container, worktree, "branch greeting, check stage and post_rebase")
    # main moves on with a change to the same line: the rebase conflicts
    _write(container, repo, "README.md", "hi from main\n")
    _commit_all(container, repo, "main greets")
    rc, out = run_in(container, ["git", "push", "-q", "origin", "main"], cwd=repo)
    assert rc == 0, out

    rc, out = _claude(
        container, worktree, "/omc:rebase-main", "Bash", "Read", "Edit", "Write", "Skill"
    )
    assert rc == 0, out

    transcript = f"\ntranscript:\n{out[-2000:]}"
    assert _git(container, worktree, "symbolic-ref", "--short", "HEAD") == BRANCH, (
        f"the rebase is still paused or left {BRANCH}{transcript}"
    )
    origin_main = _git(container, worktree, "rev-parse", "origin/main")
    assert _git(container, worktree, "merge-base", "origin/main", "HEAD") == origin_main, (
        f"{BRANCH} is not on the moved base{transcript}"
    )
    assert _git(container, worktree, "rev-list", "--count", "origin/main..HEAD") == "1", transcript
    rc, markers = run_in(
        container, ["git", "-C", worktree, "grep", "-nE", "^(<<<<<<<|=======$|>>>>>>>)", "HEAD"]
    )
    assert rc == 1, f"conflict markers left in HEAD: {markers}{transcript}"
    rc, _ = run_in(container, ["test", "-e", _POST_REBASE_MARKER])
    assert rc == 0, f"post_rebase never ran after the base moved{transcript}"
    rc, _ = run_in(container, ["test", "-e", _CHECK_MARKER])
    assert rc != 0, f"the check ran although post_rebase said before_implementation{transcript}"
