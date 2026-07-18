"""Live `omc watch --once`: real git sync + real GitNexus reindex, no tokens."""

from __future__ import annotations

import re

import pytest

from .harness import configure_omc, make_work_repo, run_in

pytestmark = pytest.mark.e2e


def _push_remote_commit(container, repo):
    script = (
        f"git clone -q {repo}-origin /work/other && cd /work/other && "
        "echo teammate > t.txt && git add -A && git commit -qm 'remote change' && "
        "git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_watch_once_syncs_and_reindexes_for_real(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _push_remote_commit(container, repo)

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "synced main" in out, out
    rc, _ = run_in(container, ["test", "-f", f"{repo}/t.txt"])
    assert rc == 0, "remote commit did not arrive via ff-sync"
    # the REAL gitnexus analyze ran and produced an index
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, f"watch did not build a real index:\n{out[:1500]}"
    # the wt starter got seeded (create-if-absent)
    rc, _ = run_in(container, ["test", "-f", f"{repo}/.config/wt.toml"])
    assert rc == 0, "ensure_wt_config did not seed the starter"


def test_configure_in_repo_builds_agents_chain(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "configure", "--set", "llm.default=claude"], cwd=repo)
    assert rc == 0, out
    rc, _ = run_in(container, ["test", "-L", f"{repo}/AGENTS.md"])
    assert rc == 0, "AGENTS.md is not a symlink"
    rc, _ = run_in(container, ["test", "-L", f"{repo}/CLAUDE.md"])
    assert rc == 0, "CLAUDE.md is not a symlink"
    rc, resolved = run_in(
        container, ["bash", "-c", f"cat {repo}/AGENTS.md && cat {repo}/CLAUDE.md"]
    )
    assert rc == 0 and resolved.count("omc behavior layer") == 2, resolved[:400]
    rc, _ = run_in(container, ["test", "-f", f"{repo}/.omc/config/AGENTS.md"])
    assert rc == 0, "project layer not seeded"


def test_watch_once_up_to_date_still_refreshes_index(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "up to date" in out, out
    # --once is the refresh-now button: the REAL index is built even with no new commits
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, f"--once did not force an index refresh:\n{out[:1500]}"


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


def test_watch_auto_build_runs_stage_via_shim_provider(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    # seed a project build stage + a claude shim that answers with a passing verdict.
    # NOTE: a printf-based one-liner with escaped embedded quotes (as originally
    # sketched) loses its backslashes when the container's shell writes the file,
    # producing an invalid (unquoted-key) OMC_STAGE JSON line. A quoted heredoc
    # sidesteps that escaping entirely, matching _seed_container_hook's style above.
    seed = (
        f"mkdir -p {repo}/.omc/skills/build /shim && "
        f"printf '# build\\nrun true\\n' > {repo}/.omc/skills/build/SKILL.md && "
        "cat > /shim/claude <<'EOF'\n"
        "#!/bin/sh\n"
        "echo 'OMC_STAGE "
        '{"stage": "build", "configured": true, "passed": true, "summary": "ok"}'
        "'\n"
        "EOF\n"
        "chmod +x /shim/claude"
    )
    rc, out = run_in(container, ["bash", "-c", seed])
    assert rc == 0, out
    shim_path = "PATH=/shim:/usr/local/bin:/usr/bin:/bin"
    argv = ["env", shim_path, "omc", "watch", "--once", "--auto-build"]
    rc, out = run_in(container, argv, cwd=repo, timeout=300)
    assert rc == 0, out
    assert "running project build stage via claude" in out, out
    assert "auto-build passed" in out, out
