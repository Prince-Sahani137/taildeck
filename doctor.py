"""`python taildeck.py --doctor`: check that everything TailDeck needs is in place.

Prints one line per check ([ok] / [todo] / [warn]) with a platform-specific hint
for anything missing. Exit status is 1 when something required is missing.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import psutil

import metrics
import services

IS_WINDOWS = sys.platform == "win32"
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def exposed_listeners(ports) -> list:
    """Listening sockets on *ports* that accept connections from other machines."""
    wanted = set(int(p) for p in ports)
    found = []
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return found
    for c in conns:
        if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port in wanted \
                and c.laddr.ip not in LOOPBACK:
            found.append((c.laddr.ip, c.laddr.port))
    return sorted(set(found))


def listening_on(port: int) -> bool:
    try:
        return any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port
                   for c in psutil.net_connections(kind="inet"))
    except (psutil.AccessDenied, OSError):
        return False


def _hint(windows: str, linux: str) -> str:
    return windows if IS_WINDOWS else linux


def run(t) -> int:
    """*t* is the imported taildeck module (its config is the source of truth)."""
    here: Path = t.HERE
    lines: list = []
    missing = 0

    def ok(msg):
        lines.append(("ok", msg, ""))

    def todo(msg, hint=""):
        nonlocal missing
        missing += 1
        lines.append(("todo", msg, hint))

    def warn(msg, hint=""):
        lines.append(("warn", msg, hint))

    ok(f"Python {sys.version.split()[0]} on {sys.platform}")
    if sys.version_info < (3, 9):
        todo("Python 3.9 or newer is required")

    t.load_password()
    if not t.PASSWORD or t.PASSWORD in ("CHANGE_ME", "YOUR_PASSWORD"):
        todo("PANEL_PASSWORD is not set", "copy .env.example to .env and set a long random PANEL_PASSWORD")
    elif len(t.PASSWORD) < 12:
        warn("PANEL_PASSWORD is short", "use at least 12 characters (python -c \"import secrets;print(secrets.token_hex(16))\")")
    else:
        ok("PANEL_PASSWORD is set")

    ts_bin = metrics.tailscale_bin()
    if not (shutil.which(ts_bin) or os.path.exists(ts_bin)):
        todo("Tailscale CLI not found", _hint("winget install -e --id Tailscale.Tailscale",
                                              "see https://tailscale.com/download"))
    else:
        info = metrics.tailscale()
        if info and info.get("backend") == "Running":
            ok(f"Tailscale is running ({info.get('name') or info.get('ip') or 'this node'})")
        else:
            todo("Tailscale is installed but not signed in / running", "run: tailscale up")

    ports = []
    for key in t.WORKSPACE_KEYS:
        label = next((w["label"] for w in t.WORKSPACES_CFG if w["key"] == key), key)
        if key == "term":
            ttyd = services.find_ttyd(here)
            if shutil.which(ttyd) or os.path.isfile(ttyd):
                ok(f"{label}: ttyd found")
            else:
                todo(f"{label}: ttyd not found",
                     _hint("run windows\\setup.ps1 -InstallDeps (downloads it to .\\tools), or get ttyd.exe from "
                           "https://github.com/tsl0922/ttyd/releases and set TTYD_BIN",
                           "install ttyd (e.g. apt install ttyd) or set TTYD_BIN"))
            continue
        # desktop tabs: websockify + noVNC + a VNC server
        if importlib.util.find_spec("websockify") or shutil.which("websockify"):
            ok(f"{label}: websockify found")
        else:
            todo(f"{label}: websockify not found", "pip install websockify")
        nv = services.novnc_dir(here)
        if nv and (Path(nv) / "vnc.html").is_file():
            ok(f"{label}: noVNC found ({nv})")
        else:
            todo(f"{label}: noVNC web files not found",
                 _hint("run windows\\setup.ps1 -DownloadNoVNC, or set NOVNC_DIR", "apt install novnc, or set NOVNC_DIR"))
        port = int(os.environ["VNC_HYPR_PORT" if key == "hypr" else "VNC_XFCE_PORT"])
        ports.append(port)
        if listening_on(port):
            ok(f"{label}: a VNC server is listening on port {port}")
        else:
            todo(f"{label}: nothing is listening on VNC port {port}",
                 _hint("install a VNC server, e.g. winget install -e --id GlavSoft.TightVNC (or TigerVNC.TigerVNC), "
                       "set a VNC password and restrict it to localhost",
                       "start wayvnc inside your Hyprland session (wayvnc 127.0.0.1 5900)" if key == "hypr"
                       else "start the virtual desktop: systemctl --user enable --now taildeck-xfce"))
        # thumbnails are optional: only a warning
        if key == "hypr" and not shutil.which("grim"):
            warn(f"{label}: no live preview", "install grim")
        if key == "xfce":
            if sys.platform in ("win32", "darwin"):
                if not importlib.util.find_spec("PIL"):
                    warn(f"{label}: no live preview", "pip install Pillow")
            elif not any(shutil.which(x) for x in ("scrot", "maim", "import")):
                warn(f"{label}: no live preview", "install scrot, maim or ImageMagick")

    exposed = exposed_listeners(ports)
    for ip, port in exposed:
        warn(f"VNC port {port} accepts connections from other machines (bound to {ip})",
             "configure the VNC server to listen on localhost only; TailDeck is the access control")

    spec_path = here / os.getenv("SERVICES_FILE", "services.json")
    if spec_path.exists():
        try:
            n = len(services.load_specs(spec_path) or [])
            ok(f"{spec_path.name}: {n} service(s) defined")
        except ValueError as exc:
            todo(f"{spec_path.name} is invalid: {exc}")
    ok(f"service manager: {t.MANAGER.name}; tabs: {', '.join(t.WORKSPACE_KEYS)}")

    tags = {"ok": "[ok]  ", "todo": "[todo]", "warn": "[warn]"}
    for status, msg, hint in lines:
        print(f"{tags[status]} {msg}")
        if hint:
            print(f"         -> {hint}")
    print()
    print("Everything required is in place." if not missing
          else f"{missing} required item(s) missing; fix the [todo] lines above.")
    return 1 if missing else 0
