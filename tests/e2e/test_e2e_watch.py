"""Live `omc watch --once`: real git sync and GitNexus reindex. The auto-build
hook test runs a real .omc/bin/build and needs no LLM token."""

from __future__ import annotations

import json
import re
import uuid

import pytest

from .harness import (
    add_stage_hook,
    configure_omc,
    hold_watch_stand_in,
    hook_instructions,
    make_work_repo,
    run_in,
)

pytestmark = pytest.mark.e2e


def _push_remote_commit(container, repo, *, project_id: str | None = None):
    """A teammate's commit on origin/main; with `project_id`, it also commits
    `.omc/project.id` with that ID."""
    commit_id = f"mkdir -p .omc && echo {project_id} > .omc/project.id && " if project_id else ""
    script = (
        f"git clone -q {repo}-origin /work/other && cd /work/other && "
        f"echo teammate > t.txt && {commit_id}git add -A && git commit -qm 'remote change' && "
        "git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_watch_once_syncs_and_reindexes_for_real(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    # The primary made its own untracked ID before the teammate committed theirs:
    # git refuses the ff-merge over it, and watch yields to the committed one.
    local_id, committed_id = str(uuid.uuid4()), str(uuid.uuid4())
    rc, out = run_in(
        container, ["bash", "-c", f"mkdir -p .omc && echo {local_id} > .omc/project.id"], cwd=repo
    )
    assert rc == 0, out
    _push_remote_commit(container, repo, project_id=committed_id)

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "synced main" in out, out
    rc, _ = run_in(container, ["test", "-f", f"{repo}/t.txt"])
    assert rc == 0, "remote commit did not arrive via ff-sync"
    rc, project_id = run_in(container, ["cat", f"{repo}/.omc/project.id"])
    assert rc == 0 and project_id.strip() == committed_id, (
        f"the committed project ID did not win: {project_id!r}\n{out[:1500]}"
    )
    # Clean but for the wt starter the watch seeds untracked (asserted below):
    # no `.omc/project.id` left modified or untracked, the index is self-ignored.
    rc, status = run_in(container, ["git", "status", "--porcelain"], cwd=repo, stdout_only=True)
    assert rc == 0 and status.splitlines() == ["?? .config/"], status
    # the REAL gitnexus analyze ran and produced an index
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, f"watch did not build a real index:\n{out[:1500]}"
    # the wt starter got seeded (create-if-absent)
    rc, _ = run_in(container, ["test", "-f", f"{repo}/.config/wt.toml"])
    assert rc == 0, "ensure_wt_config did not seed the starter"


def test_configure_in_repo_seeds_project_and_global_instructions(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["rm", "/root/.claude/CLAUDE.md"])
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "configure", "--set", "llm.default=claude"], cwd=repo)
    assert rc == 0, out
    for name in ("AGENTS.md", "CLAUDE.md"):
        rc, _ = run_in(container, ["test", "-e", f"{repo}/{name}"])
        assert rc == 1, f"configure created root {name}"
        rc, _ = run_in(container, ["test", "-L", f"{repo}/{name}"])
        assert rc == 1, f"configure created root {name} symlink"
    # The fixture ships a one-line .gitignore; configure must leave it as is.
    rc, ignored = run_in(container, ["cat", f"{repo}/.gitignore"])
    assert rc == 0 and ignored == ".claude/settings.local.json\n", (
        f"configure touched .gitignore: {ignored!r}"
    )
    rc, _ = run_in(container, ["test", "-L", f"{repo}/.gitignore"])
    assert rc == 1, "configure replaced root .gitignore with a symlink"
    rc, global_instructions = run_in(container, ["cat", "/root/.claude/CLAUDE.md"])
    assert rc == 0, global_instructions
    assert global_instructions.count("<!-- omc:begin ") == 1
    assert "# omc behavior layer" in global_instructions
    rc, _ = run_in(container, ["test", "-f", f"{repo}/.omc/config/AGENTS.md"])
    assert rc == 0, "project layer not seeded"


def test_watch_once_up_to_date_still_refreshes_index(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "up to date" in out, out
    # --once checks now and repairs whatever is stale: the REAL index exists
    # afterwards even when there are no new commits
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, f"--once left the index missing:\n{out[:1500]}"


def _seed_container_hook(container, repo, body):
    script = (
        f"mkdir -p {repo}/.omc/hooks && cat > {repo}/.omc/hooks/post-watch.sh <<'EOF'\n{body}\nEOF"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_watch_once_runs_project_post_watch_hook_for_real(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_container_hook(container, repo, 'echo "$OMC_WATCH_OUTCOME" > marker.txt')

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "running project post-watch hook (.omc/hooks/post-watch.sh)" in out, out
    assert "post-watch hook done" in out, out
    # hook really ran, in the repo root, with the outcome env var
    rc, marker = run_in(container, ["cat", f"{repo}/marker.txt"])
    assert rc == 0 and marker.strip() == "refreshed", marker


def test_watch_once_failing_hook_links_log_and_exits_zero(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_container_hook(container, repo, "echo boom-e2e >&2\nexit 1")

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out  # hook failure never breaks --once
    m = re.search(r"post-watch hook failed \(exit 1\) — log: (\S+)", out)
    assert m, f"missing failure narration:\n{out}"
    rc, log = run_in(container, ["cat", m.group(1)])
    assert rc == 0 and "boom-e2e" in log, log


def test_watch_auto_build_skips_when_unconfigured(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "watch", "--once", "--auto-build"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "no project build stage configured — skipping auto-build" in out, out


def test_watch_auto_build_runs_build_hook(container):
    """--auto-build runs the real `.omc/bin/build` through the stage runner; no LLM."""
    # Deliberately NOT configure_omc: its model pin is a live `claude` probe, and
    # auto-build asks no provider anything. `llm.default` alone probes nothing; watch
    # needs only a configured omc and the provider CLI on PATH (its tool probe).
    rc, out = run_in(container, ["omc", "configure", "--set", "llm.default=claude"], timeout=60)
    assert rc == 0, out
    repo = make_work_repo(container)
    instructions = hook_instructions(
        timeout="5m",
        quiet="2m",
        done="The hook exits once it has written `marker`. Nothing is held.",
        passed="Exit 0. Any other exit is failed.",
        fixing="Read the log. Never edit `.omc/bin/*`: that is the stage itself.",
    )
    add_stage_hook(container, repo, "build", "echo built > marker; exit 0", instructions)
    rc, out = run_in(
        container,
        ["bash", "-c", "git add -A && git commit -qm 'build hook' && git push -q origin main"],
        cwd=repo,
    )
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "watch", "--once", "--auto-build"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "running project build stage (.omc/bin/build)" in out, out
    assert "auto-build passed" in out, out
    rc, marker = run_in(container, ["cat", f"{repo}/marker"])
    assert rc == 0 and marker.strip() == "built", f"the build hook did not run:\n{out}"


def _json_file(container, path):
    rc, raw = run_in(container, ["cat", path], timeout=5)
    assert rc == 0, f"no {path}: {raw}"
    return json.loads(raw)


def _file_hex(container, path) -> str:
    rc, out = run_in(
        container,
        ["python3", "-c", "import sys; print(open(sys.argv[1], 'rb').read().hex())", path],
        timeout=5,
    )
    assert rc == 0, out
    return out.strip()


def _stage_answer(out: str) -> dict:
    lines = [line for line in out.splitlines() if line.startswith("OMC_STAGE_RUN ")]
    assert len(lines) == 1, out
    return json.loads(lines[0][len("OMC_STAGE_RUN ") :])


# Worst case under the default 300 s ceiling: 10 (configure) + 5 (repo) + 2 x 5 (the
# hook and its template) + 5 (commit) + 140 (watch --once: sync, a three-file repo's
# embedding index, the build) + 4 x 5 (the primary's ids, record and project id) + 20
# (wt switch, copying the ignored files) + 5 (the copied id) + 30 (the stand-in takes
# the lock) + 3 x 10 (wait, start, wait: the hook exits at once) + 5 x 5 (the checks)
# = 300 s.
def test_watch_auto_build_cache_copied_into_a_worktree_is_rekeyed(container):
    """A worktree cut after an auto-build gets the primary's `.cache/` through the
    seeded wt config's copy-ignored step; its first stage command re-keys the copied
    `worktree.id`, so it never sees the primary's run, and its own build leaves the
    primary's record alone. No LLM: as test_watch_auto_build_runs_build_hook."""
    rc, out = run_in(container, ["omc", "configure", "--set", "llm.default=claude"], timeout=10)
    assert rc == 0, out
    repo = make_work_repo(container, timeout=5)
    instructions = hook_instructions(
        timeout="5m",
        quiet="2m",
        done="The hook exits once it has recorded the build. Nothing is held.",
        passed="Exit 0. Any other exit is failed.",
        fixing="Read the log. Never edit `.omc/bin/*`: that is the stage itself.",
    )
    # Outside the checkout: an untracked marker would make the worktree dirty.
    add_stage_hook(
        container, repo, "build", 'echo "$PWD" >> /tmp/omc-copied-builds; exit 0', instructions
    )
    rc, out = run_in(
        container,
        ["bash", "-c", "git add -A && git commit -qm 'build hook' && git push -q origin main"],
        cwd=repo,
        timeout=5,
    )
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "watch", "--once", "--auto-build"], cwd=repo, timeout=140)
    assert rc == 0, out
    assert "auto-build passed" in out, out
    primary_wid = _json_file(container, f"{repo}/.cache/worktree.id")
    primary_last = _json_file(container, f"{repo}/.cache/omc/last-build.json")
    primary_last_hex = _file_hex(container, f"{repo}/.cache/omc/last-build.json")
    rc, project_id = run_in(container, ["cat", f"{repo}/.omc/project.id"], timeout=5)
    assert rc == 0, project_id
    project_id = project_id.strip()

    rc, out = run_in(
        container,
        [
            "wt",
            "switch",
            "--create",
            "feature/copied",
            "--base",
            "origin/main",
            "--no-cd",
            "--yes",
            "--format=json",
        ],
        cwd=repo,
        timeout=20,
        stdout_only=True,
    )
    assert rc == 0, out
    worktree = json.loads(out)["path"]
    copied = _json_file(container, f"{worktree}/.cache/worktree.id")
    assert copied == primary_wid, f"wt did not copy the primary's .cache/: {copied}"

    hold_watch_stand_in(container, repo)
    rc, out = run_in(
        container, ["omc", "internal", "stage", "wait", "build"], cwd=worktree, timeout=10
    )
    assert rc == 1, f"the worktree saw the primary's copied build run:\n{out}"
    rc, out = run_in(
        container,
        ["omc", "internal", "stage", "start", "build"],
        cwd=worktree,
        timeout=10,
        stdout_only=True,
    )
    assert rc == 0, out
    started = _stage_answer(out)
    rc, out = run_in(
        container,
        ["omc", "internal", "stage", "wait", "build"],
        cwd=worktree,
        timeout=10,
        stdout_only=True,
    )
    assert rc == 0, out
    answer = _stage_answer(out)
    assert answer["run"] == started["run"] and answer["state"] == "passed", answer

    rekeyed = _json_file(container, f"{worktree}/.cache/worktree.id")
    assert rekeyed["id"] != primary_wid["id"], rekeyed
    assert rekeyed["path"] == worktree, rekeyed
    last = _json_file(container, f"{worktree}/.cache/omc/last-build.json")
    assert last["run"] == answer["run"] != primary_last["run"], last
    assert last["worktree_id"] == rekeyed["id"], last
    assert f"/{project_id}/{rekeyed['id']}/build-" in last["log"], last
    assert _file_hex(container, f"{repo}/.cache/omc/last-build.json") == primary_last_hex, (
        "the worktree's build rewrote the primary's last-build.json"
    )
    rc, _ = run_in(container, ["test", "!", "-e", f"{worktree}/.omc/project.id"], timeout=5)
    assert rc == 0, "the worktree has its own .omc/project.id"
    rc, status = run_in(
        container, ["git", "status", "--porcelain"], cwd=worktree, timeout=5, stdout_only=True
    )
    assert rc == 0 and status == "", status


_CLI = "/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js"


def test_watch_once_heals_feature_branch_owned_index(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container, path="/work/heal-repo")
    # Arrange the inversion: FIRST index runs on a feature branch, so the
    # flat store is stamped with it; then the branch dies.
    rc, out = run_in(container, ["git", "switch", "-qc", "feature/first"], cwd=repo)
    assert rc == 0, out
    rc, out = run_in(
        container,
        ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills"],
        cwd=repo,
        timeout=300,
    )
    assert rc == 0, f"seed analyze failed:\n{out[:1500]}"
    rc, out = run_in(
        container,
        ["bash", "-c", f"grep -o '\"branch\"[^,]*' {repo}/.gitnexus/meta.json"],
    )
    assert "feature/first" in out, f"seed did not stamp the flat store:\n{out}"
    rc, out = run_in(container, ["git", "switch", "-q", "main"], cwd=repo)
    assert rc == 0, out
    rc, out = run_in(container, ["git", "branch", "-qD", "feature/first"], cwd=repo)
    assert rc == 0, out

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, f"watch --once failed:\n{out[:2000]}"
    assert "destroying and rebuilding" in out, out
    assert "index rebuilt for main" in out, out

    # flat store now belongs to main at HEAD; no shadow branch store; registry advanced
    check = (
        "import json, subprocess, sys\n"
        f"meta = json.load(open('{repo}/.gitnexus/meta.json'))\n"
        f"head = subprocess.run(['git', '-C', '{repo}', 'rev-parse', 'HEAD'],"
        " capture_output=True, text=True).stdout.strip()\n"
        "assert meta['branch'] == 'main', meta['branch']\n"
        "assert meta['lastCommit'] == head, (meta['lastCommit'], head)\n"
        "reg = json.load(open('/root/.gitnexus/registry.json'))\n"
        "entries = reg if isinstance(reg, list) else reg.get('repos', [])\n"
        f"entry = [e for e in entries if e.get('path') == '{repo}']\n"
        "assert entry and entry[0]['lastCommit'] == head, entry\n"
        "print('HEAL-OK')\n"
    )
    rc, out = run_in(container, ["python3", "-c", check])
    assert rc == 0 and "HEAL-OK" in out, out
    rc, _ = run_in(container, ["bash", "-c", f"ls {repo}/.gitnexus/branches 2>/dev/null | grep ."])
    assert rc != 0, "shadow branch store survived the heal"
