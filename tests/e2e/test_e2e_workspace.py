"""Workspace membership and resumable closure with real Git and Worktrunk."""

from __future__ import annotations

import json

import pytest

from .harness import configure_omc, make_work_repo, run_in

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(300)]


def test_workspace_clones_reuses_lists_and_resumes_close(container):
    configure_omc(container, "claude")

    def run(argv, *, cwd=None, expected=0):
        rc, out = run_in(container, argv, cwd=cwd, timeout=60)
        assert rc == expected, out
        return out

    def write(path, content):
        run(
            [
                "python3",
                "-c",
                "import pathlib, sys; p = pathlib.Path(sys.argv[1]); "
                "p.parent.mkdir(parents=True, exist_ok=True); p.write_text(sys.argv[2])",
                path,
                content,
            ]
        )

    def workspace(cwd, *args, expected=0):
        out = run(["omc", "internal", "workspace", *args], cwd=cwd, expected=expected)
        verdicts = [line for line in out.splitlines() if line.startswith("OMC_WORKSPACE ")]
        assert len(verdicts) == 1, out
        return json.loads(verdicts[0].removeprefix("OMC_WORKSPACE "))

    def ledger():
        return json.loads(run(["cat", "/root/.omc/workspaces.json"]))

    def worktrees(primary):
        out = run(["git", "worktree", "list", "--porcelain"], cwd=primary)
        return [
            line.removeprefix("worktree ")
            for line in out.splitlines()
            if line.startswith("worktree ")
        ]

    master = make_work_repo(container, "/work/projects/master")
    dependency = make_work_repo(container, "/work/projects/dependency")
    for primary, prefix, base in (
        (master, "change/", "main"),
        (dependency, "fix/", "develop"),
    ):
        if base != "main":
            run(["git", "branch", "-m", base], cwd=primary)
        write(
            f"{primary}/.omc/config.yaml",
            f"worktree:\n  branch_prefix: '{prefix}'\n  base_branch: '{base}'\n",
        )
        run(["git", "add", ".omc"], cwd=primary)
        run(["git", "commit", "-qm", "configure workspace repository"], cwd=primary)
        run(["git", "push", "-qu", "origin", base], cwd=primary)
        run(["git", "symbolic-ref", "HEAD", f"refs/heads/{base}"], cwd=f"{primary}-origin")

    run(
        [
            "wt",
            "-C",
            master,
            "switch",
            "--create",
            "change/shared",
            "--base",
            "origin/main",
            "--no-cd",
            "--yes",
            "--format=json",
        ]
    )
    master_tree = "/work/projects/master.change-shared"
    assert worktrees(master) == [master, master_tree]
    record = "docs/superpowers/specs/2026-10-08-shared-design.md"
    write(f"{master_tree}/{record}", "# Shared workspace design\n")
    run(["git", "add", record], cwd=master_tree)
    run(["git", "commit", "-qm", "record shared design"], cwd=master_tree)

    # Remove the sibling checkout, leaving the origin whose basename must be
    # substituted into the master's origin URL. --path chooses a separate clone.
    run(["mkdir", "-p", "/work/prepared"])
    run(["mv", dependency, "/work/prepared/dependency"])
    added = workspace(master_tree, "add", "dependency-origin", "--path", dependency)
    dependency_tree = "/work/projects/dependency.fix-shared"
    dependency_entry = {
        "key": "/work/projects/dependency-origin",
        "primary": dependency,
        "worktree": dependency_tree,
        "branch": "fix/shared",
        "base": "develop",
        "role": "dependency",
    }
    master_entry = {
        "key": "/work/projects/master-origin",
        "primary": master,
        "worktree": master_tree,
        "branch": "change/shared",
        "base": "main",
        "role": "master",
    }
    assert added["ok"] is True
    assert added["repository"] == dependency_entry
    assert added["repositories"] == [dependency_entry, master_entry]
    assert run(["git", "remote", "get-url", "origin"], cwd=dependency).strip() == (
        "/work/projects/dependency-origin"
    )
    run(["test", "-d", f"{dependency}/.git"])
    run(["test", "-f", f"{dependency_tree}/.git"])
    assert worktrees(dependency) == [dependency, dependency_tree]
    assert run(["git", "branch", "--show-current"], cwd=dependency_tree).strip() == "fix/shared"

    before = ledger()
    identity = '["/work/projects/master-origin","shared"]'
    assert before == {
        "version": 1,
        "workspaces": {
            identity: {
                "master": "/work/projects/master-origin",
                "slug": "shared",
                "repositories": [master_entry, dependency_entry],
            }
        },
    }
    # This time the plain name resolves the newly cloned sibling primary.
    reused = workspace(master_tree, "add", "dependency")
    assert reused == added
    assert ledger() == before
    assert worktrees(dependency) == [dependency, dependency_tree]

    for cwd, role in ((master_tree, "master"), (dependency_tree, "dependency")):
        listed = workspace(cwd, "list")
        assert listed["ok"] is True
        assert listed["slug"] == "shared"
        assert listed["current_role"] == role
        assert listed["master_worktree"] == master_tree
        dep_status, master_status = listed["repositories"]
        for status, entry in ((dep_status, dependency_entry), (master_status, master_entry)):
            assert {key: status[key] for key in entry} == entry
            assert status["upstream"] is None
            assert status["ahead"] is None
            assert status["behind"] is None
            assert status["compare_url"] is None
        assert dep_status["record"]["ok"] is False
        assert dep_status["record"]["reason"] == "missing"
        assert master_status["record"] == {"ok": True, "slug": "shared", "path": record}

    # Both branches have real changes, so closure cannot mistake an untouched
    # dependency branch at its base for a successfully merged implementation.
    write(f"{dependency_tree}/change.txt", "dependency implementation\n")
    run(["git", "add", "change.txt"], cwd=dependency_tree)
    run(["git", "commit", "-qm", "implement dependency"], cwd=dependency_tree)
    for entry in (dependency_entry, master_entry):
        run(["git", "push", "-qu", "origin", entry["branch"]], cwd=entry["worktree"])

    write(f"{dependency_tree}/change.txt", "dependency follow-up\n")
    run(["git", "commit", "-qam", "follow up dependency"], cwd=dependency_tree)
    dep_status, master_status = workspace(master_tree, "list")["repositories"]
    assert (dep_status["ahead"], dep_status["behind"]) == (1, 0)
    assert (master_status["ahead"], master_status["behind"]) == (0, 0)
    run(["git", "push", "-q"], cwd=dependency_tree)
    for status in workspace(dependency_tree, "list")["repositories"]:
        assert status["upstream"]["remote"] == "origin"
        assert status["upstream"]["branch"] == status["branch"]
        assert (status["ahead"], status["behind"]) == (0, 0)

    refused = workspace(master_tree, "close", expected=2)
    assert refused["ok"] is False
    assert refused["reason"] == "unmerged"
    assert refused["repository"] == dependency_entry
    assert ledger() == before
    assert worktrees(dependency) == [dependency, dependency_tree]
    assert worktrees(master) == [master, master_tree]

    # Merge through the primaries, then advance the bare origins as a forge
    # would. Close removes the merged dependency and retains the unmerged master.
    run(["git", "merge", "--ff-only", "fix/shared"], cwd=dependency)
    run(["git", "push", "-q", "origin", "develop"], cwd=dependency)
    partial = workspace(master_tree, "close", expected=2)
    assert partial["ok"] is False
    assert partial["reason"] == "unmerged"
    assert partial["repository"] == master_entry
    assert worktrees(dependency) == [dependency]
    run(["test", "!", "-e", dependency_tree])
    assert worktrees(master) == [master, master_tree]
    remaining = ledger()
    assert remaining["workspaces"][identity]["repositories"] == [master_entry]
    assert len(remaining["workspaces"]) == 1
    assert workspace(master_tree, "list")["repositories"][0]["role"] == "master"

    run(["git", "merge", "--ff-only", "change/shared"], cwd=master)
    run(["git", "push", "-q", "origin", "main"], cwd=master)
    closed = workspace(master_tree, "close")
    assert closed["ok"] is True
    assert closed["removed"] == [master_entry]
    assert closed["repositories"] == []
    assert closed["current_role"] is None
    assert closed["master_worktree"] is None
    assert ledger() == {"version": 1, "workspaces": {}}
    assert worktrees(master) == [master]
    run(["test", "!", "-e", master_tree])
