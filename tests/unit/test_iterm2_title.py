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
