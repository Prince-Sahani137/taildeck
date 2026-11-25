#!/usr/bin/env python3
"""TailDeck — a tailnet-only control deck for remote desktops (VNC) + terminal.

Serves the dashboard and these endpoints (default port 6100):

  GET  /                     the page
  GET  /api/stats            live CPU/GPU/RAM/disk/containers + host info
  GET  /api/history          last 20 min of samples (3 s apart) for charts
  GET  /api/config           non-secret settings for the page
  GET  /api/units            systemd state for every unit behind the cards
  GET  /api/thumb?which=     PNG thumbnail (hypr via grim, xfce via RFB)
  GET  /api/logs?unit=       live journal stream (SSE)
  POST /api/action           restart a service or the whole stack
  GET  /api/files            list the shared files folder
  POST /api/files?name=      upload one file (raw body)
  GET  /api/files/get?name=  download one file
  *    /hypr/* /xfce/* /term/*   proxied consoles (HTTP + WebSocket)

The console proxies strip the path prefix, inject the shared Basic Auth and
relay the WebSocket bytes, so noVNC and ttyd live inside the panel's iframes
without a second login prompt.

Safety: binds loopback (or the tailnet IP) only, requires HTTP Basic Auth,
every mutating request must carry `X-Panel: 1`, and every browser request that
carries an Origin header (including WebSocket upgrades) must come from the same
origin. A cross-site page therefore cannot ride on cached Basic Auth.
"""
from __future__ import annotations

import base64
from html import escape as html_escape
import collections
import hmac
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
sys.path.insert(0, str(HERE))

# --------------------------------------------------------------------------- #
# environment config
# --------------------------------------------------------------------------- #

def load_env(path=None):
    """Load KEY=VALUE from .env into os.environ without overwriting existing vars."""
    if path is None:
        path = HERE / ".env"
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass

# Load project config from .env (environment vars take precedence).
load_env()

# -- stats config from env --
STATS_TTL = int(os.getenv("STATS_TTL", "2"))
THUMB_TTL = int(os.getenv("THUMB_TTL", "5"))
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD", str(512 * 1024 * 1024)))
FILES_DIR = Path(os.getenv("FILES_DIR", str(HOME / "Downloads")))
PORT = int(os.getenv("PANEL_PORT", "6100"))
USER = os.getenv("PANEL_USER", "admin")
PASSWORD = os.getenv("PANEL_PASSWORD", "")
HISTORY_INTERVAL = int(os.getenv("HISTORY_INTERVAL", "3"))
HISTORY_SIZE = int(os.getenv("HISTORY_SIZE", "400"))

import metrics  # noqa: E402
import xcap  # noqa: E402

BIND = os.getenv("PANEL_BIND", "127.0.0.1")
PROJECT = os.getenv("PROJECT_NAME", "TailDeck")
REALM = "TailDeck"

# key -> (label, units, restartable)
# Unit names match the files in systemd/ (user units).
SERVICES = [
    ("hypr", "Hyprland", ["wayvnc.service", "taildeck-ws-hypr.service"], True),
    ("xfce", "XFCE", ["taildeck-xfce.service", "taildeck-ws-xfce.service"], True),
    ("term", "Terminal", ["taildeck-term.service"], True),
]
UNIT_INDEX = {key: (label, units) for key, label, units, _ in SERVICES}
STACK = ["wayvnc.service", "taildeck-xfce.service", "taildeck-ws-hypr.service",
         "taildeck-ws-xfce.service", "taildeck-term.service"]
LOG_UNITS = {key: units[0] for key, _, units, _ in SERVICES}
LOG_UNITS.update({
    "proxy-hypr": "taildeck-ws-hypr.service",
    "proxy-xfce": "taildeck-ws-xfce.service",
    "panel": "taildeck.service",
})

# backend key -> loopback address of the service that owns it
PROXY_BACKENDS = {
    "hypr": (os.getenv("WS_HYPR_HOST", "127.0.0.1"), int(os.getenv("WS_HYPR_PORT", "6101"))),
    "xfce": (os.getenv("WS_XFCE_HOST", "127.0.0.1"), int(os.getenv("WS_XFCE_PORT", "6102"))),
    "term": (os.getenv("TERM_HOST", "127.0.0.1"), int(os.getenv("TERM_PORT", "6103"))),
}
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


def load_password() -> str:
    """Credentials come from PANEL_USER / PANEL_PASSWORD (environment or .env)."""
    global PASSWORD
    PASSWORD = os.environ.get("PANEL_PASSWORD", "").strip()
    return PASSWORD


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def run(cmd, timeout=10, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except Exception:
        return None


def _strip_prefix(path: str) -> str:
    """The panel is mounted at /vnc by tailscale serve, which strips the mount.

    Direct HTTP hits on :6100 keep the prefix, so fold both shapes together.
    """
    if path == "/vnc":
        return "/"
    if path.startswith("/vnc/"):
        return path[4:]
    return path


def proxy_target(path: str):
    """Match a stripped path to a console backend.

    Returns (key, host, port, upstream_path) or None. `/hypr/vnc.html` becomes
    (`hypr`, 127.0.0.1, 6101, `/vnc.html`).
    """
    for key, (host, port) in PROXY_BACKENDS.items():
        prefix = "/" + key
        if path == prefix or path.startswith(prefix + "/"):
            return key, host, port, path[len(prefix):] or "/"
    return None


_backend_auth: dict[str, str] = {}


def backend_auth() -> str:
    """Authorization header for websockify/ttyd, which share our credentials."""
    if "value" not in _backend_auth:
        raw = f"{USER}:{PASSWORD}"
        _backend_auth["value"] = "Basic " + base64.b64encode(raw.encode()).decode()
    return _backend_auth["value"]


def secure_origin() -> str | None:
    """HTTPS origin of the tailscale-serve mount (HTTPS_ORIGIN or MagicDNS name)."""
    explicit = os.getenv("HTTPS_ORIGIN", "").strip().rstrip("/")
    if explicit:
        return explicit
    name = tailnet_name()
    return f"https://{name}" if name else None


_tailnet_name = {"value": None, "at": 0.0}


def tailnet_name() -> str | None:
    """MagicDNS name, needed to send plain-HTTP visitors to the HTTPS mount."""
    now = time.time()
    if _tailnet_name["value"] and now - _tailnet_name["at"] < 300:
        return _tailnet_name["value"]
    name = None
    r = run(["tailscale", "status", "--json"], timeout=8)
    if r and r.returncode == 0:
        try:
            info = json.loads(r.stdout or "{}")
            name = (info.get("Self", {}).get("DNSName") or "").rstrip(".") or None
        except (ValueError, AttributeError):
            name = None
    if not name:
        try:
            name = (HERE / ".tailnet-name").read_text().strip() or None
        except OSError:
            name = None
    _tailnet_name.update(value=name, at=now)
    return name


def unit_states() -> dict:
    """One systemctl call for every unit we care about."""
    units = sorted({u for _, _, us, _ in SERVICES for u in us})
    r = run(["systemctl", "--user", "show", "-p", "Id",
             "-p", "ActiveState", "-p", "UnitFileState", *units], timeout=8)
    out = {}
    if not r or r.returncode != 0:
        return out
    block = {}
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


def wayland_display() -> str | None:
    runtime = Path(f"/run/user/{os.getuid()}")
    for sock in sorted(runtime.glob("wayland-*")):
        if sock.suffix == ".lock":
            continue
        if sock.is_socket():
            return sock.name
    return None


_hypr_sig = {"value": None, "at": 0.0}


def hypr_signature() -> str | None:
    now = time.time()
    if _hypr_sig["value"] and now - _hypr_sig["at"] < 60:
        return _hypr_sig["value"]
    root = Path(f"/run/user/{os.getuid()}/hypr")
    best = None
    for d in root.glob("*"):
        sock = d / ".socket.sock"
        if not sock.exists():
            continue
        try:
            mtime = sock.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, d.name)
    if best:
        _hypr_sig.update(value=best[1], at=now)
        return best[1]
    _hypr_sig.update(value=None, at=now)
    return None


def hypr_monitor(sig: str) -> str | None:
    env = dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE=sig)
    r = run(["hyprctl", "monitors", "-j"], timeout=5, env=env)
    if not r or r.returncode != 0:
        return None
    try:
        monitors = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not monitors:
        return None
    for m in monitors:
        if m.get("focused"):
            return m.get("name")
    return monitors[0].get("name")


# --------------------------------------------------------------------------- #
# thumbnails
# --------------------------------------------------------------------------- #

_thumb_cache: dict[str, tuple[float, bytes | None]] = {}
_thumb_lock = threading.Lock()


def _grim() -> bytes | None:
    sig = hypr_signature()
    if not sig:
        return None
    monitor = hypr_monitor(sig)
    if not monitor:
        return None
    wl = wayland_display()
    if not wl:
        return None
    env = dict(
        os.environ,
        HYPRLAND_INSTANCE_SIGNATURE=sig,
        WAYLAND_DISPLAY=wl,
        XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}",
    )
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        r = run(["grim", "-o", monitor, "-s", "0.35", "-t", "png", path],
                timeout=15, env=env)
        if not r or r.returncode != 0:
            return None
        return Path(path).read_bytes()
    except OSError:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _vnc_frame() -> bytes | None:
    try:
        return xcap.capture_png()
    except Exception:
        return None


def thumbnail(which: str) -> bytes | None:
    if which not in ("hypr", "xfce"):
        return None
    now = time.time()
    with _thumb_lock:
        hit = _thumb_cache.get(which)
        if hit and now - hit[0] < THUMB_TTL:
            return hit[1]
    data = _grim() if which == "hypr" else _vnc_frame()
    with _thumb_lock:
        _thumb_cache[which] = (time.time(), data)
    return data


# --------------------------------------------------------------------------- #
# history (charts)
# --------------------------------------------------------------------------- #

_history: collections.deque = collections.deque(maxlen=HISTORY_SIZE)
_history_lock = threading.Lock()


def cpu_temp() -> float:
    temps = metrics.temperatures()
    cpu_temps = [t["current"] for t in temps
                 if "nvme" not in f"{t.get('chip', '')}{t.get('label', '')}".lower()]
    return max(cpu_temps or [t["current"] for t in temps] or [0.0])


def history_sample() -> dict:
    c = metrics.cpu()
    m = metrics.memory()
    g = metrics.gpu()
    system_procs, user_procs = metrics.top_procs(3)

    procs, seen = [], set()
    for p in system_procs + user_procs:
        name = (p.get("name") or "?").strip()
        if name in seen:
            continue
        seen.add(name)
        procs.append({
            "name": name,
            "cpu": round(p.get("cpu_percent") or 0, 1),
            "mem": round(p.get("memory_percent") or 0, 1),
        })
        if len(procs) >= 6:
            break

    return {
        "t": int(time.time()),
        "cpu": round(c["percent"], 1),
        "ram": round(m["percent"], 1),
        "temp": round(cpu_temp(), 1),
        "gpu": None if not g else round(g["util"]),
        "gpu_temp": None if not g else round(g["temp"]),
        "load": [round(x, 2) for x in c.get("load", ())],
        "procs": procs,
    }


def history_loop():
    while True:
        started = time.time()
        try:
            sample = history_sample()
            with _history_lock:
                _history.append(sample)
        except Exception:
            pass
        time.sleep(max(0.5, HISTORY_INTERVAL - (time.time() - started)))


def history_payload() -> dict:
    with _history_lock:
        samples = list(_history)
    return {"interval": HISTORY_INTERVAL, "samples": samples}


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #

_stats_cache: tuple[float, dict] | None = None
_stats_lock = threading.Lock()


def stats_payload() -> dict:
    global _stats_cache
    now = time.time()
    with _stats_lock:
        if _stats_cache and now - _stats_cache[0] < STATS_TTL:
            return _stats_cache[1]

    c = metrics.cpu()
    m = metrics.memory()
    ds = metrics.disks()
    temp = cpu_temp()
    g = metrics.gpu()
    up = sum(1 for x in metrics.docker_containers() if x["state"] == "running")
    failed = metrics.failed_units()
    batt = metrics.battery()
    ts = metrics.tailscale()
    system_procs, user_procs = metrics.top_procs(3)

    full = [d for d in ds if d["percent"] == max((x["percent"] for x in ds), default=0)]
    root = next((d for d in full if d["mount"] == "/"), full[0] if full else None)

    payload = {
        "cpu": round(c["percent"], 1),
        "cores": c.get("physical") or c.get("cores"),
        "freq": c.get("freq_mhz"),
        "temp": round(temp, 1),
        "ram": round(m["percent"], 1),
        "ram_used": m["used"],
        "ram_total": m["total"],
        "swap": round(m["swap_percent"], 1),
        "swap_used": m["swap_used"],
        "swap_total": m["swap_total"],
        "disk": round(root["percent"], 1) if root else 0.0,
        "disk_mount": root["mount"] if root else None,
        "disk_used": root["used"] if root else 0,
        "disk_total": root["total"] if root else 0,
        "containers": up,
        "gpu": None if not g else {
            "name": g["name"],
            "util": round(g["util"]),
            "temp": round(g["temp"]),
            "used": g["mem_used"],
            "total": g["mem_total"],
        },
        "battery": None if not batt else {
            "percent": batt["percent"],
            "plugged": batt["plugged"],
        },
        "uptime": metrics.uptime_secs(),
        "failed_units": len(failed),
        "failed_list": failed,
        "tailscale": None if not ts else {
            "online": ts["backend"] == "Running",
            "backend": ts["backend"],
            "ip": ts["ip"],
            "name": ts["name"],
            "dns": tailnet_name(),
        },
        "procs": [
            {"name": p["name"], "cpu": round(p["cpu_percent"], 1),
             "mem": round(p["memory_percent"], 1)}
            for p in (system_procs + user_procs)
        ][:5],
        "load": [round(x, 2) for x in c.get("load", ())],
    }
    with _stats_lock:
        _stats_cache = (time.time(), payload)
    return payload


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #

def list_files() -> list[dict]:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for p in FILES_DIR.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            st = p.stat()
            rows.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    except OSError:
        return []
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[:100]


def safe_path(name: str) -> Path | None:
    clean = Path(name).name
    if not clean or clean in (".", "..") or clean.startswith("."):
        return None
    return FILES_DIR / clean


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "taildeck"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------- #

    def log_message(self, fmt, *args):  # quieter journal
        if args and str(args[1] if len(args) > 1 else "").startswith("4"):
            sys.stderr.write("panel: %s\n" % (fmt % args))

    def _authorised(self) -> bool:
        if not PASSWORD:
            self._text(500, "panel has no password configured\n")
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="%s"' % REALM)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        try:
            raw = base64.b64decode(header[6:], validate=True).decode()
        except Exception:
            raw = ""
        user, _, pw = raw.partition(":")
        ok = (hmac.compare_digest(user.encode(), USER.encode())
              & hmac.compare_digest(pw.encode(), PASSWORD.encode()))
        if not ok:
            time.sleep(1)  # slow down password guessing
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="%s"' % REALM)
            self.send_header("Content-Length", "0")
            self.end_headers()
        return ok

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return urllib.parse.urlsplit(origin).netloc == host and bool(host)

    def _text(self, code: int, body: str, ctype="text/plain; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._text(code, json.dumps(obj), "application/json")

    def _bytes(self, data: bytes, ctype: str, code=200, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- routing ----------------------------------------------------------- #

    def do_GET(self):
        if not self._authorised():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = _strip_prefix(parsed.path.rstrip("/") or "/")
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            return self._static("index.html", "text/html; charset=utf-8")
        route = proxy_target(path)
        if route:
            if not self._secure():
                return self._to_https()
            return self._proxy(route[0], route[3])
        if path in ("/style.css", "/app.js", "/manifest.webmanifest", "/icon.svg"):
            ctype = {
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".webmanifest": "application/manifest+json",
                ".svg": "image/svg+xml",
            }[Path(path).suffix]
            return self._static(path.lstrip("/"), ctype)

        if path == "/api/stats":
            return self._json(stats_payload())
        if path == "/api/history":
            return self._json(history_payload())
        if path == "/api/config":
            return self._json(self.config_payload())
        if path == "/api/units":
            states = unit_states()
            rows = []
            for key, label, units, restartable in SERVICES:
                st = [states.get(u, {}) for u in units]
                active = all(s.get("active") for s in st) if st else False
                rows.append({
                    "key": key, "label": label,
                    "active": active,
                    "units": [{"unit": u, "state": states.get(u, {}).get("state", "unknown")}
                              for u in units],
                    "restartable": restartable,
                })
            return self._json({"services": rows, "host": self.headers.get("Host", "")})
        if path == "/api/thumb":
            which = (query.get("which") or [""])[0]
            data = thumbnail(which)
            if not data:
                return self._text(503, "thumbnail unavailable\n")
            return self._bytes(data, "image/png")
        if path == "/api/logs":
            return self._stream_logs((query.get("unit") or [""])[0])
        if path == "/api/files":
            return self._json({"dir": str(FILES_DIR), "files": list_files()})
        if path == "/api/files/get":
            name = (query.get("name") or [""])[0]
            target = safe_path(name)
            if not target or not target.is_file():
                return self._text(404, "not found\n")
            return self._download(target)
        return self._text(404, "not found\n")

    def do_POST(self):
        if not self._authorised():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = _strip_prefix(parsed.path.rstrip("/") or "/")
        query = urllib.parse.parse_qs(parsed.query)

        route = proxy_target(path)
        if route:
            if not self._secure():
                return self._to_https()
            return self._proxy(route[0], route[3])

        if self.headers.get("X-Panel") != "1" or not self._same_origin():
            return self._text(403, "missing X-Panel header\n")

        if path == "/api/action":
            return self._action()
        if path == "/api/files":
            return self._upload((query.get("name") or [""])[0])
        return self._text(404, "not found\n")

    # -- handlers ---------------------------------------------------------- #

    # -- configuration endpoint ---------------------------------------------- #

    def config_payload(self) -> dict:
        return {
            "project": PROJECT,
            "history_interval": HISTORY_INTERVAL,
            "history_size": HISTORY_SIZE,
            "max_upload": MAX_UPLOAD,
            "secure_mount": secure_origin(),
        }

    def _secure(self) -> bool:
        """True when the request arrived over HTTPS.

        tailscale serve terminates TLS and may pass X-Forwarded-Proto; when it
        doesn't, the MagicDNS name (no explicit port, not an IP) is the signal.
        Direct hits on http://<tailnet-ip>:6100 are never secure.
        """
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if proto:
            return proto == "https"
        host = self.headers.get("Host", "")
        if host.endswith(":443"):
            return True
        name, sep, port = host.rpartition(":")
        if sep and port.isdigit():
            return False
        if not name:
            name = host
        if name in ("localhost", ""):
            return False
        try:
            ipaddress.ip_address(name.strip("[]"))
            return False
        except ValueError:
            return True

    def _proxy(self, backend: str, upstream_path: str):
        host, port = PROXY_BACKENDS[backend]
        parsed = urllib.parse.urlsplit(self.path)
        target = upstream_path + (("?" + parsed.query) if parsed.query else "")
        if self.headers.get("Upgrade", "").lower() == "websocket":
            if not self._same_origin():
                return self._text(403, "cross-origin websocket refused\n")
            return self._proxy_ws(host, port, target)
        return self._proxy_http(host, port, target)

    def _proxy_http(self, host: str, port: int, target: str):
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            body = self.rfile.read(length)
        headers = {"Authorization": backend_auth(), "Connection": "close"}
        for key in ("Content-Type", "Accept", "User-Agent", "Range",
                    "If-None-Match", "If-Modified-Since"):
            value = self.headers.get(key)
            if value:
                headers[key] = value
        conn = http.client.HTTPConnection(host, port, timeout=30)
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException):
            conn.close()
            return self._text(502, "console backend unavailable\n")

        self.close_connection = True
        self.send_response_only(resp.status, resp.reason)
        has_length = resp.getheader("Content-Length") is not None
        for key, value in resp.getheaders():
            if key.lower() in HOP_HEADERS or key.lower() == "connection":
                continue
            self.send_header(key, value)
        if not has_length:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            self.wfile.flush()
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()

    def _proxy_ws(self, host: str, port: int, target: str):
        key = self.headers.get("Sec-WebSocket-Key", "")
        version = self.headers.get("Sec-WebSocket-Version", "13")
        protocols = self.headers.get("Sec-WebSocket-Protocol")
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError:
            return self._text(502, "console backend unavailable\n")
        upstream.settimeout(None)

        lines = [
            f"GET {target} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            f"Sec-WebSocket-Version: {version}",
            f"Authorization: {backend_auth()}",
        ]
        if protocols:
            lines.append(f"Sec-WebSocket-Protocol: {protocols}")
        origin = self.headers.get("Origin")
        if origin:
            lines.append(f"Origin: {origin}")
        try:
            upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = upstream.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            upstream.close()
            return self._text(502, "websocket handshake failed\n")

        head, _, rest = buf.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1") if head else ""
        parts = status_line.split(" ", 2)
        code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 502
        if code != 101:
            upstream.close()
            return self._text(502, "websocket refused\n")

        self.close_connection = True
        self.send_response_only(101, "Switching Protocols")
        keep = {b"upgrade", b"connection", b"sec-websocket-accept",
                b"sec-websocket-protocol", b"sec-websocket-extensions"}
        for line in head.split(b"\r\n")[1:]:
            if b":" not in line:
                continue
            name, _, value = line.partition(b":")
            if name.strip().lower() in keep:
                self.send_header(name.strip().decode("latin-1"),
                                 value.strip().decode("latin-1"))
        self.end_headers()
        if rest:
            try:
                self.wfile.write(rest)
                self.wfile.flush()
            except OSError:
                upstream.close()
                return

        def pump_up():
            try:
                while True:
                    data = self.rfile.read1(65536)
                    if not data:
                        break
                    upstream.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    upstream.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        def pump_down():
            try:
                while True:
                    data = upstream.recv(65536)
                    if not data:
                        break
                    self.wfile.write(data)
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                try:
                    self.connection.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        sender = threading.Thread(target=pump_up, daemon=True)
        sender.start()
        pump_down()
        sender.join(timeout=5)
        upstream.close()

    def _static(self, name: str, ctype: str):
        target = HERE / name
        try:
            data = target.read_bytes()
        except OSError:
            return self._text(404, "not found\n")
        if name == "index.html":
            html = data.decode()
            html = html.replace("__HTTPS_ORIGIN__", html_escape(secure_origin() or ""))
            html = html.replace("__PROJECT__", html_escape(PROJECT))
            data = html.encode()
        self._bytes(data, ctype)

    def _to_https(self):
        """Reach the console through the HTTPS mount, the only one it can use."""
        origin = secure_origin()
        if not origin:
            return self._text(503, "tailnet name unknown\n")
        parsed = urllib.parse.urlsplit(self.path)
        location = f"{origin}/vnc{_strip_prefix(parsed.path)}"
        if parsed.query:
            location += "?" + parsed.query
        self.send_response(308)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _action(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            return self._text(413, "payload too large\n")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return self._text(400, "bad json\n")
        target = str(body.get("target") or "")
        if target == "vnc-stack":
            units = STACK
        elif target in UNIT_INDEX:
            units = UNIT_INDEX[target][1]
        else:
            return self._text(400, "unknown target\n")
        threading.Thread(target=self._restart, args=(units,), daemon=True).start()
        return self._json({"ok": True, "restarted": units})

    def _restart(self, units: list[str]):
        for unit in units:
            run(["systemctl", "--user", "restart", unit], timeout=60)
            time.sleep(1)

    def _download(self, target: Path):
        try:
            size = target.stat().st_size
            fh = open(target, "rb")
        except OSError:
            return self._text(500, "read failed\n")
        quoted = urllib.parse.quote(target.name)
        ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", target.name)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}")
        self.end_headers()
        try:
            with fh:
                shutil.copyfileobj(fh, self.wfile, 65536)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _upload(self, name: str):
        target = safe_path(name)
        if not target:
            return self._text(400, "bad name\n")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._text(400, "empty body\n")
        if length > MAX_UPLOAD:
            return self._text(413, "file too large\n")
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        remaining = length
        try:
            with open(tmp, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise OSError("short read")
                    fh.write(chunk)
                    remaining -= len(chunk)
            os.replace(tmp, target)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return self._text(500, "write failed\n")
        return self._json({"ok": True, "name": target.name, "size": length})

    def _stream_logs(self, unit_key: str):
        unit = LOG_UNITS.get(unit_key)
        if not unit:
            return self._text(400, "unknown unit\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        env = dict(os.environ,
                   XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}",
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{os.getuid()}/bus")
        try:
            proc = subprocess.Popen(
                ["journalctl", "--user", "-u", unit, "-n", "150", "-f",
                 "-o", "short-iso", "--no-pager"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env)
        except OSError:
            try:
                self.wfile.write(b"data: could not start journalctl\n\n")
            except OSError:
                pass
            return
        deadline = time.time() + 1800
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                try:
                    self.wfile.write(f"data: {line.rstrip()}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                if time.time() > deadline:
                    break
        except Exception:
            pass
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def resolve_bind() -> str:
    """argv[1] or PANEL_BIND; the word "tailscale" means this node's tailnet IPv4."""
    bind = sys.argv[1] if len(sys.argv) > 1 else BIND
    if bind == "tailscale":
        r = run(["tailscale", "ip", "-4"], timeout=8)
        ip = (r.stdout.split() or [""])[0] if r and r.returncode == 0 else ""
        if not ip:
            sys.exit("taildeck: PANEL_BIND=tailscale but Tailscale has no IPv4 yet")
        return ip
    return bind


def main():
    load_password()
    if not PASSWORD or PASSWORD in ("CHANGE_ME", "YOUR_PASSWORD"):
        sys.exit("taildeck: set a real PANEL_PASSWORD in .env (see .env.example)")
    name = tailnet_name()
    if name:
        try:
            (HERE / ".tailnet-name").write_text(name + "\n")
        except OSError:
            pass
    bind = resolve_bind()
    server = ThreadingHTTPServer((bind, PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=history_loop, name="history", daemon=True).start()
    print(f"taildeck: listening on {bind}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
