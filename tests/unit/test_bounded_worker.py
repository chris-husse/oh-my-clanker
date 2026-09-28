import os
import sys
import time

import pytest

from omc.toolctx import ToolContext


def test_bounded_run_captures_output_and_environment(tmp_path):
    ctx = ToolContext(home=tmp_path, env={"OMC_TEST": "base"})
    cp = ctx.run_bounded(
        [sys.executable, "-c", "import os; print(os.environ['OMC_TEST'])"],
        timeout=2,
        extra_env={"OMC_TEST": "override"},
    )
    assert cp.returncode == 0
    assert cp.stdout == "override\n"


def test_bounded_run_reaps_timed_out_child(tmp_path):
    ctx = ToolContext(home=tmp_path, env={})
    pidfile = tmp_path / "pid"
    script = "import os,time,sys; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)"
    with pytest.raises(TimeoutError):
        ctx.run_bounded([sys.executable, "-c", script, str(pidfile)], timeout=0.5)
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_bounded_run_kills_descendant_after_timeout(tmp_path):
    ctx = ToolContext(home=tmp_path, env={})
    marker = tmp_path / "late"
    child = (
        "import time,pathlib,sys; time.sleep(1); pathlib.Path(sys.argv[1]).write_text('survived')"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
        "time.sleep(30)"
    )
    with pytest.raises(TimeoutError):
        ctx.run_bounded([sys.executable, "-c", parent, child, str(marker)], timeout=0.3)
    time.sleep(1.1)
    assert not marker.exists()
