import fcntl
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from omc.errors import OmcError
from omc.workspace import (
    ledger_path,
    load_ledger,
    ordered_repositories,
    repository_key,
    save_ledger,
    update_ledger,
    workspace_id,
)


def _repository(key="host/org/master", role="master"):
    return {
        "key": key,
        "primary": f"/work/{key}",
        "worktree": f"/work/{key}.feature",
        "branch": "feature/same",
        "base": "main",
        "role": role,
    }


def _workspace(master="host/org/master", slug="same"):
    return {"master": master, "slug": slug, "repositories": [_repository(master)]}


def _ledger():
    return {
        "version": 1,
        "workspaces": {'["host/org/master","same"]': _workspace()},
    }


def test_absent_ledger_returns_empty_schema_without_creating_home(tmp_path):
    home = tmp_path / "absent"
    assert ledger_path(home) == home / "workspaces.json"
    assert load_ledger(home) == {"version": 1, "workspaces": {}}
    assert not home.exists()


def test_ledger_round_trip(tmp_path):
    home = tmp_path / "nested" / "home"
    data = _ledger()
    save_ledger(home, data)
    assert load_ledger(home) == data


def test_workspace_identity_preserves_master_and_slug_boundaries():
    first = workspace_id("host/owner/a", "same")
    assert first != workspace_id("host/owner/b", "same")
    assert json.loads(first) == ["host/owner/a", "same"]
    assert workspace_id("a/b", "c") != workspace_id("a", "b/c")


@pytest.mark.parametrize(
    "forge",
    [
        None,
        {},
        {"host": "github.com", "owner": "org"},
        {"host": "", "owner": "org", "name": "lib"},
        {"host": "github.com", "owner": 1, "name": "lib"},
    ],
)
@pytest.mark.parametrize("origin", ["/work/lib-origin", "ssh://git@host:2222/org/lib.git"])
def test_repository_identity_retains_full_origin_without_complete_forge(forge, origin):
    assert repository_key({"repo": {"forge": forge}}, origin) == origin
    assert repository_key({"repo": {}}, origin) == origin


def test_repository_identity_prefers_complete_forge():
    assert (
        repository_key(
            {"repo": {"forge": {"host": "github.com", "owner": "org", "name": "lib"}}},
            "ignored",
        )
        == "github.com/org/lib"
    )


def test_ordered_repositories_keeps_dependency_registration_order_and_master_last():
    master = _repository()
    second = _repository("host/org/z", "dependency")
    third = _repository("host/org/a", "dependency")
    workspace = {"repositories": [master, second, third]}
    assert ordered_repositories(workspace) == [second, third, master]
    assert workspace["repositories"] == [master, second, third]


def test_concurrent_updates_do_not_lose_workspaces(tmp_path):
    def add_workspaces(prefix):
        for i in range(25):
            master = f"host/{prefix}/repo{i}"
            update_ledger(
                tmp_path,
                lambda data, master=master: data["workspaces"].update(
                    {workspace_id(master, "same"): _workspace(master)}
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(add_workspaces, ["a", "b"]))
    assert len(load_ledger(tmp_path)["workspaces"]) == 50


def test_update_waits_for_sibling_lock_then_reads_fresh_data(tmp_path):
    started = Event()
    mutated = Event()

    def write():
        started.set()

        def mutate(data):
            mutated.set()
            assert len(data["workspaces"]) == 1
            data["workspaces"][workspace_id("host/org/new", "same")] = _workspace("host/org/new")

        return update_ledger(tmp_path, mutate)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with (tmp_path / "workspaces.json.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            future = pool.submit(write)
            try:
                assert started.wait(2)
                assert not mutated.wait(0.1)
                save_ledger(tmp_path, _ledger())
                # Readers must remain lock-free even while a writer owns the lock.
                assert load_ledger(tmp_path) == _ledger()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        result = future.result(timeout=2)
    assert len(result["workspaces"]) == 2
    assert load_ledger(tmp_path) == result


def test_save_uses_unique_temporary_files_and_atomic_replacement(tmp_path, monkeypatch):
    real_replace = os.replace
    paths = []
    data = _ledger()

    def replace(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.parent == destination.parent == tmp_path
        assert source != destination
        assert json.loads(source.read_text()) == data
        assert load_ledger(tmp_path) == {"version": 1, "workspaces": {}}
        paths.append(source)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    for _ in range(2):
        save_ledger(tmp_path, data)
        assert load_ledger(tmp_path) == data
        ledger_path(tmp_path).unlink()
    assert len(set(paths)) == 2
    assert list(tmp_path.iterdir()) == []


def test_failed_replacement_preserves_previous_data_and_removes_temp(tmp_path, monkeypatch):
    save_ledger(tmp_path, _ledger())

    def fail_replace(source, destination):
        raise OSError("replacement failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        save_ledger(tmp_path, {"version": 1, "workspaces": {}})
    assert load_ledger(tmp_path) == _ledger()
    assert list(tmp_path.iterdir()) == [ledger_path(tmp_path)]


def test_update_holds_exclusive_lock_through_atomic_save(tmp_path, monkeypatch):
    real_replace = os.replace

    def replace(source, destination):
        with (tmp_path / "workspaces.json.lock").open("w") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    assert update_ledger(tmp_path, lambda data: None) == {"version": 1, "workspaces": {}}


@pytest.mark.parametrize(
    "repositories",
    [
        [_repository(role="dependency")],
        [_repository(), _repository("host/org/other")],
        [_repository(), _repository(role="dependency")],
        [_repository("host/org/other")],
        [None],
        [{}],
    ],
)
def test_inconsistent_repository_membership_is_rejected(tmp_path, repositories):
    data = _ledger()
    next(iter(data["workspaces"].values()))["repositories"] = repositories
    ledger_path(tmp_path).write_text(json.dumps(data))
    with pytest.raises(OmcError, match="workspace ledger"):
        load_ledger(tmp_path)


def test_mismatched_workspace_identity_is_rejected(tmp_path):
    data = {"version": 1, "workspaces": {"wrong": _workspace()}}
    ledger_path(tmp_path).write_text(json.dumps(data))
    with pytest.raises(OmcError, match="workspace ledger"):
        load_ledger(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        "{broken",
        "[]",
        "{}",
        '{"version":2,"workspaces":{}}',
        '{"version":true,"workspaces":{}}',
        '{"version":1,"workspaces":[]}',
        '{"version":1,"workspaces":{"bad":null}}',
    ],
)
def test_corrupt_ledger_is_rejected_without_overwriting(tmp_path, raw):
    ledger_path(tmp_path).write_text(raw)
    with pytest.raises(OmcError, match="workspace ledger"):
        update_ledger(tmp_path, lambda data: data.clear())
    assert ledger_path(tmp_path).read_text() == raw


@pytest.mark.parametrize(
    "field,value",
    [
        ("master", ""),
        ("slug", None),
        ("repositories", {}),
        ("repositories", []),
    ],
)
def test_malformed_workspace_is_rejected(tmp_path, field, value):
    data = _ledger()
    next(iter(data["workspaces"].values()))[field] = value
    ledger_path(tmp_path).write_text(json.dumps(data))
    with pytest.raises(OmcError, match="workspace ledger"):
        load_ledger(tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("key", ""),
        ("primary", "relative"),
        ("worktree", None),
        ("branch", ""),
        ("base", 1),
        ("role", "unknown"),
    ],
)
def test_malformed_repository_is_rejected(tmp_path, field, value):
    data = _ledger()
    next(iter(data["workspaces"].values()))["repositories"][0][field] = value
    ledger_path(tmp_path).write_text(json.dumps(data))
    with pytest.raises(OmcError, match="workspace ledger"):
        load_ledger(tmp_path)


def test_invalid_mutation_preserves_previous_ledger_and_releases_lock(tmp_path):
    save_ledger(tmp_path, _ledger())
    with pytest.raises(OmcError, match="workspace ledger"):
        update_ledger(tmp_path, lambda data: data.clear())
    assert load_ledger(tmp_path) == _ledger()
    assert update_ledger(tmp_path, lambda data: None) == _ledger()


# These fixtures keep directory topology/config/ledger/record discovery real;
# only the subprocess boundary is replaced by an argv-strict recorder.
class WorkspaceTools:
    def __init__(self, tmp_path, monkeypatch):
        from omc.toolctx import ToolContext

        self.ctx = ToolContext(tmp_path / "home", {})
        self.projects = tmp_path / "projects"
        self.projects.mkdir()
        self.calls = []
        self.repos = {}
        self.list_overrides = {}
        self.remote_ok = True
        self.clone_ok = True
        self.switch_ok = True
        self.clone_aware = True
        self.fetch_codes = {}
        self.merge_codes = {}
        self.remove_codes = {}
        self.master = self.add_repo("master")
        self.current = self.add_tree(self.master, "feature/same")
        monkeypatch.chdir(self.current)
        monkeypatch.setattr(self.ctx, "run", self.run)

    def add_repo(self, name, *, origin=None, prefix="feature/", base="main", aware=True):
        path = self.projects / name
        path.mkdir(parents=True, exist_ok=True)
        (path / ".git").mkdir()
        if aware:
            (path / ".omc").mkdir()
            (path / ".omc/config.yaml").write_text(
                f"worktree:\n  branch_prefix: '{prefix}'\n  base_branch: '{base}'\n"
            )
        self.repos[str(path)] = {
            "primary": path,
            "branch": base,
            "origin": origin or f"https://github.com/org/{path.name}.git",
            "items": [],
        }
        return path

    def add_tree(self, primary, branch):
        path = primary.parent / (primary.name + "." + branch.replace("/", "-"))
        path.mkdir(exist_ok=True)
        (path / ".git").write_text("gitdir: stub\n")
        cfg = primary / ".omc/config.yaml"
        if cfg.exists():
            (path / ".omc").mkdir(exist_ok=True)
            (path / ".omc/config.yaml").write_text(cfg.read_text())
        repo = self.repos[str(primary)]
        self.repos[str(path)] = {**repo, "branch": branch}
        repo["items"].append(
            {
                "branch": branch,
                "worktree": {"path": str(path)},
                "default_branch": {"ahead": 99, "behind": 88},
            }
        )
        return path

    def run(self, argv, *, cwd=None, **kwargs):
        from subprocess import CompletedProcess

        argv = list(argv)
        directory = str(Path(cwd or Path.cwd()).resolve())
        self.calls.append((argv, directory))
        repo = self.repos.get(directory)

        def done(out="", rc=0):
            return CompletedProcess(argv, rc, out, "failure" if rc else "")

        if argv[:2] == ["git", "ls-remote"]:
            assert len(argv) == 3
            return done("abc\tHEAD\n", 0 if self.remote_ok else 1)
        if argv[:2] == ["git", "clone"]:
            assert argv[2] == "--"
            destination = Path(argv[4])
            if not self.clone_ok:
                destination.mkdir()
                (destination / "keep").write_text("partial clone")
                return done(rc=1)
            self.add_repo(str(destination), origin=argv[3], aware=self.clone_aware)
            return done()
        if argv[0] == "wt":
            assert argv[1] == "-C"
            primary = Path(argv[2])
            if argv[3:] == ["--config-set", "list.json-schema=2", "list", "--format=json"]:
                if str(primary) in self.list_overrides:
                    return done(self.list_overrides[str(primary)])
                return done(
                    json.dumps(
                        {"schema": 2, "repo": {}, "items": self.repos[str(primary)]["items"]}
                    )
                )
            if argv[3] == "remove":
                assert len(argv) == 5
                return done(rc=self.remove_codes.get(str(primary), 0))
            assert argv[3] == "switch"
            if not self.switch_ok:
                return done(rc=1)
            assert argv[4] == "--create"
            tree = self.add_tree(primary, argv[5])
            return done(json.dumps({"path": str(tree)}))
        if argv == ["git", "rev-parse", "--show-toplevel"]:
            return done(directory, 0) if repo else done(rc=128)
        if argv == ["git", "worktree", "list", "--porcelain"]:
            return done(f"worktree {repo['primary']}\n") if repo else done(rc=128)
        if argv == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return done(repo["branch"])
        if argv == ["git", "remote", "get-url", "origin"]:
            return done(repo["origin"])
        if argv[:3] == ["git", "fetch", "origin"]:
            return done(rc=self.fetch_codes.get(directory, 0))
        if argv[:3] == ["git", "merge-base", "--is-ancestor"]:
            assert len(argv) == 5
            return done(rc=self.merge_codes.get(directory, 0))
        if argv[:3] == ["git", "cat-file", "-e"]:
            return done()
        if argv[:3] == ["git", "status", "--porcelain"]:
            return done(repo.get("status", ""))
        raise AssertionError((argv, directory))


@pytest.fixture
def workspace_tools(tmp_path, monkeypatch):
    return WorkspaceTools(tmp_path, monkeypatch)


def test_inspection_pins_schema_two_when_worktrunk_defaults_to_schema_one(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    from omc.toolctx import ToolContext
    from omc.workspace import _inspect

    ctx = ToolContext(tmp_path / "home", {})
    schema_two = {"schema": 2, "repo": {}, "items": []}

    def run(argv, **kwargs):
        # Worktrunk 0.68.0 defaults to a top-level array; schema 2 is opt-in.
        pinned = [
            ctx.wt_bin,
            "-C",
            "/repo",
            "--config-set",
            "list.json-schema=2",
            "list",
            "--format=json",
        ]
        output = schema_two if argv == pinned else []
        return CompletedProcess(argv, 0, json.dumps(output), "")

    monkeypatch.setattr(ctx, "run", run)
    assert _inspect(ctx, "/repo") == schema_two


def _result(capsys):
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("OMC_WORKSPACE ")
    return json.loads(lines[0].removeprefix("OMC_WORKSPACE "))


def _add(tools, target, **kwargs):
    from omc import workspace

    return workspace.run_add(tools.ctx, str(target), **kwargs)


def _list(tools):
    from omc import workspace

    return workspace.run_list(tools.ctx)


def test_add_sibling_uses_own_config_and_is_idempotent(workspace_tools, capsys):
    t = workspace_tools
    dep = t.add_repo("lib", prefix="change/", base="develop")
    assert _add(t, "lib") == 0
    result = _result(capsys)
    assert result["repository"]["branch"] == "change/same"
    assert result["repository"]["base"] == "develop"
    assert result["repository"]["primary"] == str(dep.resolve())
    assert (
        [
            "wt",
            "-C",
            str(dep),
            "switch",
            "--create",
            "change/same",
            "--base",
            "origin/develop",
            "--no-cd",
            "--yes",
            "--format=json",
        ],
        str(t.current),
    ) in t.calls
    assert (["git", "fetch", "origin", "develop"], str(dep)) in t.calls
    assert not any(a[1] in ("ls-remote", "clone") for a, _ in t.calls if a[0] == "git")
    assert _add(t, "lib") == 0
    _result(capsys)
    assert len([a for a, _ in t.calls if "switch" in a]) == 1
    entries = next(iter(load_ledger(t.ctx.home)["workspaces"].values()))["repositories"]
    assert [r["role"] for r in entries] == ["master", "dependency"]


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("https://github.com/org/master.git", "https://github.com/org/lib.git"),
        ("git@gitlab.example:group/master.git", "git@gitlab.example:group/lib.git"),
        ("/srv/remotes/master.git", "/srv/remotes/lib.git"),
    ],
)
def test_add_derives_and_verifies_origin_before_clone(workspace_tools, capsys, origin, expected):
    t = workspace_tools
    t.repos[str(t.master)]["origin"] = origin
    t.add_repo("second")
    assert _add(t, "lib") == 0
    _result(capsys)
    calls = [a for a, _ in t.calls]
    assert ["git", "ls-remote", expected] in calls
    assert ["git", "clone", "--", expected, str(t.projects / "lib")] in calls
    assert calls.index(["git", "ls-remote", expected]) < calls.index(
        ["git", "clone", "--", expected, str(t.projects / "lib")]
    )


@pytest.mark.parametrize(
    "case,rc,reason",
    [
        ("unresolved", 3, "unresolved"),
        ("topology", 3, "not-a-projects-folder"),
        ("occupied", 2, "occupied"),
        ("unaware", 2, "not-omc-aware"),
        ("clone-failed", 1, "clone-failed"),
        ("switch-failed", 1, "worktree-failed"),
        ("self", 2, "master-self"),
        ("primary", 2, "primary-checkout"),
        ("detached", 2, "no-prefix"),
        ("no-prefix", 2, "no-prefix"),
    ],
)
def test_failed_first_add_never_creates_ledger(
    workspace_tools, monkeypatch, capsys, case, rc, reason
):
    t = workspace_tools
    target = "lib"
    if case in ("unresolved", "clone-failed"):
        t.add_repo("second")
    if case == "unresolved":
        t.remote_ok = False
    elif case == "clone-failed":
        t.clone_ok = False
    elif case == "topology":
        for i in range(3):
            t.add_tree(t.master, f"feature/extra-{i}")
    elif case == "occupied":
        (t.projects / "lib").mkdir()
        (t.projects / "lib/keep").write_text("keep")
    elif case == "unaware":
        t.add_repo("lib", aware=False)
    elif case == "switch-failed":
        t.add_repo("lib")
        t.switch_ok = False
    elif case == "self":
        target = "master"
    elif case == "primary":
        monkeypatch.chdir(t.master)
    elif case in ("detached", "no-prefix"):
        t.repos[str(t.current)]["branch"] = "HEAD" if case == "detached" else "topic"
    assert _add(t, target) == rc
    result = _result(capsys)
    assert result["reason"] == reason
    assert not ledger_path(t.ctx.home).exists()
    if case in ("occupied", "clone-failed"):
        assert (t.projects / "lib/keep").exists()
    if case == "unaware":
        assert result["checkout"] == str(t.projects / "lib")


def test_explicit_clone_destination_bypasses_projects_folder_gate(workspace_tools, capsys):
    t = workspace_tools
    dest = t.projects.parent / "chosen"
    assert _add(t, "lib", path=str(dest)) == 0
    assert _result(capsys)["repository"]["primary"] == str(dest)


def test_existing_path_and_symlink_register_canonical_paths(workspace_tools, capsys):
    t = workspace_tools
    dep = t.add_repo("elsewhere/lib")
    link = t.projects / "alias"
    link.symlink_to(dep, target_is_directory=True)
    assert _add(t, link) == 0
    assert _result(capsys)["repository"]["primary"] == str(dep)


def test_cross_workspace_membership_refuses_before_cutting(workspace_tools, capsys):
    t = workspace_tools
    dep = t.add_repo("lib")
    other = _workspace("another/master")
    entry = {
        "key": "https://github.com/org/lib.git",
        "primary": str(dep),
        "worktree": str(dep) + ".feature-same",
        "branch": "feature/same",
        "base": "main",
        "role": "dependency",
    }
    other["repositories"].append(entry)
    save_ledger(
        t.ctx.home, {"version": 1, "workspaces": {workspace_id(other["master"], "same"): other}}
    )
    before = ledger_path(t.ctx.home).read_bytes()
    assert _add(t, "lib") == 2
    assert _result(capsys)["reason"] == "membership-collision"
    assert ledger_path(t.ctx.home).read_bytes() == before
    assert not any("switch" in a for a, _ in t.calls)


def test_list_without_workspace_is_empty(workspace_tools, capsys):
    assert _list(workspace_tools) == 0
    assert _result(capsys) == {
        "ok": True,
        "slug": "same",
        "current_role": None,
        "master_worktree": None,
        "repositories": [],
    }


def test_list_dependency_keeps_upstream_unknown_and_orders_master_last(
    workspace_tools, monkeypatch, capsys
):
    t = workspace_tools
    t.add_repo("lib", origin="/srv/remotes/lib.git")
    assert _add(t, "lib") == 0
    entry = _result(capsys)["repository"]
    monkeypatch.chdir(entry["worktree"])
    assert _list(t) == 0
    result = _result(capsys)
    assert result["current_role"] == "dependency"
    assert result["master_worktree"] == str(t.current)
    assert [r["role"] for r in result["repositories"]] == ["dependency", "master"]
    assert result["repositories"][0]["upstream"] is None
    assert result["repositories"][0]["ahead"] is None
    assert result["repositories"][0]["behind"] is None
    assert result["repositories"][0]["compare_url"] is None
    assert result["repositories"][0]["record"]["reason"] == "missing"


@pytest.mark.parametrize(
    "provider,url,suffix",
    [
        ("github", "https://github.com/org/lib", "/compare/release%2Fnext...change%2Fsame"),
        ("gitlab", "https://gitlab.example/group/lib", "/-/compare/release%2Fnext...change%2Fsame"),
        (
            "bitbucket",
            "https://bitbucket.org/org/lib",
            "/branches/compare/change%2Fsame..release%2Fnext",
        ),
        ("other", "https://other.example/org/lib", None),
    ],
)
@pytest.mark.parametrize("dirty", [False, True])
def test_list_reports_record_upstream_and_forge_compare(
    workspace_tools, capsys, provider, url, suffix, dirty
):
    t = workspace_tools
    dep = t.add_repo("lib", prefix="change/", base="release/next")
    assert _add(t, "lib") == 0
    entry = _result(capsys)["repository"]
    tree = Path(entry["worktree"])
    record = tree / "docs/superpowers/specs/2026-10-08-same-design.md"
    record.parent.mkdir(parents=True)
    record.write_text("design")
    t.repos[str(tree)]["status"] = " M record" if dirty else ""
    upstream = {"remote": "origin", "branch": "change/same", "ahead": 2, "behind": 3}
    t.list_overrides[str(dep)] = json.dumps(
        {
            "repo": {"forge": {"provider": provider, "url": url}},
            "items": [{**t.repos[str(dep)]["items"][0], "upstream": upstream}],
        }
    )
    assert _list(t) == 0
    result = _result(capsys)["repositories"][0]
    assert result["record"]["ok"] is (not dirty)
    if dirty:
        assert result["record"]["reason"] == "unclean"
    assert result["upstream"] == upstream
    assert (result["ahead"], result["behind"]) == (2, 3)
    assert result["compare_url"] == (url + suffix if suffix else None)


def test_same_slug_unrelated_repo_does_not_join_workspace(workspace_tools, monkeypatch, capsys):
    t = workspace_tools
    t.add_repo("lib")
    assert _add(t, "lib") == 0
    _result(capsys)
    other = t.add_tree(t.add_repo("unrelated"), "feature/same")
    monkeypatch.chdir(other)
    assert _list(t) == 0
    assert _result(capsys)["repositories"] == []


@pytest.mark.parametrize(
    "bad", ["{broken", "[]", "{}", '{"repo":{},"items":[null]}', '{"repo":{},"items":[]}']
)
def test_list_bad_inspection_is_explicit_failure(workspace_tools, capsys, bad):
    t = workspace_tools
    dep = t.add_repo("lib")
    assert _add(t, "lib") == 0
    _result(capsys)
    t.list_overrides[str(dep)] = bad
    assert _list(t) == 1
    assert _result(capsys)["ok"] is False


def test_missing_registered_worktree_is_explicit_failure(workspace_tools, capsys):
    import shutil

    t = workspace_tools
    t.add_repo("lib")
    assert _add(t, "lib") == 0
    entry = _result(capsys)["repository"]
    shutil.rmtree(entry["worktree"])
    assert _list(t) == 1
    assert _result(capsys)["reason"] == "missing-worktree"


def test_named_sibling_wins_over_worktree_local_directory(workspace_tools, capsys):
    t = workspace_tools
    dep = t.add_repo("lib")
    (t.current / "lib").mkdir()
    (t.current / "lib/keep").write_text("local source directory")
    assert _add(t, "lib") == 0
    assert _result(capsys)["repository"]["primary"] == str(dep)
    assert (t.current / "lib/keep").read_text() == "local source directory"


def test_named_sibling_linked_worktree_refuses_without_registering_its_primary(
    workspace_tools, capsys
):
    t = workspace_tools
    other = t.add_repo("other")
    linked = t.add_tree(other, "feature/elsewhere")
    sibling = t.projects / "lib"
    linked.rename(sibling)
    t.repos[str(sibling)] = t.repos.pop(str(linked))
    before = list(t.calls)

    assert _add(t, "lib") == 2
    result = _result(capsys)
    assert result["reason"] == "occupied"
    assert result["checkout"] == str(sibling)
    assert not ledger_path(t.ctx.home).exists()
    assert not any("switch" in argv or "clone" in argv for argv, _ in t.calls[len(before) :])


def test_git_suffix_target_does_not_duplicate_origin_suffix(workspace_tools, capsys):
    t = workspace_tools
    t.add_repo("second")
    assert _add(t, "lib.git") == 0
    _result(capsys)
    assert (["git", "ls-remote", "https://github.com/org/lib.git"], str(t.current)) in t.calls


@pytest.mark.parametrize("bad", [{"provider": []}, {"provider": "github", "url": []}])
def test_malformed_forge_is_an_inspection_failure(workspace_tools, capsys, bad):
    t = workspace_tools
    dep = t.add_repo("lib")
    assert _add(t, "lib") == 0
    _result(capsys)
    t.list_overrides[str(dep)] = json.dumps(
        {"repo": {"forge": bad}, "items": t.repos[str(dep)]["items"]}
    )
    assert _list(t) == 1
    assert _result(capsys)["reason"] == "inspection-failed"


@pytest.mark.parametrize("count", [-1, "2", True])
def test_malformed_upstream_count_fails_instead_of_fabricating_unknown(
    workspace_tools, capsys, count
):
    t = workspace_tools
    dep = t.add_repo("lib")
    assert _add(t, "lib") == 0
    _result(capsys)
    t.repos[str(dep)]["items"][0]["upstream"] = {
        "remote": "origin",
        "branch": "feature/same",
        "ahead": count,
        "behind": 0,
    }
    assert _list(t) == 1
    assert _result(capsys)["reason"] == "inspection-failed"


def test_empty_target_is_usage_not_master_self(workspace_tools, capsys):
    assert _add(workspace_tools, "") == 2
    assert _result(capsys)["reason"] == "usage"


def test_registration_rechecks_collision_after_worktree_creation(
    workspace_tools, monkeypatch, capsys
):
    from omc import workspace

    t = workspace_tools
    dep = t.add_repo("lib")
    original_update = workspace.update_ledger

    def raced_update(home, mutate):
        other = _workspace("other/master")
        other["repositories"].append(
            {
                "key": "https://github.com/org/lib.git",
                "primary": str(dep),
                "worktree": str(dep) + ".feature-same",
                "branch": "feature/same",
                "base": "main",
                "role": "dependency",
            }
        )
        save_ledger(
            home, {"version": 1, "workspaces": {workspace_id(other["master"], "same"): other}}
        )
        return original_update(home, mutate)

    monkeypatch.setattr(workspace, "update_ledger", raced_update)
    assert _add(t, "lib") == 2
    assert _result(capsys)["reason"] == "membership-collision"
    assert list(load_ledger(t.ctx.home)["workspaces"]) == ['["other/master","same"]']


def test_fetch_and_worktree_cut_do_not_hold_ledger_lock(workspace_tools, monkeypatch, capsys):
    t = workspace_tools
    t.add_repo("lib")
    t.ctx.home.mkdir()
    real_run = t.ctx.run
    checked = []

    def run(argv, **kwargs):
        if "fetch" in argv or "switch" in argv:
            with (t.ctx.home / "workspaces.json.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                checked.append(argv)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(t.ctx, "run", run)
    assert _add(t, "lib") == 0
    _result(capsys)
    assert len(checked) == 2


def test_clone_without_omc_keeps_checkout_and_does_not_register(workspace_tools, capsys):
    t = workspace_tools
    t.clone_aware = False
    dest = t.projects.parent / "chosen"
    assert _add(t, "lib", path=str(dest)) == 2
    result = _result(capsys)
    assert result["reason"] == "not-omc-aware"
    assert result["checkout"] == str(dest)
    assert (dest / ".git").is_dir()
    assert not ledger_path(t.ctx.home).exists()


def test_current_identity_must_match_even_when_branch_and_path_match(workspace_tools, capsys):
    t = workspace_tools
    t.add_repo("lib")
    assert _add(t, "lib") == 0
    _result(capsys)
    t.repos[str(t.master)]["origin"] = "https://github.com/another/master.git"
    assert _list(t) == 0
    assert _result(capsys)["repositories"] == []


def test_corrupt_ledger_and_tool_failure_each_emit_one_error(workspace_tools, monkeypatch, capsys):
    t = workspace_tools
    t.ctx.home.mkdir()
    ledger_path(t.ctx.home).write_text("{broken")
    assert _list(t) == 1
    assert _result(capsys)["reason"] == "error"
    assert ledger_path(t.ctx.home).read_text() == "{broken"

    def fail(*args, **kwargs):
        raise OSError("tool unavailable")

    monkeypatch.setattr(t.ctx, "run", fail)
    assert _list(t) == 2
    assert _result(capsys)["reason"] == "not-a-repository"


def test_internal_workspace_success_dispatch_emits_one_verdict(
    workspace_tools, monkeypatch, capsys
):
    from omc.internal import run_internal
    from omc.toolctx import ToolContext

    t = workspace_tools
    monkeypatch.setattr(ToolContext, "from_env", lambda: t.ctx)
    t.add_repo("lib")
    assert run_internal(["workspace", "add", "lib"]) == 0
    assert _result(capsys)["repository"]["role"] == "dependency"
    assert run_internal(["workspace", "list"]) == 0
    assert [repo["role"] for repo in _result(capsys)["repositories"]] == ["dependency", "master"]


def _close_fixture(t, capsys):
    for name in ("dependency-a", "dependency-b"):
        t.add_repo(name, prefix="change/", base="develop")
        assert _add(t, name) == 0
        _result(capsys)
    t.calls.clear()
    return next(iter(load_ledger(t.ctx.home)["workspaces"].values()))


def _close(t):
    from omc.workspace import run_workspace

    return run_workspace(t.ctx, ["close"])


def _remaining_names(t):
    workspaces = load_ledger(t.ctx.home)["workspaces"]
    if not workspaces:
        return []
    return [
        Path(entry["primary"]).name
        for entry in ordered_repositories(next(iter(workspaces.values())))
    ]


def _removed_names(t):
    return [Path(a[2]).name for a, _ in t.calls if a[0] == "wt" and a[3] == "remove"]


def test_close_dependency_refuses(workspace_tools, capsys, monkeypatch):
    t = workspace_tools
    workspace = _close_fixture(t, capsys)
    monkeypatch.chdir(workspace["repositories"][1]["worktree"])
    assert _close(t) == 2
    result = _result(capsys)
    assert result["reason"] == "not-master"
    assert result["master_worktree"] == workspace["repositories"][0]["worktree"]
    assert _removed_names(t) == []
    assert not any(a[1] == "fetch" for a, _ in t.calls)
    assert _remaining_names(t) == ["dependency-a", "dependency-b", "master"]


def test_close_stops_at_unmerged(workspace_tools, capsys):
    t = workspace_tools
    _close_fixture(t, capsys)
    t.merge_codes[str(t.projects / "dependency-b")] = 1
    assert _close(t) == 2
    payload = _result(capsys)
    assert payload["reason"] == "unmerged"
    assert Path(payload["repository"]["primary"]).name == "dependency-b"
    assert _removed_names(t) == ["dependency-a"]
    assert _remaining_names(t) == ["dependency-b", "master"]
    assert not any(a[1] == "fetch" and cwd == str(t.master) for a, cwd in t.calls)


def test_close_resumes_after_partial_success(workspace_tools, capsys):
    t = workspace_tools
    _close_fixture(t, capsys)
    t.merge_codes[str(t.projects / "dependency-b")] = 1
    assert _close(t) == 2
    _result(capsys)
    t.merge_codes.clear()
    assert _close(t) == 0
    payload = _result(capsys)
    assert [Path(e["primary"]).name for e in payload["removed"]] == ["dependency-b", "master"]
    assert payload["repositories"] == []
    assert _removed_names(t) == ["dependency-a", "dependency-b", "master"]
    assert load_ledger(t.ctx.home)["workspaces"] == {}


@pytest.mark.parametrize("failure_index", [0, 1])
def test_close_fetch_failure_retains_entries(workspace_tools, capsys, failure_index):
    t = workspace_tools
    _close_fixture(t, capsys)
    names = ["dependency-a", "dependency-b", "master"]
    primary = str(t.projects / names[failure_index])
    t.fetch_codes[primary] = 128
    assert _close(t) == 1
    assert _result(capsys)["reason"] == "fetch-failed"
    assert _remaining_names(t) == names[failure_index:]
    assert _removed_names(t) == names[:failure_index]
    assert not any(a[1] == "merge-base" and cwd == primary for a, cwd in t.calls)


def test_close_remove_failure_retains_entries(workspace_tools, capsys):
    t = workspace_tools
    _close_fixture(t, capsys)
    t.remove_codes[str(t.projects / "dependency-b")] = 1
    assert _close(t) == 1
    assert _result(capsys)["reason"] == "remove-failed"
    assert _remaining_names(t) == ["dependency-b", "master"]
    assert _removed_names(t) == ["dependency-a", "dependency-b"]


def test_close_master_last_deletes_workspace(workspace_tools, capsys):
    t = workspace_tools
    workspace = _close_fixture(t, capsys)
    assert _close(t) == 0
    assert _result(capsys)["repositories"] == []
    assert _removed_names(t) == ["dependency-a", "dependency-b", "master"]
    assert (
        workspace_id(workspace["master"], workspace["slug"])
        not in load_ledger(t.ctx.home)["workspaces"]
    )
    for entry in ordered_repositories(workspace):
        primary, branch, base = entry["primary"], entry["branch"], entry["base"]
        fetch = (
            ["git", "fetch", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"],
            primary,
        )
        ancestry = (["git", "merge-base", "--is-ancestor", branch, f"origin/{base}"], primary)
        remove = (["wt", "-C", primary, "remove", branch], primary)
        assert t.calls.index(fetch) < t.calls.index(ancestry) < t.calls.index(remove)


def test_close_missing_branch_fails_closed(workspace_tools, capsys):
    t = workspace_tools
    _close_fixture(t, capsys)
    t.merge_codes[str(t.projects / "dependency-a")] = 128
    assert _close(t) == 1
    assert _result(capsys)["reason"] == "merge-check-failed"
    assert _removed_names(t) == []
    assert _remaining_names(t) == ["dependency-a", "dependency-b", "master"]


def test_close_missing_worktree_fails_closed(workspace_tools, capsys):
    t = workspace_tools
    workspace = _close_fixture(t, capsys)
    tree = Path(workspace["repositories"][1]["worktree"])
    tree.rename(tree.with_name("moved"))
    assert _close(t) == 1
    assert _result(capsys)["reason"] == "missing-worktree"
    assert _removed_names(t) == []
    assert _remaining_names(t) == ["dependency-a", "dependency-b", "master"]


def test_close_unregistered_worktree_fails_closed(workspace_tools, capsys):
    t = workspace_tools
    _close_fixture(t, capsys)
    t.list_overrides[str(t.projects / "dependency-a")] = '{"repo":{},"items":[]}'
    assert _close(t) == 1
    assert _result(capsys)["reason"] == "missing-worktree"
    assert _removed_names(t) == []


def test_close_preserves_unrelated_concurrent_workspace(workspace_tools, capsys, monkeypatch):
    t = workspace_tools
    _close_fixture(t, capsys)
    original_run = t.ctx.run

    def run(argv, **kwargs):
        if argv[:4] == ["wt", "-C", str(t.master), "remove"]:
            update_ledger(
                t.ctx.home, lambda data: data["workspaces"].update(_ledger()["workspaces"])
            )
        return original_run(argv, **kwargs)

    monkeypatch.setattr(t.ctx, "run", run)
    assert _close(t) == 0
    _result(capsys)
    assert load_ledger(t.ctx.home) == _ledger()


def test_close_rechecks_entry_identity_before_ledger_drop(workspace_tools, capsys, monkeypatch):
    t = workspace_tools
    workspace = _close_fixture(t, capsys)
    identity = workspace_id(workspace["master"], workspace["slug"])
    original_run = t.ctx.run

    def run(argv, **kwargs):
        if argv[:4] == ["wt", "-C", str(t.projects / "dependency-a"), "remove"]:

            def change(data):
                data["workspaces"][identity]["repositories"][1]["branch"] = "change/replacement"

            update_ledger(t.ctx.home, change)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(t.ctx, "run", run)
    assert _close(t) == 2
    assert _result(capsys)["reason"] == "workspace-changed"
    remaining = load_ledger(t.ctx.home)["workspaces"][identity]["repositories"]
    assert remaining[1]["branch"] == "change/replacement"
    assert _removed_names(t) == ["dependency-a"]
    assert _remaining_names(t) == ["dependency-a", "dependency-b", "master"]


def test_close_refreshes_checked_ref_with_restricted_fetch_mapping(tmp_path, monkeypatch, capsys):
    from subprocess import CompletedProcess

    from omc.toolctx import ToolContext
    from omc.workspace import run_close

    ctx = ToolContext(tmp_path / "home", dict(os.environ))
    primary, origin, tree = (tmp_path / name for name in ("primary", "origin.git", "feature"))
    primary.mkdir()

    def git(*args, cwd=primary):
        return ctx.run([ctx.git_bin, *args], cwd=cwd, check=True).stdout.strip()

    git("init", "--bare", str(origin))
    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (primary / ".omc").mkdir()
    (primary / ".omc/config.yaml").write_text(
        "worktree:\n  branch_prefix: feature/\n  base_branch: main\n"
    )
    git("add", ".omc/config.yaml")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(origin))
    git("worktree", "add", "-b", "feature/same", str(tree))
    git("commit", "--allow-empty", "-m", "feature", cwd=tree)
    feature = git("rev-parse", "feature/same")
    git("push", "origin", "feature/same:main")
    git("fetch", "origin")
    # Move the actual remote base back, without updating the local tracking ref.
    git("update-ref", "refs/heads/main", base, cwd=origin)
    git("config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
    assert git("rev-parse", "origin/main") == feature
    assert git("rev-parse", "main", cwd=origin) == base

    entry = {
        "key": str(origin),
        "primary": str(primary),
        "worktree": str(tree),
        "branch": "feature/same",
        "base": "main",
        "role": "master",
    }
    ledger = {
        "version": 1,
        "workspaces": {
            workspace_id(str(origin), "same"): {
                "master": str(origin),
                "slug": "same",
                "repositories": [entry],
            }
        },
    }
    save_ledger(ctx.home, ledger)
    original_run = ctx.run
    removals = []

    def run(argv, **kwargs):
        if argv[0] != ctx.wt_bin:
            return original_run(argv, **kwargs)
        if argv == [
            ctx.wt_bin,
            "-C",
            str(primary),
            "--config-set",
            "list.json-schema=2",
            "list",
            "--format=json",
        ]:
            data = {
                "repo": {},
                "items": [{"branch": "feature/same", "worktree": {"path": str(tree)}}],
            }
            return CompletedProcess(argv, 0, json.dumps(data), "")
        assert argv == [ctx.wt_bin, "-C", str(primary), "remove", "feature/same"]
        removals.append(argv)
        return CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ctx, "run", run)
    monkeypatch.chdir(tree)
    assert run_close(ctx) == 2
    assert _result(capsys)["reason"] == "unmerged"
    assert git("rev-parse", "origin/main") == base
    assert load_ledger(ctx.home) == ledger
    assert removals == []


@pytest.mark.parametrize(
    "existing",
    [
        "https://github.com/other/lib.git",
        "https://elsewhere.example/org/lib.git",
        "https://github.com:8443/org/lib.git",
        "ssh://other@github.com/org/lib.git",
    ],
)
@pytest.mark.parametrize("explicit_path", [False, True])
def test_explicit_origin_mismatch_refuses_before_cut_or_ledger(
    workspace_tools, capsys, existing, explicit_path
):
    t = workspace_tools
    dep = t.add_repo("lib", origin=existing)
    save_ledger(t.ctx.home, _ledger())
    before = ledger_path(t.ctx.home).read_bytes()
    kwargs = {"path": str(dep)} if explicit_path else {}
    assert _add(t, "https://github.com/org/lib.git", **kwargs) == 2
    payload = _result(capsys)
    assert payload["reason"] == "origin-mismatch"
    assert payload["checkout"] == str(dep)
    assert ledger_path(t.ctx.home).read_bytes() == before
    assert not any("switch" in argv or "clone" in argv for argv, _ in t.calls)


@pytest.mark.parametrize(
    "requested,existing",
    [
        ("https://github.com/org/lib.git", "https://github.com/org/lib.git"),
        ("https://GITHUB.COM/org/lib.git", "https://github.com/org/lib/"),
        ("ssh://git@host:2222/org/lib.git", "ssh://git@host:2222/org/lib.git"),
        ("git@host:org/lib.git", "git@host:org/lib.git"),
    ],
)
def test_matching_explicit_origin_reuses_checkout(workspace_tools, capsys, requested, existing):
    t = workspace_tools
    dep = t.add_repo("lib", origin=existing)
    assert _add(t, requested) == 0
    assert _result(capsys)["repository"]["primary"] == str(dep)
    assert not any(argv[1] in ("ls-remote", "clone") for argv, _ in t.calls)


def _credential_url(name="lib"):
    # Synthetic values only; assertion messages never include their contents.
    secret = "synthetic-workspace-credential"
    return secret, f"https://user:{secret}@host:8443/org/{name}.git"


def _assert_no_credential(captured, secret):
    assert secret not in captured.out + captured.err, "credential escaped output boundary"


@pytest.mark.parametrize("derived", [False, True])
@pytest.mark.parametrize("failure", ["remote", "clone", "inspection", "fetch", "switch"])
def test_workspace_errors_redact_credentials_without_changing_transport(
    workspace_tools, monkeypatch, capsys, derived, failure
):
    from subprocess import CompletedProcess

    t = workspace_tools
    secret, url = _credential_url()
    if derived:
        t.repos[str(t.master)]["origin"] = _credential_url("master")[1]
    target = "lib" if derived else url
    dest = t.projects / "lib"
    original = t.ctx.run

    def run(argv, **kwargs):
        hit = (
            (failure == "remote" and argv[:2] == ["git", "ls-remote"])
            or (failure == "clone" and argv[:2] == ["git", "clone"])
            or (failure == "inspection" and argv[:3] == ["wt", "-C", str(dest)] and "list" in argv)
            or (failure == "fetch" and argv[:2] == ["git", "fetch"])
            or (failure == "switch" and "switch" in argv)
        )
        if hit:
            t.calls.append((list(argv), str(kwargs.get("cwd", t.current))))
            return CompletedProcess(argv, 1, "", f"failed to access '{url}'")
        return original(argv, **kwargs)

    monkeypatch.setattr(t.ctx, "run", run)
    code = _add(t, target, path=str(dest))
    assert code == {"remote": 3, "clone": 1, "inspection": 1, "fetch": 0, "switch": 1}[failure]
    _assert_no_credential(capsys.readouterr(), secret)
    calls = [argv for argv, _ in t.calls]
    assert ["git", "ls-remote", url] in calls
    if failure != "remote":
        assert ["git", "clone", "--", url, str(dest)] in calls


def test_fallback_identity_excludes_credentials_and_survives_rotation(workspace_tools, capsys):
    t = workspace_tools
    secret, url = _credential_url()
    dep = t.add_repo("lib", origin=url)
    assert _add(t, url) == 0
    _assert_no_credential(capsys.readouterr(), secret)
    raw = ledger_path(t.ctx.home).read_text()
    assert secret not in raw, "credential persisted in ledger"
    assert "https://host:8443/org/lib.git" in raw
    t.repos[str(dep)]["origin"] = "https://replacement@host:8443/org/lib.git"
    assert _add(t, "lib") == 0
    assert _result(capsys)["repository"]["key"] == "https://host:8443/org/lib.git"
    assert len([argv for argv, _ in t.calls if "switch" in argv]) == 1
    assert _list(t) == 0
    _assert_no_credential(capsys.readouterr(), secret)


def test_list_redacts_nested_upstream_and_compare_urls(workspace_tools, capsys):
    t = workspace_tools
    dep = t.add_repo("lib")
    assert _add(t, "lib") == 0
    _result(capsys)
    secret, url = _credential_url()
    items = t.repos[str(dep)]["items"]
    items[0]["upstream"] = {"remote": {"urls": [url]}, "ahead": 0, "behind": 0}
    t.list_overrides[str(dep)] = json.dumps(
        {
            "repo": {"forge": {"provider": "github", "url": url}},
            "items": items,
        }
    )
    assert _list(t) == 0
    output = capsys.readouterr()
    _assert_no_credential(output, secret)
    payload = json.loads(output.out.removeprefix("OMC_WORKSPACE "))
    assert payload["repositories"][0]["compare_url"] == (
        "https://host:8443/org/lib/compare/main...feature%2Fsame"
    )


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("https://user:synthetic@host:8443/org/lib.git", "https://host:8443/org/lib.git"),
        ("ssh://builder:synthetic@host:2222/org/lib.git", "ssh://builder@host:2222/org/lib.git"),
        ("builder@host:org/lib.git", "builder@host:org/lib.git"),
        ("/srv/local@host/lib.git", "/srv/local@host/lib.git"),
    ],
)
def test_fallback_identity_preserves_location_and_ssh_username(origin, expected):
    assert repository_key({"repo": {}}, origin) == expected


@pytest.mark.parametrize(
    "requested,existing",
    [
        ("ssh://git@host/org/lib.git", "ssh://builder@host/org/lib.git"),
        ("ssh://git@host:2222/org/lib.git", "ssh://git@host:2223/org/lib.git"),
        ("file:///srv/lib.git", "file:///srv/lib"),
    ],
)
def test_explicit_origin_preserves_ssh_account_port_and_local_path(
    workspace_tools, capsys, requested, existing
):
    t = workspace_tools
    dep = t.add_repo("lib", origin=existing)
    assert _add(t, requested, path=str(dep)) == 2
    assert _result(capsys)["reason"] == "origin-mismatch"
    assert not ledger_path(t.ctx.home).exists()
    assert not any("switch" in argv for argv, _ in t.calls)


def test_invalid_workspace_arguments_do_not_print_credentials(workspace_tools, capsys):
    from omc.workspace import run_workspace

    secret, url = _credential_url()
    assert run_workspace(workspace_tools.ctx, ["add", "lib", "--unknown", url]) == 2
    _assert_no_credential(capsys.readouterr(), secret)


@pytest.mark.parametrize("raises", [False, True])
def test_close_redacts_subprocess_diagnostics_and_nested_repository(
    workspace_tools, monkeypatch, capsys, raises
):
    from subprocess import CompletedProcess

    t = workspace_tools
    dep = t.add_repo("lib")
    assert _add(t, "lib") == 0
    _result(capsys)
    secret, url = _credential_url()

    # Simulate an older entry with extra nested metadata: output still redacts it.
    def add_metadata(data):
        entry = next(iter(data["workspaces"].values()))["repositories"][1]
        entry["metadata"] = {"url": url}

    update_ledger(t.ctx.home, add_metadata)
    original = t.ctx.run

    def run(argv, **kwargs):
        if argv[:2] == ["git", "fetch"] and kwargs.get("cwd") == str(dep):
            if raises:
                raise OSError(f"cannot fetch {url}")
            return CompletedProcess(argv, 128, "", f"cannot fetch {url}")
        return original(argv, **kwargs)

    monkeypatch.setattr(t.ctx, "run", run)
    assert _close(t) == 1
    output = capsys.readouterr()
    _assert_no_credential(output, secret)
    assert json.loads(output.out.removeprefix("OMC_WORKSPACE "))["reason"] == "fetch-failed"
