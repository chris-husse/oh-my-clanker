from omc.terminals import detect_terminal
from omc.toolctx import ToolContext


def test_osc_fallback():
    t = detect_terminal({})
    assert t.name == "osc"
    assert t.title_sequence("my-slug") == "\033]0;my-slug\007"


def test_iterm2_detected_reuses_osc0():
    t = detect_terminal({"TERM_PROGRAM": "iTerm.app"})
    assert t.name == "iterm2"
    assert t.title_sequence("x") == "\033]0;x\007"


def test_portable_terminal_writes_title(capsys, tmp_path):
    ctx = ToolContext(home=tmp_path, env={})
    assert detect_terminal(ctx.env).set_title(ctx, "feature/name") is True
    assert capsys.readouterr().out == "\033]0;feature/name\007"


def test_iterm2_session_id_accepts_bare_and_prefixed_uuid():
    from omc.terminals import iterm2_session_id

    uuid = "38B11221-B7E1-4F36-8A3B-50D549172632"
    assert iterm2_session_id({"ITERM_SESSION_ID": uuid}) == uuid
    assert iterm2_session_id({"ITERM_SESSION_ID": f"w0t1p0:{uuid}"}) == uuid
    assert iterm2_session_id({"ITERM_SESSION_ID": uuid.lower()}) == uuid.lower()


def test_iterm2_session_id_rejects_missing_and_malformed():
    from omc.terminals import iterm2_session_id

    uuid = "38B11221-B7E1-4F36-8A3B-50D549172632"
    for env in (
        {},
        {"ITERM_SESSION_ID": ""},
        {"ITERM_SESSION_ID": "bad"},
        {"ITERM_SESSION_ID": f"unrelated:{uuid}"},
        {"ITERM_SESSION_ID": f"w0t1p0:{uuid}:extra"},
        {"ITERM_SESSION_ID": "38B11221B7E14F368A3B50D549172632"},  # no dashes: not a round trip
        {"ITERM_SESSION_ID": "w0t1p0:"},
    ):
        assert iterm2_session_id(env) is None, env
