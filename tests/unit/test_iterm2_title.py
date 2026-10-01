import asyncio


def test_worker_uses_calling_session_and_literal_title():
    from omc.iterm2_title import set_title

    events = []
    caller = object()
    other = object()

    class Tab:
        async def async_set_variable(self, name, value):
            events.append(("variable", name, value))

        async def async_set_title(self, expression):
            events.append(("title", expression))

    class App:
        def get_session_by_id(self, identity):
            events.append(("lookup", identity))
            return caller

        def get_window_and_tab_for_session(self, session):
            events.append(("tab lookup", session))
            return other, Tab()

    class SDK:
        async def async_get_app(self, connection):
            return App()

    asyncio.run(set_title(object(), "caller-id", "$(danger) \\()", SDK()))
    assert events == [
        ("lookup", "caller-id"),
        ("tab lookup", caller),
        ("variable", "user.omc_title", "$(danger) \\()"),
        ("title", r"\(user.omc_title)"),
    ]


def test_worker_rejects_missing_session_and_tab():
    from omc.iterm2_title import TargetNotFound, set_title

    class MissingSession:
        def get_session_by_id(self, identity):
            return None

    class MissingTab:
        def get_session_by_id(self, identity):
            return object()

        def get_window_and_tab_for_session(self, session):
            return object(), None

    class SDK:
        def __init__(self, app):
            self.app = app

        async def async_get_app(self, connection):
            return self.app

    for app in (MissingSession(), MissingTab()):
        try:
            asyncio.run(set_title(object(), "caller-id", "x", SDK(app)))
        except TargetNotFound:
            pass
        else:
            raise AssertionError("missing target accepted")


def test_release_resets_title_to_empty_before_clearing_variable():
    from omc.iterm2_title import release_title

    events = []
    caller = object()

    class Tab:
        async def async_set_variable(self, name, value):
            events.append(("variable", name, value))

        async def async_set_title(self, expression):
            events.append(("title", expression))

    class App:
        def get_session_by_id(self, identity):
            events.append(("lookup", identity))
            return caller

        def get_window_and_tab_for_session(self, session):
            return object(), Tab()

    class SDK:
        async def async_get_app(self, connection):
            return App()

    asyncio.run(release_title(object(), "caller-id", SDK()))
    # "" restores the live default title (SDK docs); the variable is cleared AFTER so a
    # concurrent evaluation of \(user.omc_title) never shows an empty pin.
    assert events == [
        ("lookup", "caller-id"),
        ("title", ""),
        ("variable", "user.omc_title", None),
    ]


def test_worker_argv_forms():
    import sys

    from omc.iterm2_title import worker_argv

    base = [sys.executable, "-m", "omc.iterm2_title", "--session-id", "abc"]
    assert worker_argv("abc", title="feature/x") == [*base, "--", "feature/x"]
    assert worker_argv("abc", release=True) == [*base, "--release"]
    assert worker_argv("abc", title="-weird") == [*base, "--", "-weird"]


def test_main_swallows_sdk_errors_with_the_fixed_stderr_line(monkeypatch, capsys):
    import sys
    import types

    fake = types.ModuleType("iterm2")

    class Connection:
        @staticmethod
        async def async_create():
            raise RuntimeError("cookie=SECRET-COOKIE key=SECRET-KEY")

    fake.Connection = Connection
    monkeypatch.setitem(sys.modules, "iterm2", fake)
    from omc.iterm2_title import main

    assert main(["--session-id", "38B11221-B7E1-4F36-8A3B-50D549172632", "--", "t"]) == 1
    assert main(["--session-id", "38B11221-B7E1-4F36-8A3B-50D549172632", "--release"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "omc: iTerm2 title update failed\nomc: iTerm2 title update failed\n"
    assert "SECRET" not in captured.err


def test_main_requires_exactly_one_of_release_or_title(capsys):
    from omc.iterm2_title import main

    assert main(["--session-id", "abc"]) == 2
    assert main(["--session-id", "abc", "--release", "--", "title"]) == 2
    assert capsys.readouterr().out == ""
