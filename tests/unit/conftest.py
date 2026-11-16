"""Unit-tier seam (spec 2026-11-16 §5): embedding is always on in the product,
but a unit test never installs GitNexus's embedding runtime unless it carries
@pytest.mark.real_embeddings. The marked tests and the real-tool E2E cover the
embedding paths; everything else pins index behavior keyword-only."""

import pytest

from . import _mutexproc, _stagekit


@pytest.fixture(autouse=True)
def _keyword_only_unless_real_embeddings(request, monkeypatch):
    if request.node.get_closest_marker("real_embeddings") is None:
        monkeypatch.setattr("omc.gitnexus.ensure_embeddings", lambda ctx, **kw: False)


@pytest.fixture
def nowatch(tmp_path, monkeypatch):
    """A stage-test repo as the cwd, with no watch; every run still live is killed after.

    The env carries the stage root and omc home too, for code that reads os.environ."""
    monkeypatch.setenv("OMC_STAGE_ROOT", str(tmp_path / "stages"))
    monkeypatch.setenv("OMC_HOME", str(tmp_path / "home"))
    s = _stagekit.Stage(tmp_path, _stagekit.git_repo(tmp_path / "repo"))
    monkeypatch.chdir(s.repo)
    try:
        yield s
    finally:
        s.cleanup()


@pytest.fixture
def stage(nowatch):
    """`nowatch` with a watch alive lock held for the repo."""
    nowatch.watch = _mutexproc.hold_alive_lock(nowatch.repo)
    return nowatch
