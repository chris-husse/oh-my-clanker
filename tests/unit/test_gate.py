"""`omc internal gate` — the deterministic half of the MR gate (spec 2026-11-16 §5.1).

Real git repositories under pytest's tmp: a bare origin holding `main`, a
primary clone, and feature worktrees. Assertions are on the single `OMC_GATE`
line, exit codes and the `--git-path` state files.
"""

import json
import subprocess
from pathlib import Path

import pytest

from omc.internal import run_internal


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _git_out(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_primary_with_origin(tmp_path):
    """A real primary clone with a bare origin holding main."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    primary = tmp_path / "primary"
    subprocess.run(["git", "clone", "-q", str(origin), str(primary)], check=True)
    _git("config", "user.email", "t@t", cwd=primary)
    _git("config", "user.name", "t", cwd=primary)
    (primary / "f.txt").write_text("one\n")
    _git("add", ".", cwd=primary)
    _git("commit", "-qm", "c1", cwd=primary)
    _git("branch", "-M", "main", cwd=primary)
    _git("push", "-q", "-u", "origin", "main", cwd=primary)
    return origin, primary


def _commit(wt, name="feature.txt", content="feature\n", msg="feature"):
    path = Path(wt) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git("add", ".", cwd=wt)
    _git("commit", "-qm", msg, cwd=wt)


def _add_worktree(primary, tmp_path, branch="feature/x", name="wt"):
    """A feature worktree with one commit of its own."""
    wt = tmp_path / name
    _git("worktree", "add", "-q", "-b", branch, str(wt), cwd=primary)
    _git("config", "user.email", "t@t", cwd=wt)
    _git("config", "user.name", "t", cwd=wt)
    _commit(wt, content=f"{branch}\n")
    return wt


def _feature(tmp_path):
    _, primary = _setup_primary_with_origin(tmp_path)
    return primary, _add_worktree(primary, tmp_path)


def _run(args, cwd, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "omchome"))
    rc = run_internal(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _gate_line(out):
    lines = [ln for ln in out.splitlines() if ln.startswith("OMC_GATE ")]
    assert len(lines) == 1, out
    return json.loads(lines[0].split(" ", 1)[1])


def _ask(gate, cwd, tmp_path, monkeypatch, capsys):
    rc, out, err = _run(["gate", gate], cwd, tmp_path, monkeypatch, capsys)
    assert rc == 0, err
    return _gate_line(out)


def _record(gate, cwd, tmp_path, monkeypatch, capsys):
    rc, out, err = _run(["gate", "record", gate], cwd, tmp_path, monkeypatch, capsys)
    assert rc == 0, err
    return out, err


def _tree(cwd, rev="HEAD"):
    return _git_out("rev-parse", f"{rev}^{{tree}}", cwd=cwd)


def _hook(root, gate):
    path = Path(root) / ".omc" / "hooks" / f"needs_{gate}" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: needs\n---\nanswer\n")
    return path


@pytest.mark.parametrize("gate", ["review", "verify"])
def test_unchanged_tree_runs_nothing(gate, tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _record(gate, wt, tmp_path, monkeypatch, capsys)
    v = _ask(gate, wt, tmp_path, monkeypatch, capsys)
    assert v["gate"] == gate
    assert v["run"] is False
    assert v["ask"] is False
    assert v["reason"] == "unchanged"
    assert v["previous"] == v["tree"] == _tree(wt)


def test_review_never_reviewed_without_hook_does_not_run(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    v = _ask("review", wt, tmp_path, monkeypatch, capsys)
    assert v["run"] is False
    assert v["ask"] is False
    assert v["reason"] == "no-hook"
    mb = _git_out("merge-base", "origin/main", "HEAD", cwd=wt)
    assert v["previous"] == _tree(wt, mb)
    assert v["tree"] == _tree(wt)
    assert "no review has run" in v["message"]
    assert "needs_review" in v["message"]


def test_review_changed_since_record_without_hook(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _record("review", wt, tmp_path, monkeypatch, capsys)
    recorded = _tree(wt)
    _commit(wt, name="more.txt")
    v = _ask("review", wt, tmp_path, monkeypatch, capsys)
    assert v["reason"] == "no-hook"
    assert v["run"] is False
    assert v["ask"] is False
    assert v["previous"] == recorded
    assert "no review has run" not in v["message"]


def test_verify_without_record_runs_without_asking(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _hook(wt, "verify")
    v = _ask("verify", wt, tmp_path, monkeypatch, capsys)
    assert v["run"] is True
    assert v["ask"] is False
    assert v["reason"] == "no-record"
    assert v["previous"] is None


def test_verify_changed_without_hook_runs(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _record("verify", wt, tmp_path, monkeypatch, capsys)
    _commit(wt, name="more.txt")
    v = _ask("verify", wt, tmp_path, monkeypatch, capsys)
    assert v["run"] is True
    assert v["ask"] is False
    assert v["reason"] == "no-hook"


@pytest.mark.parametrize("gate", ["review", "verify"])
def test_changed_tree_with_hook_asks(gate, tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _hook(wt, gate)
    _git("add", ".", cwd=wt)
    _git("commit", "-qm", "hook", cwd=wt)
    _record(gate, wt, tmp_path, monkeypatch, capsys)
    recorded = _tree(wt)
    _commit(wt, name="more.txt")
    v = _ask(gate, wt, tmp_path, monkeypatch, capsys)
    assert v["run"] is True
    assert v["ask"] is True
    assert v["reason"] == "hook"
    assert v["previous"] == recorded
    assert v["tree"] == _tree(wt)


def test_hook_only_in_primary_is_not_seen(tmp_path, monkeypatch, capsys):
    primary, wt = _feature(tmp_path)
    _hook(primary, "verify")
    _record("verify", wt, tmp_path, monkeypatch, capsys)
    _commit(wt, name="more.txt")
    v = _ask("verify", wt, tmp_path, monkeypatch, capsys)
    assert v["reason"] == "no-hook"


def test_record_writes_the_git_path_file(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    rc, out, err = _run(["gate", "record", "verify"], wt, tmp_path, monkeypatch, capsys)
    assert rc == 0
    assert out == ""
    assert err.strip()
    state = Path(wt) / _git_out("rev-parse", "--git-path", "omc/verified-tree", cwd=wt)
    assert state.read_text() == _tree(wt) + "\n"


def test_record_refuses_a_dirty_tracked_tree(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    state = Path(wt) / _git_out("rev-parse", "--git-path", "omc/verified-tree", cwd=wt)
    (Path(wt) / "f.txt").write_text("dirty\n")
    rc, out, err = _run(["gate", "record", "verify"], wt, tmp_path, monkeypatch, capsys)
    assert rc == 2
    assert out == ""
    assert "error:" in err
    assert not state.exists()

    _git("checkout", "--", "f.txt", cwd=wt)
    (Path(wt) / "untracked.log").write_text("artifact\n")
    rc, _, err = _run(["gate", "record", "verify"], wt, tmp_path, monkeypatch, capsys)
    assert rc == 0, err
    assert state.read_text() == _tree(wt) + "\n"


def test_worktrees_keep_separate_state(tmp_path, monkeypatch, capsys):
    primary, wt_a = _feature(tmp_path)
    wt_b = _add_worktree(primary, tmp_path, branch="feature/y", name="wt-b")
    _record("verify", wt_a, tmp_path, monkeypatch, capsys)
    assert _ask("verify", wt_a, tmp_path, monkeypatch, capsys)["reason"] == "unchanged"
    v = _ask("verify", wt_b, tmp_path, monkeypatch, capsys)
    assert v["reason"] == "no-record"


def test_message_only_amend_keeps_the_record(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _record("verify", wt, tmp_path, monkeypatch, capsys)
    _git("commit", "--amend", "-qm", "other", cwd=wt)
    v = _ask("verify", wt, tmp_path, monkeypatch, capsys)
    assert v["reason"] == "unchanged"


def test_stale_state_from_another_branch_asks_again(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _record("verify", wt, tmp_path, monkeypatch, capsys)
    _git("switch", "-q", "-c", "feature/b", "origin/main", cwd=wt)
    _commit(wt, name="other.txt", content="different\n")
    v = _ask("verify", wt, tmp_path, monkeypatch, capsys)
    assert v["reason"] != "unchanged"
    assert v["run"] is True


def test_question_line_has_the_seven_fields(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    for gate in ("review", "verify"):
        v = _ask(gate, wt, tmp_path, monkeypatch, capsys)
        assert list(v) == ["gate", "run", "ask", "reason", "tree", "previous", "message"]
        assert isinstance(v["message"], str) and v["message"]


@pytest.mark.parametrize(
    "argv",
    [
        ["gate"],
        ["gate", "bogus"],
        ["gate", "review", "x"],
        ["gate", "record"],
        ["gate", "record", "bogus"],
    ],
)
def test_gate_usage_errors(argv, tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    rc, out, err = _run(argv, wt, tmp_path, monkeypatch, capsys)
    assert rc == 2
    assert "usage:" in err
    assert "gate [record] review|verify" in err
    assert out == ""


@pytest.mark.parametrize("argv", [["gate", "review"], ["gate", "record", "verify"]])
def test_gate_outside_a_repository_refuses(argv, tmp_path, monkeypatch, capsys):
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    rc, out, err = _run(argv, nowhere, tmp_path, monkeypatch, capsys)
    assert rc == 2
    assert "error: not inside a git repository" in err
    assert out == ""


def test_review_without_origin_is_a_git_error(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "lonely"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _commit(repo, name="f.txt", content="one\n", msg="c1")
    rc, out, err = _run(["gate", "review"], repo, tmp_path, monkeypatch, capsys)
    assert rc == 1
    assert "error:" in err
    assert "OMC_GATE" not in out


# Messages name what the tree is compared with: a branch with no recorded review
# compares against its merge-base, so its messages never cite a recorded review.


def test_review_never_reviewed_unchanged_message_names_the_merge_base(
    tmp_path, monkeypatch, capsys
):
    _, wt = _feature(tmp_path)
    _git("revert", "--no-edit", "HEAD", cwd=wt)  # the tree is the merge-base tree again
    v = _ask("review", wt, tmp_path, monkeypatch, capsys)
    assert v["reason"] == "unchanged"
    assert v["run"] is False and v["ask"] is False
    assert "since the last recorded review" not in v["message"]
    assert "no review has run on this branch" in v["message"]
    assert "merge-base" in v["message"]


def test_review_never_reviewed_hook_message_names_the_merge_base(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _hook(wt, "review")
    _git("add", ".", cwd=wt)
    _git("commit", "-qm", "hook", cwd=wt)
    v = _ask("review", wt, tmp_path, monkeypatch, capsys)
    assert v["reason"] == "hook"
    assert v["run"] is True and v["ask"] is True
    assert "since the last recorded review" not in v["message"]
    assert "no review has run on this branch" in v["message"]
    assert "`needs_review` hook decides" in v["message"]


def test_recorded_messages_cite_the_last_recorded_gate(tmp_path, monkeypatch, capsys):
    _, wt = _feature(tmp_path)
    _record("review", wt, tmp_path, monkeypatch, capsys)
    v = _ask("review", wt, tmp_path, monkeypatch, capsys)
    assert v["message"] == "the tree is unchanged since the last recorded review — no review runs"


@pytest.mark.parametrize("gate", ["review", "verify"])
def test_no_record_message_is_built_from_the_gate_name(gate):
    from omc.gate import _decide

    other = "verify" if gate == "review" else "review"
    v = _decide(gate, "t", None, False, False)
    assert v.reason == "no-record"
    assert v.message == f"no green {gate} is recorded in this worktree — {gate} runs"
    assert other not in v.message
