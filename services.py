"""Service management for TailDeck: status, restart and logs.

Two interchangeable managers share one interface:

* ``SystemdManager``: Linux user units (``systemctl --user`` / ``journalctl``).
* ``ProcessManager``: TailDeck starts, supervises and restarts the console
  backends (websockify, ttyd, ...) itself and keeps their output in memory.
  Works on Windows, macOS and Linux without systemd.

``choose_manager()`` picks systemd when it is usable and falls back to the
process manager otherwise (override with SERVICE_MANAGER=systemd|process).
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import psutil

IS_WINDOWS = sys.platform == "win32"
NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEWGROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _run(cmd, timeout=10, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env=env, stdin=subprocess.DEVNULL, creationflags=NOWINDOW,
                              encoding="utf-8", errors="replace")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# interface
# --------------------------------------------------------------------------- #

class ServiceManager:
    name = "base"

    def start(self) -> None:
        """Called once when the panel starts."""

    def stop(self) -> None:
        """Called once when the panel exits."""

    def states(self, units: list[str]) -> dict:
        """{unit: {"active": bool, "state": str, "enabled": bool}}"""
        raise NotImplementedError

    def restart(self, units: list[str]) -> None:
        raise NotImplementedError

    def log_lines(self, unit: str, history: int = 150) -> Iterator[Optional[str]]:
        """Yield recent lines, then follow. ``None`` is a keep-alive heartbeat."""
        raise NotImplementedError

    def failed_units(self) -> list[str]:
        return []


# --------------------------------------------------------------------------- #
# systemd
# --------------------------------------------------------------------------- #

class SystemdManager(ServiceManager):
    name = "systemd"

    @staticmethod
    def available() -> bool:
        if not shutil.which("systemctl"):
            return False
        r = _run(["systemctl", "--user", "show-environment"], timeout=5)
        return bool(r and r.returncode == 0)

    def states(self, units: list[str]) -> dict:
        out: dict = {}
        if not units:
            return out
        r = _run(["systemctl", "--user", "show", "-p", "Id", "-p", "ActiveState",
                  "-p", "UnitFileState", *sorted(set(units))], timeout=8)
        if not r or r.returncode != 0:
            return out
        block: dict = {}
        for line in (r.stdout or "").splitlines() + [""]:
            if not line.strip():
                if block.get("Id"):
                    out[block["Id"]] = {
                        "active": block.get("ActiveState", "unknown") == "active",
                        "state": block.get("ActiveState", "unknown"),
                        "enabled": block.get("UnitFileState", "unknown") == "enabled",
                    }
                block = {}
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                block[k] = v
        return out

    def restart(self, units: list[str]) -> None:
        for unit in units:
            _run(["systemctl", "--user", "restart", unit], timeout=60)
            time.sleep(1)

    def log_lines(self, unit: str, history: int = 150) -> Iterator[Optional[str]]:
        uid = os.getuid()
        env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{uid}",
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{uid}/bus")
        try:
            proc = subprocess.Popen(
                ["journalctl", "--user", "-u", unit, "-n", str(history), "-f",
                 "-o", "short-iso", "--no-pager"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env)
        except OSError:
            yield "could not start journalctl"
            return
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                yield line.rstrip()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


# --------------------------------------------------------------------------- #
# process supervisor
# --------------------------------------------------------------------------- #

@dataclass
class ServiceSpec:
    id: str
    label: str
    cmd: list
    cwd: Optional[str] = None
    env: dict = field(default_factory=dict)
    autostart: bool = True
    workspace: Optional[str] = None   # hypr | xfce | term: the UI tab this belongs to
    role: str = "main"                # "proxy" marks the websockify/ttyd front of a workspace
    preflight: list = field(default_factory=list)  # messages logged at start (setup hints)


class LogBuffer:
    """Thread-safe ring buffer of timestamped lines with blocking follow."""

    def __init__(self, maxlen: int = 2000):
        self._lines: collections.deque = collections.deque(maxlen=maxlen)
        self._seq = 0
        self._cond = threading.Condition()

    def add(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._cond:
            for line in str(text).splitlines() or [""]:
                self._seq += 1
                self._lines.append((self._seq, f"{stamp} {line}"))
            self._cond.notify_all()

    def tail(self, n: int) -> tuple:
        with self._cond:
            items = list(self._lines)[-n:] if n > 0 else []
            return [t for _, t in items], self._seq

    def wait_after(self, seq: int, timeout: float) -> list:
        with self._cond:
            if self._seq <= seq:
                self._cond.wait(timeout)
            return [(s, t) for s, t in self._lines if s > seq]


class _Runtime:
    def __init__(self, spec: ServiceSpec):
        self.spec = spec
        self.buffer = LogBuffer()
        self.popen: Optional[subprocess.Popen] = None
        self.want_running = False
        self.fail_count = 0
        self.started_at = 0.0
        self.next_start = 0.0
        self.gave_up = False
        self.missing: Optional[str] = None
        self.last_exit: Optional[int] = None


def _python_exe() -> str:
    """sys.executable, but never the windowless pythonw.exe (no stdio for children)."""
    exe = sys.executable or "python"
    if IS_WINDOWS and exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(cand):
            return cand
    return exe


def expand(arg: str, env=None) -> str:
    env = os.environ if env is None else env
    return _VAR_RE.sub(lambda m: env.get(m.group(1), ""), str(arg))


class ProcessManager(ServiceManager):
    name = "process"
    MAX_FAILS = 8          # consecutive crashes before giving up until a manual restart
    STABLE_AFTER = 60.0    # a run longer than this resets the crash counter
    BACKOFF_BASE = 1.0     # restart delay = BACKOFF_BASE * 2**crashes, capped at 30s

    def __init__(self, specs: list, pidfile: Optional[Path] = None):
        self.pidfile = pidfile
        self.specs = {s.id: s for s in specs}
        self._rt = {s.id: _Runtime(s) for s in specs}
        self._extra: dict = {}
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- extra read-only log sources (e.g. the panel's own log) -------------- #

    def add_buffer(self, key: str, buf: LogBuffer) -> None:
        self._extra[key] = buf

    def _buffer(self, unit: str) -> Optional[LogBuffer]:
        if unit in self._extra:
            return self._extra[unit]
        rt = self._rt.get(unit)
        return rt.buffer if rt else None

    # -- lifecycle ----------------------------------------------------------- #

    # -- stale children (parent was hard-killed, e.g. Task Scheduler "End") ----- #

    def _save_pids(self) -> None:
        if not self.pidfile:
            return
        children = []
        for rt in self._rt.values():
            if rt.popen is not None and rt.popen.poll() is None:
                try:
                    children.append({"pid": rt.popen.pid,
                                     "created": psutil.Process(rt.popen.pid).create_time()})
                except psutil.Error:
                    pass
        try:
            me = psutil.Process(os.getpid())
            data = {"parent": {"pid": me.pid, "created": me.create_time()}, "children": children}
            self.pidfile.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, psutil.Error):
            pass

    def reap_stale(self) -> int:
        """Kill children recorded by a previous run whose parent is no longer alive."""
        if not self.pidfile:
            return 0
        try:
            data = json.loads(self.pidfile.read_text(encoding="utf-8"))
            parent, rows = data.get("parent") or {}, data.get("children") or []
        except (OSError, ValueError, AttributeError):
            return 0
        try:  # never touch the children of a TailDeck that is still running
            p = psutil.Process(int(parent["pid"]))
            if abs(p.create_time() - float(parent["created"])) < 2:
                return 0
        except (psutil.Error, KeyError, ValueError, TypeError):
            pass
        killed = 0
        for row in rows if isinstance(rows, list) else []:
            try:
                proc = psutil.Process(int(row["pid"]))
                # the creation time guards against a recycled PID
                if abs(proc.create_time() - float(row["created"])) > 2:
                    continue
                for p in proc.children(recursive=True) + [proc]:
                    try:
                        p.kill()
                    except psutil.Error:
                        pass
                killed += 1
            except (psutil.Error, KeyError, ValueError, TypeError):
                continue
        try:
            self.pidfile.write_text("{}", encoding="utf-8")
        except OSError:
            pass
        return killed

    def start(self) -> None:
        self.reap_stale()
        for rt in self._rt.values():
            rt.want_running = rt.spec.autostart
        self._thread = threading.Thread(target=self._loop, name="supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            for rt in self._rt.values():
                rt.want_running = False
                self._kill(rt)

    def _loop(self) -> None:
        while not self._stopping.is_set():
            now = time.time()
            with self._lock:
                for rt in self._rt.values():
                    self._tick(rt, now)
            self._stopping.wait(1.0)

    def _tick(self, rt: _Runtime, now: float) -> None:
        if not rt.want_running or rt.gave_up:
            return
        if rt.popen is not None:
            code = rt.popen.poll()
            if code is None:
                return
            rt.last_exit = code
            rt.popen = None
            ran = now - rt.started_at
            rt.fail_count = 0 if ran > self.STABLE_AFTER else rt.fail_count + 1
            if rt.fail_count >= self.MAX_FAILS:
                rt.gave_up = True
                rt.buffer.add(f"exited with code {code}; giving up after "
                              f"{rt.fail_count} crashes (restart it from the panel)")
                return
            delay = min(30, self.BACKOFF_BASE * 2 ** rt.fail_count)
            rt.next_start = now + delay
            rt.buffer.add(f"exited with code {code}; restarting in {delay:g}s")
            return
        if now >= rt.next_start:
            self._spawn(rt)

    def _spawn(self, rt: _Runtime) -> None:
        spec = rt.spec
        env = dict(os.environ)
        env.update({k: expand(v, env) for k, v in spec.env.items()})
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["PYTHONIOENCODING"] = "utf-8"
        args = [expand(a, env) for a in spec.cmd]
        if args[0] in ("python", "python3"):
            exe = _python_exe()
        else:
            exe = shutil.which(args[0]) or (args[0] if os.path.isfile(args[0]) else None)
        for note in spec.preflight:
            rt.buffer.add(note)
        if not exe:
            rt.missing = args[0]
            rt.buffer.add(f"executable not found: {args[0]} (install it or set its path in services.json)")
            rt.next_start = time.time() + 30
            return
        rt.missing = None
        shown = self._redact(" ".join([os.path.basename(exe)] + args[1:]))
        rt.buffer.add(f"starting: {shown}")
        kwargs: dict = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = NOWINDOW | NEWGROUP
        else:
            kwargs["start_new_session"] = True
        try:
            rt.popen = subprocess.Popen(
                [exe] + args[1:], cwd=expand(spec.cwd, env) if spec.cwd else None, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **kwargs)
        except OSError as exc:
            rt.buffer.add(f"could not start: {exc}")
            rt.next_start = time.time() + 30
            return
        rt.started_at = time.time()
        threading.Thread(target=self._reader, args=(rt, rt.popen), daemon=True).start()
        self._save_pids()

    def _reader(self, rt: _Runtime, popen: subprocess.Popen) -> None:
        try:
            for raw in iter(popen.stdout.readline, b""):  # type: ignore[union-attr]
                rt.buffer.add(self._redact(raw.decode("utf-8", "replace").rstrip()))
        except (OSError, ValueError):
            pass

    @staticmethod
    def _redact(text: str) -> str:
        secret = os.environ.get("PANEL_PASSWORD", "")
        return text.replace(secret, "****") if len(secret) >= 4 else text

    def _kill(self, rt: _Runtime) -> None:
        popen, rt.popen = rt.popen, None
        if popen is None or popen.poll() is not None:
            return
        try:
            parent = psutil.Process(popen.pid)
            procs = parent.children(recursive=True) + [parent]
        except psutil.Error:
            procs = []
        for p in procs:
            try:
                p.terminate()
            except psutil.Error:
                pass
        if procs:
            _, alive = psutil.wait_procs(procs, timeout=3)
            for p in alive:
                try:
                    p.kill()
                except psutil.Error:
                    pass
        try:
            popen.wait(timeout=3)
        except subprocess.TimeoutExpired:
            popen.kill()
        self._save_pids()

    # -- interface ------------------------------------------------------------ #

    def states(self, units: list[str]) -> dict:
        out: dict = {}
        with self._lock:
            for unit in units:
                rt = self._rt.get(unit)
                if rt is None:
                    continue
                running = rt.popen is not None and rt.popen.poll() is None
                if running:
                    state = "active"
                elif rt.gave_up or rt.missing:
                    state = "failed"
                elif rt.want_running:
                    state = "activating"
                else:
                    state = "inactive"
                out[unit] = {"active": running, "state": state,
                             "enabled": rt.spec.autostart}
        return out

    def restart(self, units: list[str]) -> None:
        for unit in units:
            rt = self._rt.get(unit)
            if rt is None:
                continue
            with self._lock:
                rt.buffer.add("restart requested")
                self._kill(rt)
                rt.want_running = True
                rt.gave_up = False
                rt.fail_count = 0
                rt.next_start = 0.0
                self._spawn(rt)
            time.sleep(0.5)

    def failed_units(self) -> list[str]:
        with self._lock:
            return sorted(u for u, s in self.states(list(self._rt)).items()
                          if s["state"] == "failed")

    def log_lines(self, unit: str, history: int = 150) -> Iterator[Optional[str]]:
        buf = self._buffer(unit)
        if buf is None:
            yield f"no such service: {unit}"
            return
        lines, seq = buf.tail(history)
        yield from lines
        while not self._stopping.is_set():
            items = buf.wait_after(seq, 15)
            if not items:
                yield None  # heartbeat, lets the HTTP layer notice a closed browser tab
                continue
            seq = items[-1][0]
            for _, text in items:
                yield text


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

def load_specs(path: Path) -> Optional[list]:
    """Read services.json; None when the file does not exist."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    entries = raw.get("services") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f'{path.name}: expected {{"services": [...]}}')
    specs, seen = [], set()
    for i, e in enumerate(entries):
        where = f"{path.name}: services[{i}]"
        if not isinstance(e, dict):
            raise ValueError(f"{where}: must be an object")
        sid = e.get("id")
        if not isinstance(sid, str) or not _ID_RE.match(sid):
            raise ValueError(f"{where}: 'id' must match [A-Za-z0-9_.-]+")
        if sid in seen:
            raise ValueError(f"{where}: duplicate id {sid!r}")
        cmd = e.get("cmd")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
            raise ValueError(f"{where}: 'cmd' must be a non-empty list of strings")
        seen.add(sid)
        specs.append(ServiceSpec(
            id=sid, label=str(e.get("label") or sid), cmd=cmd, cwd=e.get("cwd"),
            env={str(k): str(v) for k, v in (e.get("env") or {}).items()},
            autostart=bool(e.get("autostart", True)),
            workspace=e.get("workspace"), role=str(e.get("role") or "main")))
    return specs


def default_specs(workspaces: list, here: Path) -> list:
    """Sensible built-in services used when there is no services.json."""
    env = os.environ
    novnc = env.get("NOVNC_DIR", "").strip()
    if not novnc:
        bundled = here / "novnc"
        novnc = str(bundled) if bundled.is_dir() else ("" if IS_WINDOWS else "/usr/share/novnc")
    specs: list = []

    def ws_proxy(key: str, label: str, listen: str, vnc: str) -> ServiceSpec:
        hints = []
        cmd = ["python", "-m", "websockify"]
        if novnc and Path(novnc).is_dir():
            cmd += ["--web", novnc, "--web-auth"]
        else:
            hints.append("noVNC web files not found: download noVNC and set NOVNC_DIR "
                         "in .env, or the console page will not load")
            cmd += ["--web-auth"]
        cmd += ["--auth-plugin", "BasicHTTPAuth",
                "--auth-source", "${PANEL_USER}:${PANEL_PASSWORD}",
                f"127.0.0.1:${{{listen}}}", f"127.0.0.1:${{{vnc}}}"]
        return ServiceSpec(id=f"taildeck-ws-{key}", label=label, cmd=cmd,
                           workspace=key, role="proxy", preflight=hints)

    if "hypr" in workspaces:
        specs.append(ws_proxy("hypr", "noVNC proxy (Hyprland)", "WS_HYPR_PORT", "VNC_HYPR_PORT"))
    if "xfce" in workspaces:
        specs.append(ws_proxy("xfce", "noVNC proxy (desktop)", "WS_XFCE_PORT", "VNC_XFCE_PORT"))
    if "term" in workspaces:
        shell = env.get("TERM_SHELL", "").strip()
        if not shell:
            if IS_WINDOWS:
                shell = shutil.which("pwsh") or shutil.which("powershell") or "cmd.exe"
            else:
                shell = env.get("SHELL") or "/bin/bash"
        specs.append(ServiceSpec(
            id="taildeck-term", label="Terminal (ttyd)", workspace="term", role="main",
            cmd=[env.get("TTYD_BIN", "").strip() or "ttyd", "-W", "-i", "127.0.0.1",
                 "-p", "${TERM_PORT}", "-c", "${PANEL_USER}:${PANEL_PASSWORD}", shell]))
    return specs


def choose_manager(preference: str, specs_path: Path, workspaces: list, here: Path):
    """Return (manager, specs or None). specs is None for systemd."""
    pref = (preference or "auto").strip().lower()
    if pref not in ("auto", "systemd", "process"):
        raise ValueError("SERVICE_MANAGER must be auto, systemd or process")
    use_systemd = (pref == "systemd") or (pref == "auto" and not IS_WINDOWS
                                          and SystemdManager.available())
    if use_systemd:
        return SystemdManager(), None
    specs = load_specs(specs_path)
    if specs is None:
        specs = default_specs(workspaces, here)
    port = os.environ.get("PANEL_PORT", "6100")
    return ProcessManager(specs, here / f".taildeck-pids-{port}.json"), specs
