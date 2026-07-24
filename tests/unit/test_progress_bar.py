import io
import time

from omc.cli.progress_bar import BarThread, render_bar


def test_render_bar_matches_build_golden():
    assert render_bar(21, 48802.0) == "[====>             ]  21% (13:33:22)"


def test_render_bar_complete():
    assert render_bar(100, 61.0) == "[==================] 100% (00:01:01)"


def test_render_bar_fresh_zero_shows_arrow_head():
    assert render_bar(0, 0.0).startswith("[>")


def test_render_bar_indeterminate_bounces_with_spin():
    first = render_bar(None, 0.0, spin=0)
    second = render_bar(None, 0.0, spin=1)
    assert "<=>" in first and "<=>" in second and first != second
    assert " --% " in first


class _NonTTY(io.StringIO):
    pass  # StringIO.isatty() is False


class _TTY(io.StringIO):
    def isatty(self):
        return True


class _Tracker:
    def __init__(self):
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def render(self, now=None):
        return "RENDERED"


def test_bar_thread_is_noop_without_tty():
    out = _NonTTY()
    bar = BarThread(_Tracker(), out=out)
    bar.start()
    bar.stop()
    assert out.getvalue() == ""


def test_bar_thread_refreshes_and_redraws_on_tty():
    out = _TTY()
    tracker = _Tracker()
    bar = BarThread(tracker, out=out)
    bar.start()
    time.sleep(1.3)  # one 1s redraw beat
    bar.stop()
    assert tracker.refreshed >= 1
    assert "\rRENDERED" in out.getvalue()
    assert out.getvalue().endswith("\r\x1b[K")  # stop clears the bar line


def test_multibar_paints_block_and_clears_on_stop():
    from omc.cli.progress_bar import MultiBarThread

    out = _TTY()
    bar = MultiBarThread(lambda: ["ROW-A", "ROW-B"], out=out)
    bar.start()
    time.sleep(1.3)  # one beat
    bar.stop()
    text = out.getvalue()
    assert "ROW-A" in text and "ROW-B" in text
    assert "\r\x1b[K" in text  # line-erase repaint discipline
    assert "\x1b[2A" in text  # cursor-up over the 2-line block (stop's erase)


def test_multibar_is_noop_without_tty():
    from omc.cli.progress_bar import MultiBarThread

    out = _NonTTY()
    bar = MultiBarThread(lambda: ["ROW"], out=out)
    bar.start()
    bar.stop()
    assert out.getvalue() == ""
