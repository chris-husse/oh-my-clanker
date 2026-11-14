import io
import json
import os
import shlex
from types import SimpleNamespace

import pytest

from omc.herdr import osc7_report, percent_encode_path
from omc.internal import run_internal


def test_percent_encode_matches_fish_url_escape():
    assert percent_encode_path("/home/a b/x%y/ü") == "/home/a%20b/x%25y/%C3%BC"
    assert percent_encode_path("/t/repo's space/A-Z_a.z~0") == "/t/repo%27s%20space/A-Z_a.z~0"
    assert percent_encode_path("/x/a+b,c=d@e:f!g(h)*") == "/x/a%2Bb%2Cc%3Dd%40e%3Af%21g%28h%29%2A"


def test_percent_encode_never_raises_on_non_utf8_bytes():
    assert percent_encode_path(os.fsdecode(b"/x/\xff")) == "/x/%FF"


def test_osc7_report_is_hostless_file_uri():
    assert osc7_report("/w/a b") == "\x1b]7;file:///w/a%20b\x07"


def _ps_stub(bindir, line, rc=0):
    log = bindir / "ps.log"
    script = bindir / "ps"
    script.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        f"printf '%s\\n' {shlex.quote(line)}\nexit {rc}\n"
    )
    script.chmod(0o755)
    return log


@pytest.fixture
def herdr_env(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    (dev / "pts").mkdir(parents=True)
    (dev / "pts" / "7").write_bytes(b"")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr("omc.herdr._DEV_DIR", dev)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("TERM_PROGRAM", "herdr")

    def stdin(text):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    return SimpleNamespace(dev=dev, bindir=bindir, stdin=stdin, log=bindir / "ps.log")


def test_herdr_cwd_writes_encoded_report_to_first_ancestor_terminal(herdr_env, capsys):
    _ps_stub(herdr_env.bindir, "  4242 pts/7")
    herdr_env.stdin(json.dumps({"cwd": "/home/a b/x%y/ü", "hook_event_name": "SessionStart"}))
    assert run_internal(["herdr-cwd"]) == 0
    assert (
        herdr_env.dev / "pts" / "7"
    ).read_bytes() == b"\x1b]7;file:///home/a%20b/x%25y/%C3%BC\x07"
    assert herdr_env.log.read_text().splitlines()[0] == f"-o ppid= -o tty= -p {os.getppid()}"
    out, err = capsys.readouterr()
    assert out == "" and err == ""


@pytest.mark.parametrize("term_program", [None, "tmux"])
def test_herdr_cwd_outside_herdr_does_nothing(herdr_env, monkeypatch, capsys, term_program):
    if term_program is None:
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
    else:
        monkeypatch.setenv("TERM_PROGRAM", term_program)
    _ps_stub(herdr_env.bindir, "  4242 pts/7")
    herdr_env.stdin(json.dumps({"cwd": "/w"}))
    assert run_internal(["herdr-cwd"]) == 0
    assert (herdr_env.dev / "pts" / "7").read_bytes() == b""
    assert not herdr_env.log.exists()
    out, err = capsys.readouterr()
    assert out == "" and err == ""


@pytest.mark.parametrize(
    ("stdin", "ps_line", "ps_rc"),
    [
        ("", "4242 pts/7", 0),
        ("not json", "4242 pts/7", 0),
        ('{"session_id": "x"}', "4242 pts/7", 0),
        ('{"cwd": "relative/dir"}', "4242 pts/7", 0),
        ('{"cwd": "/x/\\ud800"}', "4242 pts/7", 0),
        ('{"cwd": "/w"}', "    1 ?", 0),
        ('{"cwd": "/w"}', "", 1),
        ('{"cwd": "/w"}', "4242 ?", 0),
        ('{"cwd": "/w"}', "4242 ??", 0),
        ('{"cwd": "/w"}', "4242 pts/../pts/7", 0),
        ('{"cwd": "/w"}', "4242 pts/9", 0),
    ],
    ids=[
        "no-input",
        "not-json",
        "no-cwd",
        "relative-cwd",
        "lone-surrogate-cwd",
        "no-ancestor-terminal",
        "ps-fails",
        "never-reaches-pid-1",
        "macos-no-terminal",
        "not-a-device-name",
        "device-absent",
    ],
)
def test_herdr_cwd_ends_silently(herdr_env, capsys, stdin, ps_line, ps_rc):
    _ps_stub(herdr_env.bindir, ps_line, ps_rc)
    herdr_env.stdin(stdin)
    assert run_internal(["herdr-cwd"]) == 0
    assert (herdr_env.dev / "pts" / "7").read_bytes() == b""
    out, err = capsys.readouterr()
    assert out == "" and err == ""


def test_herdr_cwd_rejects_extra_arguments():
    assert run_internal(["herdr-cwd", "x"]) == 2
