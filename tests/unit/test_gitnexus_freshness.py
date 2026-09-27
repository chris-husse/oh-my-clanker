"""snapshot_freshness — one test per reason code (spec §1)."""

import json
import os
import subprocess

from omc.gitnexus import Freshness, snapshot_freshness
from omc.toolctx import ToolContext


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo_with_origin(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("one\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c1", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)
    _git("push", "-q", "-u", "origin", "main", cwd=repo)
    return origin, repo


def _commit(repo, name):
    (repo / name).write_text(name)
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", name, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _seed_index(repo, *, last=None, branch="main", repo_path=None, dirty=False, name="meta.json"):
    d = repo / ".gitnexus"
    d.mkdir(exist_ok=True)
    meta = {
        "branch": branch,
        "lastCommit": last if last is not None else _git("rev-parse", "HEAD", cwd=repo),
        "repoPath": repo_path if repo_path is not None else str(repo),
    }
    if dirty:
        meta["incrementalInProgress"] = {"startedAt": 1, "toWriteCount": 3}
    (d / name).write_text(json.dumps(meta))


def _seed_wiki(repo, from_commit):
    w = repo / ".gitnexus" / "wiki"
    w.mkdir(parents=True, exist_ok=True)
    (w / "meta.json").write_text(json.dumps({"fromCommit": from_commit, "moduleFiles": {}}))


def _ctx():
    return ToolContext.from_env({"HOME": os.environ["HOME"], "PATH": os.environ["PATH"]})


def _codes(v: Freshness):
    return [r.code for r in v.reasons]


def test_fresh_index_and_wiki(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    _seed_wiki(repo, _git("rev-parse", "HEAD", cwd=repo))
    v = snapshot_freshness(_ctx(), repo, "main")
    assert v.fresh and v.reasons == () and v.fix == "" and v.run_in == str(repo.resolve())
    assert v.basis == "origin/main"
    assert v.to_json() == {
        "fresh": True,
        "basis": "origin/main",
        "reasons": [],
        "fix": "",
        "run_in": str(repo.resolve()),
    }


def test_index_missing(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    v = snapshot_freshness(_ctx(), repo, "main")
    assert _codes(v) == ["index-missing"] and not v.fresh
    assert v.fix == "omc watch --once"  # no wiki reason when the index itself is missing


def test_store_inverted_skips_distance_codes(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, branch="feature/x", last="0" * 40)  # unknown commit, but inverted wins
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["store-inverted"]
    assert v.reasons[0].detail == {"owner": "feature/x"}


def test_inversion_judged_from_gitnexus_json_when_present(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, name="gitnexus.json", branch="feature/x")
    _seed_index(repo, name="meta.json", branch="main")  # legacy mirror says main: ignored
    assert _codes(snapshot_freshness(_ctx(), repo, "main", documentation=False)) == [
        "store-inverted"
    ]


def test_index_foreign_and_realpath_alias(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, repo_path="/Users/someone/Projects/app")
    assert "index-foreign" in _codes(snapshot_freshness(_ctx(), repo, "main"))
    link = tmp_path / "alias"
    os.symlink(repo, link)
    _seed_index(repo, repo_path=str(link))
    assert "index-foreign" not in _codes(snapshot_freshness(_ctx(), repo, "main"))


def test_missing_repo_path_is_not_foreign(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus").mkdir()
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"branch": "main", "lastCommit": _git("rev-parse", "HEAD", cwd=repo)})
    )
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert "index-foreign" not in _codes(v)


def test_index_dirty(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, dirty=True)
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-dirty"] and v.reasons[0].detail == {"startedAt": 1}


def test_index_unknown(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, last="a" * 40)
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-unknown"]


def test_index_missing_last_commit_is_unknown(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    d = repo / ".gitnexus"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"branch": "main", "repoPath": str(repo)}))
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-unknown"] and not v.fresh

    (d / "meta.json").write_text(
        json.dumps({"branch": "main", "repoPath": str(repo), "lastCommit": None})
    )
    v2 = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v2) == ["index-unknown"] and not v2.fresh


def test_index_behind_counts_commits(tmp_path):
    origin, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)  # at c1
    _commit(repo, "c2")
    _commit(repo, "c3")
    _git("push", "-q", "origin", "main", cwd=repo)
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-behind"] and v.reasons[0].detail == {"count": 2}
    assert "2 commits behind origin/main" in v.reasons[0].text


def test_index_diverged(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _git("switch", "-qc", "feature/side", cwd=repo)
    side = _commit(repo, "side")
    _git("switch", "-q", "main", cwd=repo)
    _seed_index(repo, last=side)  # known object, not an ancestor of origin/main
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert _codes(v) == ["index-diverged"]


def test_ref_head_instead_of_origin(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    _commit(repo, "local-only")  # HEAD ahead of origin/main, not pushed
    against_origin = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    against_head = snapshot_freshness(_ctx(), repo, "main", ref="HEAD", documentation=False)
    assert against_origin.fresh  # index == origin/main
    assert _codes(against_head) == ["index-behind"] and against_head.basis == "HEAD"


def test_unresolved_ref_skips_distances(tmp_path):
    repo = tmp_path / "solo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("x")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c", cwd=repo)
    _seed_index(repo)  # no origin at all
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert v.basis == "unresolved" and v.fresh


def test_unresolved_ref_still_reports_index_unknown(tmp_path):
    repo = tmp_path / "solo2"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("x")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "c", cwd=repo)
    _seed_index(repo, last="a" * 40)  # no origin at all; garbage lastCommit
    v = snapshot_freshness(_ctx(), repo, "main", documentation=False)
    assert v.basis == "unresolved"
    assert _codes(v) == ["index-unknown"]


def test_gitnexus_json_preferred_and_meta_json_ignored(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, name="gitnexus.json")
    _seed_index(repo, name="meta.json", dirty=True)  # would be index-dirty if read
    assert snapshot_freshness(_ctx(), repo, "main", documentation=False).fresh


def test_unreadable_meta_is_missing(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    (repo / ".gitnexus").mkdir()
    (repo / ".gitnexus" / "meta.json").write_text("{not json")
    assert _codes(snapshot_freshness(_ctx(), repo, "main")) == ["index-missing"]


def test_wiki_missing_and_fix_string(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    v = snapshot_freshness(_ctx(), repo, "main")
    assert _codes(v) == ["wiki-missing"]
    assert v.fix == "omc watch --once --enable-documentation"


def test_wiki_unknown(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    _seed_wiki(repo, "b" * 40)
    assert _codes(snapshot_freshness(_ctx(), repo, "main")) == ["wiki-unknown"]


def test_wiki_behind_compares_against_index_not_ref(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    c1 = _git("rev-parse", "HEAD", cwd=repo)
    _seed_wiki(repo, c1)
    _commit(repo, "c2")
    c3 = _commit(repo, "c3")
    _seed_index(repo, last=c3)  # index moved past the docs by 2
    v = snapshot_freshness(_ctx(), repo, "main", ref="HEAD")
    assert _codes(v) == ["wiki-behind"] and v.reasons[0].detail["count"] == 2


def test_wiki_ahead_of_index_is_not_behind(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    c1 = _git("rev-parse", "HEAD", cwd=repo)
    c2 = _commit(repo, "c2")
    _seed_index(repo, last=c1)
    _seed_wiki(repo, c2)  # docs generated AFTER the index commit
    v = snapshot_freshness(_ctx(), repo, "main", ref="HEAD")
    assert _codes(v) == ["index-behind"]  # only the index is stale


def test_documentation_false_skips_wiki_checks(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo)
    assert snapshot_freshness(_ctx(), repo, "main", documentation=False).fresh


def test_all_applicable_reasons_reported(tmp_path):
    _, repo = _repo_with_origin(tmp_path)
    _seed_index(repo, dirty=True, repo_path="/elsewhere")
    _commit(repo, "c2")
    _git("push", "-q", "origin", "main", cwd=repo)
    assert _codes(snapshot_freshness(_ctx(), repo, "main", documentation=False)) == [
        "index-foreign",
        "index-dirty",
        "index-behind",
    ]
