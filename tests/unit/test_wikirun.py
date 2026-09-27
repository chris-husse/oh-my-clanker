"""wikirun: the wiki-run supervision plumbing shared by dependency docs and the
project wiki (spec 2026-09-27-stale-knowledge-snapshot-self-heal §2)."""

import json


def test_tracker_lives_in_wikirun_and_is_reexported_by_dependency():
    import omc.dependency as dep
    import omc.wikirun as wr

    assert dep.PageCountTracker is wr.PageCountTracker
    assert dep._WIKI_STALL_SECONDS is wr._WIKI_STALL_SECONDS
    assert dep._WIKI_POLL_SECONDS is wr._WIKI_POLL_SECONDS
    assert wr._WIKI_STALL_SECONDS == 300.0 and wr._WIKI_POLL_SECONDS == 1.0


def test_tracker_counts_modules_pages_and_overview(tmp_path):
    from omc.wikirun import PageCountTracker

    (tmp_path / "first_module_tree.json").write_text(
        json.dumps([{"slug": "a", "children": [{"slug": "b"}]}, {"slug": "c"}])
    )
    (tmp_path / "a.md").write_text("x")
    t = PageCountTracker(tmp_path)
    assert t.beat() == (4, 1)  # 3 modules + overview; one page down
    assert t.percent == 25


def test_dependency_monkeypatch_of_constants_still_reaches_run_document(monkeypatch):
    """test_dependency.py patches `omc.dependency._WIKI_*`; run_document must keep
    reading those names from ITS OWN module namespace, or the patches go dead."""
    import inspect

    import omc.dependency as dep

    src = inspect.getsource(dep.run_document)
    assert "stall_after=_WIKI_STALL_SECONDS" in src
    assert "poll=_WIKI_POLL_SECONDS" in src
    monkeypatch.setattr(dep, "_WIKI_STALL_SECONDS", 0.5)
    assert dep._WIKI_STALL_SECONDS == 0.5  # rebinding the re-export is what tests do
