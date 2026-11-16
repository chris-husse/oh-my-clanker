import json
import shutil
import subprocess
import threading
import uuid

import pytest

from omc.errors import OmcError, Refusal
from omc.stageid import ensure_cache, project_id, resolve_ids, worktree_id
from omc.toolctx import ToolContext

from . import _stagekit
from ._stubs import host_bin


def _ctx():
    return ToolContext.from_env(_stagekit.hermetic_env())


def test_project_id_created_once_and_stable(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    value = project_id(_ctx(), repo)
    assert project_id(_ctx(), repo) == value
    uuid.UUID(value)
    assert (repo / ".omc/project.id").read_text() == value + "\n"


def test_project_id_from_worktree_lands_in_primary(tmp_path, monkeypatch):
    repo = _stagekit.git_repo(tmp_path / "repo")
    worktree = _stagekit.add_worktree(repo, tmp_path / "wt", "feat")
    monkeypatch.chdir(worktree)
    ids = resolve_ids(_ctx())
    assert ids.project == (repo / ".omc/project.id").read_text().strip()
    assert not (worktree / ".omc/project.id").exists()
    assert ids.primary.resolve() == repo.resolve()
    assert ids.root.resolve() == worktree.resolve()
    assert ids.worktree == json.loads((worktree / ".cache/worktree.id").read_text())["id"]


def test_project_id_rejects_garbage(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    (repo / ".omc").mkdir()
    (repo / ".omc/project.id").write_text("nope\n")
    with pytest.raises(OmcError, match="project.id: not a UUID — fix or delete it"):
        project_id(_ctx(), repo)


def test_project_id_empty_file_is_created(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    (repo / ".omc").mkdir()
    (repo / ".omc/project.id").write_text("")
    uuid.UUID(project_id(_ctx(), repo))


def _git(repo, *args):
    subprocess.run(
        [host_bin("git"), *args],
        cwd=repo,
        env=_stagekit.hermetic_env(),
        check=True,
        capture_output=True,
    )


def test_project_id_missing_adopts_the_one_committed_at_head(tmp_path):
    """A fresh primary whose HEAD has the file (deleted, or not checked out) adopts it."""
    repo = _stagekit.git_repo(tmp_path / "repo")
    committed = str(uuid.uuid4())
    (repo / ".omc").mkdir()
    (repo / ".omc/project.id").write_text(committed + "\n")
    _stagekit.commit_all(repo, "project id")
    (repo / ".omc/project.id").unlink()
    assert project_id(_ctx(), repo) == committed
    assert (repo / ".omc/project.id").read_text() == committed + "\n"


def test_project_id_missing_adopts_the_one_on_origin_base(tmp_path):
    """Another clone committed the ID; this one has not synced yet: it takes that one."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    other = _stagekit.git_repo(tmp_path / "other")
    _git(other, "remote", "add", "origin", str(origin))
    _git(other, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(tmp_path / "repo"))
    repo = tmp_path / "repo"
    committed = str(uuid.uuid4())
    (other / ".omc").mkdir()
    (other / ".omc/project.id").write_text(committed + "\n")
    _stagekit.commit_all(other, "project id")
    _git(other, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    assert project_id(_ctx(), repo) == committed
    assert _stagekit.tracked_changes(repo) == ""  # HEAD itself was not touched


def test_worktree_id_copied_from_other_worktree_is_regenerated(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    worktree = _stagekit.add_worktree(repo, tmp_path / "wt", "feat")
    primary_value = worktree_id(repo)
    ensure_cache(worktree)
    shutil.copy(repo / ".cache/worktree.id", worktree / ".cache/worktree.id")
    assert worktree_id(worktree) != primary_value
    data = json.loads((worktree / ".cache/worktree.id").read_text())
    assert data["path"] == str(worktree.resolve())
    assert worktree_id(repo) == primary_value


def test_worktree_id_unparseable_is_regenerated(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    ensure_cache(repo)
    (repo / ".cache/worktree.id").write_text("{not json")
    value = worktree_id(repo)
    uuid.UUID(value)
    assert worktree_id(repo) == value


def test_primary_has_its_own_worktree_id(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    value = worktree_id(repo)
    uuid.UUID(value)
    assert worktree_id(repo) == value
    data = json.loads((repo / ".cache/worktree.id").read_text())
    assert data == {"id": value, "path": str(repo.resolve())}


def test_cache_gitignore_hides_everything(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    assert ensure_cache(repo) == repo / ".cache"
    assert (repo / ".cache/.gitignore").read_text() == "*\n"
    (repo / ".cache/x").write_text("x\n")
    status = subprocess.run(
        [host_bin("git"), "status", "--porcelain"],
        cwd=repo,
        env=_stagekit.hermetic_env(),
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_resolve_ids_outside_repo_refuses(tmp_path, monkeypatch):
    outside = tmp_path / "empty"
    outside.mkdir()
    monkeypatch.chdir(outside)
    with pytest.raises(Refusal, match="not inside a git repository"):
        resolve_ids(_ctx())


def _race(fn, arg, n=16):
    """`n` threads call fn(arg) at once; their results."""
    barrier = threading.Barrier(n)
    results = []

    def call():
        barrier.wait()
        results.append(fn(arg))

    threads = [threading.Thread(target=call) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_project_id_first_creation_race_agrees(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    for _ in range(30):
        (repo / ".omc/project.id").unlink(missing_ok=True)
        results = _race(lambda root: project_id(_ctx(), root), repo)
        on_disk = (repo / ".omc/project.id").read_text().strip()
        assert set(results) == {on_disk}


def test_worktree_id_first_creation_race_agrees(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    for _ in range(30):
        (repo / ".cache/worktree.id").unlink(missing_ok=True)
        results = _race(worktree_id, repo)
        on_disk = json.loads((repo / ".cache/worktree.id").read_text())["id"]
        assert set(results) == {on_disk}


def test_worktree_id_unreadable_file_propagates(tmp_path):
    repo = _stagekit.git_repo(tmp_path / "repo")
    path = ensure_cache(repo) / "worktree.id"
    path.mkdir()
    with pytest.raises(IsADirectoryError) as exc:
        worktree_id(repo)
    # The read failed, not a later replace (that error names the temp file first).
    assert exc.value.filename == str(path)
    assert exc.value.filename2 is None
