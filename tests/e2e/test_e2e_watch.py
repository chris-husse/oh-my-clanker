"""Live `omc watch --once`: real git sync + real GitNexus reindex, no tokens."""

from __future__ import annotations

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


def test_watch_once_up_to_date_still_refreshes_index(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "up to date" in out, out
    # --once is the refresh-now button: the REAL index is built even with no new commits
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, f"--once did not force an index refresh:\n{out[:1500]}"
