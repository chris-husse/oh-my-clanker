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
