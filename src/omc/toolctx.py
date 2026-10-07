from __future__ import annotations

import contextlib
import http.client
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_UV_KEYS = ("UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_CACHE_DIR")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: returning None makes urllib raise HTTPError for the
    3xx, which ``http_get`` reports as an ordinary ``(3xx, body)`` result."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


@dataclass
class ToolContext:
    home: Path
    env: Mapping[str, str]
    uv_bin: str = "uv"
    uv_env: dict[str, str] = field(default_factory=dict)
    wt_bin: str = "wt"
    git_bin: str = "git"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ToolContext:
        env = dict(os.environ if env is None else env)
        home = Path(env.get("OMC_HOME") or (Path(env.get("HOME") or str(Path.home())) / ".omc"))
        return cls(
            home=home,
            env=env,
            uv_bin=env.get("OMC_UV_BIN", "uv"),
            uv_env={k: env[k] for k in _UV_KEYS if k in env},
            wt_bin=env.get("OMC_WT_BIN", "wt"),
            git_bin=env.get("OMC_GIT_BIN", "git"),
        )

    def uv_argv(self, *args: str) -> list[str]:
        return [self.uv_bin, *args]

    def child_env(self) -> dict[str, str]:
        return {**self.env, **self.uv_env}

    def read_text(self, path: Path) -> str:
        """Read provider-owned state through the same runtime I/O boundary."""
        return path.read_text(encoding="utf-8")

    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        capture: bool = True,
        timeout: float | None = None,
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run argv (text mode) under child_env(); the single subprocess boundary.

        A captured subprocess gets stdin=DEVNULL: a tool that prompts would write the
        prompt into the captured pipe (invisible) and hang; with DEVNULL it gets EOF
        and proceeds or fails fast.
        """
        kwargs: dict[str, object] = {
            "env": {**self.child_env(), **(extra_env or {})},
            "check": check,
            "timeout": timeout,
        }
        if cwd is not None:
            kwargs["cwd"] = cwd
        if capture:
            kwargs["capture_output"] = True
            kwargs["text"] = True
            kwargs["stdin"] = subprocess.DEVNULL
        return subprocess.run(list(argv), **kwargs)  # noqa: S603 - argv list, no shell

    def run_bounded(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a captured command with a hard deadline for its entire process group."""
        kwargs: dict[str, object] = {
            "env": {**self.child_env(), **(extra_env or {})},
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "errors": "replace",
            "start_new_session": True,
        }
        if cwd is not None:
            kwargs["cwd"] = cwd
        proc = subprocess.Popen(list(argv), **kwargs)  # noqa: S603 - argv list, no shell
        try:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(f"command timed out after {timeout}s") from exc
            return subprocess.CompletedProcess(list(argv), proc.returncode, stdout, stderr)
        except BaseException:
            # A child can exit while a descendant still owns the captured pipe;
            # its process group persists after the direct child is reaped.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.communicate()
            raise

    def run_supervised(
        self,
        argv: Sequence[str],
        *,
        heartbeat: Callable[[], object],
        stall_after: float = 300.0,
        poll: float = 1.0,
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
        """Run argv captured like run(), supervised for LIVENESS, not deadline:
        the child (its whole process group — LLM grandchildren included) is
        killed only after ``stall_after`` seconds with NO progress, where
        progress = the heartbeat() token changed OR any output bytes arrived.
        No overall timeout — a healthy wiki run may take 40+ minutes.
        Returns (completed, stalled). POSIX-only (killpg), like this module.

        heartbeat runs on the supervising thread once per ``poll``; its
        exceptions count as "no change" — a broken heartbeat must neither
        kill a healthy child nor crash the supervisor.

        ``on_line``, when given, receives every stdout and stderr line (newline
        stripped) from the reader threads, serialized under one lock like
        ``stream()``. Unlike ``stream()``, a failing callback is CONTAINED, not
        re-raised: this method's job is liveness, and a reporter bug must
        neither kill a healthy child nor crash the supervisor — the same
        doctrine as heartbeat exceptions. Capture is unaffected either way."""
        kwargs: dict[str, object] = {
            "env": {**self.child_env(), **(extra_env or {})},
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "errors": "replace",
            "start_new_session": True,  # own process group so killpg is precise
        }
        if cwd is not None:
            kwargs["cwd"] = cwd
        proc = subprocess.Popen(list(argv), **kwargs)  # noqa: S603 - argv list, no shell
        chunks: dict[str, list[str]] = {"out": [], "err": []}
        activity = [0]  # bytes seen across both pipes; += is read-modify-write,
        # NOT atomic — but a lost update is harmless (add-only counter; at worst
        # one poll window sees "no change" and the stall clock keeps running).

        lock = threading.Lock()  # serializes on_line, as stream() does

        def pump(pipe, key: str) -> None:
            try:
                for raw in pipe:
                    chunks[key].append(raw)
                    activity[0] += len(raw)
                    if on_line is not None:
                        with lock, contextlib.suppress(Exception):
                            on_line(raw.rstrip("\n"))
            finally:
                pipe.close()

        readers = [
            threading.Thread(target=pump, args=(p, k), daemon=True)
            for p, k in ((proc.stdout, "out"), (proc.stderr, "err"))
        ]
        # The child runs in its own session/group (start_new_session), so a
        # terminal SIGINT never reaches it and a KeyboardInterrupt escaping the
        # supervise loop (time.sleep / heartbeat) would exit the parent while the
        # gitnexus+LLM tree kept running detached — burning money. Wrap the whole
        # supervise-and-reap section: on ANY BaseException (Ctrl-C included) kill
        # the group and re-raise. pgid == proc.pid via start_new_session, valid
        # whether or not the leader has been reaped.
        try:
            for t in readers:
                t.start()
            stalled = False
            last_token: object = object()  # sentinel: never equal to a real token
            last_activity = -1
            stamp = time.monotonic()
            while proc.poll() is None:
                time.sleep(poll)
                try:
                    token = heartbeat()
                except Exception:  # noqa: BLE001 - heartbeat failure is not the child's fault
                    token = last_token
                if token != last_token or activity[0] != last_activity:
                    last_token, last_activity = token, activity[0]
                    stamp = time.monotonic()
                elif time.monotonic() - stamp >= stall_after:
                    stalled = True
                    # SIGKILL, not TERM-then-KILL: the gitnexus child tree installs no
                    # graceful handlers (plain fs.writeFile throughout), so TERM buys
                    # nothing. A page truncated mid-write is the same pre-existing risk
                    # a user Ctrl-C has today; resume-skip tolerates it.
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass  # child won the race and exited; the stall verdict stands
                    break
            # Supervision ended when the DIRECT child exited, but a descendant may
            # still hold the inherited pipe fds and wedge silently — bare joins would
            # block forever, re-opening the "never completes" bug this feature fixes.
            # Bound the join by stall_after; if a reader is still alive, treat it as a
            # stall, kill the group once more, then join unbounded (pipes close after
            # the group dies). The leader is already reaped here, so os.getpgid fails;
            # start_new_session makes pgid == the child's pid, so killpg(proc.pid,...).
            for t in readers:
                t.join(timeout=stall_after)
            if any(t.is_alive() for t in readers):
                stalled = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass  # group already gone; the stall verdict stands
                for t in readers:
                    t.join()
            rc = proc.wait()
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass  # group already gone; nothing left to reap
            else:
                # Reap the leader so it does not linger as a zombie; the group is
                # already dead, so this returns promptly.
                with contextlib.suppress(OSError):
                    proc.wait()
            raise
        return (
            subprocess.CompletedProcess(
                list(argv), rc, "".join(chunks["out"]), "".join(chunks["err"])
            ),
            stalled,
        )

    def stream(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        cwd: str | os.PathLike[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> int:
        """Run argv, delivering every stdout/stderr line to ``on_line`` live.

        stdout and stderr are SEPARATE pipes read by two threads — never
        merged into one pipe: pipe writes beyond PIPE_BUF are not atomic, so
        a large stdout line (e.g. a stream-json tool result) could splice
        with a stderr line mid-line. Two readers guarantee whole lines; a
        lock serializes ``on_line`` calls. Per-stream order is preserved;
        cross-stream order is best-effort.

        ``on_line`` exceptions are not swallowed — callers own their callbacks.
        The first exception from either stream is captured; both readers then
        drain their pipes silently (without calling ``on_line``) so the child
        never blocks on a full pipe and the joins cannot deadlock. Once the
        child is reaped, that exception propagates to the caller (the child's
        return code is lost in that case — the raise IS the signal).

        Deliberately NO timeout: used for LLM build stages that may run an
        hour — liveness is the user's call (visible elapsed time + Ctrl-C).
        """
        kwargs: dict[str, object] = {
            "env": {**self.child_env(), **(extra_env or {})},
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "errors": "replace",
        }
        if cwd is not None:
            kwargs["cwd"] = cwd
        proc = subprocess.Popen(list(argv), **kwargs)  # noqa: S603 - argv list, no shell
        lock = threading.Lock()
        error: list[BaseException] = []

        def pump(pipe) -> None:
            try:
                for raw in pipe:
                    with lock:
                        if error:
                            continue  # a callback already failed — drain silently
                        try:
                            on_line(raw.rstrip("\n"))
                        except BaseException as exc:  # noqa: BLE001 - caller's callback owns it
                            error.append(exc)
            finally:
                pipe.close()

        # daemon=True matters only when a KeyboardInterrupt abandons the join below:
        # never strand interpreter shutdown on a wedged child's still-open pipes.
        readers = [
            threading.Thread(target=pump, args=(p,), daemon=True)
            for p in (proc.stdout, proc.stderr)
        ]
        for t in readers:
            t.start()
        for t in readers:
            t.join()
        rc = proc.wait()
        if error:
            raise error[0]
        return rc

    def http_get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, str]:
        """GET ``url``; returns ``(status, body)``. omc's ONLY network primitive —
        it lives here so the subprocess/env/network boundary stays single and
        tests fake one seam (like ``run``/``run_bounded``).

        Never raises: an HTTP error status is a RESULT (``(401, body)``), and a
        transport failure (refused, DNS, timeout) is ``(0, <error text>)``.
        The proxy MAP comes only from ``self.env`` (``http_proxy``/``https_proxy``,
        any case), never ``os.environ`` — with an explicit map urllib adds no
        system proxies. Its bypass check (``no_proxy``/macOS exclusions) is
        urllib's own and still consults the process environment; in production
        ``self.env`` is a copy of ``os.environ`` so the two agree. The caller
        owns redaction: this layer never sees what a header means. Redirects are
        never followed (a 3xx is returned as the result): urllib would replay every
        header, credentials included, to whatever host ``Location`` names.
        """
        proxies = {
            scheme: self.env[k]
            for scheme in ("http", "https")
            for k in (f"{scheme}_proxy", f"{scheme.upper()}_PROXY")
            if self.env.get(k)
        }
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), _NoRedirect())
        try:
            req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
            try:
                with opener.open(req, timeout=timeout) as resp:  # noqa: S310 - https/http only, caller-built URL
                    return resp.status, resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, exc.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # URLError is an OSError; HTTPException (BadStatusLine, IncompleteRead)
            # is not and urllib does not wrap it; ValueError = a malformed URL.
            return 0, str(exc)


def tool_version(ctx: ToolContext, argv: Sequence[str], *, timeout: float = 5) -> tuple[bool, str]:
    """Probe a tool's --version. Returns (present, detail); never raises."""
    try:
        cp = ctx.run(argv, capture=True, timeout=timeout)
    except FileNotFoundError:
        return False, f"not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as exc:
        return False, str(exc)
    if cp.returncode != 0:
        reason = (cp.stderr or cp.stdout or "").strip() or f"exit code {cp.returncode}"
        return False, reason
    return True, (cp.stdout or cp.stderr or "").strip()
