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


def test_gitnexus_progress_feeds_prefixed_json_lines_only():
    from omc.wikirun import PROGRESS_PREFIX, GitNexusProgress

    gn = GitNexusProgress()
    assert gn.percent is None and gn.phase == "" and gn.detail == ""
    gn.feed('{"level":30,"msg":"pino record on the same stream"}')
    gn.feed('GITNEXUS_PROGRES {"percent": 5}')  # wrong prefix
    gn.feed(PROGRESS_PREFIX + "{not json")
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping"}')  # no percent
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":101}')
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":-1}')
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":true}')  # bool is not a percent
    gn.feed(PROGRESS_PREFIX + '{"phase":"grouping","percent":17.5}')
    gn.feed(PROGRESS_PREFIX + "[17]")
    assert gn.percent is None  # nothing above was accepted, nothing raised
    gn.feed(
        PROGRESS_PREFIX
        + '{"phase":"grouping","percent":17,"detail":"Grouping batch 2/23 (LLM)..."}'
    )
    assert (gn.percent, gn.phase, gn.detail) == (17, "grouping", "Grouping batch 2/23 (LLM)...")
    gn.feed(PROGRESS_PREFIX + '{"phase":"done","percent":100}')
    assert (gn.percent, gn.phase, gn.detail) == (100, "done", "")


def test_gitnexus_progress_renders_through_the_shared_bar():
    from omc.cli.progress_bar import render_bar
    from omc.wikirun import PROGRESS_PREFIX, GitNexusProgress

    now = [1000.0]
    gn = GitNexusProgress(clock=lambda: now[0])
    assert gn.render() == render_bar(None, 0.0, spin=0)  # indeterminate, first spin slot
    assert gn.render() == render_bar(None, 0.0, spin=1)  # bounce advances per redraw
    gn.feed(PROGRESS_PREFIX + '{"phase":"modules","percent":40,"detail":"auth"}')
    now[0] = 1012.0
    assert gn.render() == render_bar(40, 12.0, spin=2)


def test_gitnexus_progress_bar_falls_back_to_the_page_count_until_gitnexus_speaks(tmp_path):
    from omc.cli.progress_bar import render_bar
    from omc.wikirun import PROGRESS_PREFIX, GitNexusProgress, PageCountTracker

    (tmp_path / "first_module_tree.json").write_text(
        json.dumps([{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    )
    (tmp_path / "a.md").write_text("x")  # 1/4 pages
    now = [0.0]
    gn = GitNexusProgress(clock=lambda: now[0], fallback=PageCountTracker(tmp_path))
    gn.refresh()
    assert gn.percent is None  # the raw GitNexus percent stays None …
    assert gn.render() == render_bar(25, 0.0, spin=0)  # … but the bar shows the disk count
    (tmp_path / "b.md").write_text("x")
    gn.refresh()
    assert gn.render() == render_bar(50, 0.0, spin=0)
    gn.feed(PROGRESS_PREFIX + '{"phase":"modules","percent":40,"detail":"c"}')
    assert gn.render() == render_bar(40, 0.0, spin=0)  # GitNexus wins once it speaks
