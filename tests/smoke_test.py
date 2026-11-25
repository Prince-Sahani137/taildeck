#!/usr/bin/env python3
"""End-to-end smoke test: starts TailDeck plus fake console backends and
checks auth, CSRF/origin guards, API, files, HTTP + WebSocket proxying.

    python3 tests/smoke_test.py
"""
import base64, hashlib, http.client, json, os, socket, subprocess, sys
import tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL, B_HYPR = 6190, 6191
USER, PW = "tester", "s3cret-pass"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.headers.get("Authorization") != AUTH:
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.headers.get("Upgrade", "").lower() == "websocket":
            acc = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + GUID).encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket"); self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", acc); self.end_headers()
            self.wfile.flush()
            while True:  # echo raw bytes
                d = self.request.recv(4096)
                if not d: break
                self.request.sendall(d)
            self.close_connection = True
            return
        body = b"fake novnc " + self.path.encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


def req(method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PANEL, timeout=15)
    h = {"Host": f"127.0.0.1:{PANEL}", "X-Forwarded-Proto": "https"}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    return r.status, dict(r.getheaders()), data


def ws(path, origin):
    s = socket.create_connection(("127.0.0.1", PANEL), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [f"GET {path} HTTP/1.1", f"Host: 127.0.0.1:{PANEL}", "Upgrade: websocket",
             "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
             f"Authorization: {AUTH}", "X-Forwarded-Proto: https"]
    if origin: lines.append(f"Origin: {origin}")
    s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(4096)
        if not d: break
        buf += d
    status = int(buf.split(b" ", 2)[1]) if buf else 0
    echo = None
    if status == 101:
        s.sendall(b"hello-bytes")
        echo = s.recv(64)
    s.close()
    return status, echo


def main():
    files = Path(tempfile.mkdtemp())
    srv = ThreadingHTTPServer(("127.0.0.1", B_HYPR), Backend)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = dict(os.environ, PANEL_PORT=str(PANEL), PANEL_USER=USER, PANEL_PASSWORD=PW,
               WS_HYPR_PORT=str(B_HYPR), FILES_DIR=str(files), PANEL_BIND="127.0.0.1",
               HTTPS_ORIGIN="https://example.ts.net", PROJECT_NAME="TailDeck")
    proc = subprocess.Popen([sys.executable, str(ROOT / "taildeck.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(50):
            try: socket.create_connection(("127.0.0.1", PANEL), timeout=0.3).close(); break
            except OSError: time.sleep(0.2)
        A = {"Authorization": AUTH}
        st, _, _ = req("GET", "/"); check("no creds -> 401", st == 401)
        st, _, _ = req("GET", "/", {"Authorization": "Basic " + base64.b64encode(b"x:y").decode()}); check("bad creds -> 401", st == 401)
        st, h, d = req("GET", "/", A); html = d.decode()
        check("index served", st == 200 and "TailDeck" in html)
        check("origin injected into page", 'content="https://example.ts.net"' in html and "__HTTPS_ORIGIN__" not in html)
        for f, ct in (("style.css", "text/css"), ("app.js", "javascript"), ("manifest.webmanifest", "manifest"), ("icon.svg", "svg")):
            st, h, _ = req("GET", "/" + f, A); check(f"static {f}", st == 200 and ct in h.get("Content-Type", ""))
        st, _, d = req("GET", "/vnc/", A); check("/vnc/ prefix handled", st == 200)
        st, _, d = req("GET", "/api/stats", A); j = json.loads(d)
        check("api/stats", st == 200 and all(k in j for k in ("cpu", "ram", "disk", "procs", "uptime")), str(list(j)))
        st, _, d = req("GET", "/api/history", A); check("api/history", st == 200 and "samples" in json.loads(d))
        st, _, d = req("GET", "/api/units", A); check("api/units", st == 200 and len(json.loads(d)["services"]) == 3)
        st, _, d = req("GET", "/api/config", A); c = json.loads(d)
        check("api/config has no personal data", st == 200 and "user" not in c and "tailnet_ip" not in c and c["secure_mount"] == "https://example.ts.net")
        st, _, _ = req("GET", "/api/thumb?which=xfce", A); check("thumb degrades to 503 (no X)", st in (200, 503))
        st, _, _ = req("GET", "/api/logs?unit=nope", A); check("logs unknown unit -> 400", st == 400)
        # files
        st, _, _ = req("POST", "/api/files?name=a.txt", {**A}, b"data"); check("upload without X-Panel -> 403", st == 403)
        st, _, _ = req("POST", "/api/files?name=a.txt", {**A, "X-Panel": "1", "Origin": "https://evil.example"}, b"data"); check("upload cross-origin -> 403", st == 403)
        st, _, d = req("POST", "/api/files?name=a.txt", {**A, "X-Panel": "1"}, b"hello world"); check("upload ok", st == 200 and (files / "a.txt").read_bytes() == b"hello world", d.decode())
        st, _, d = req("POST", "/api/files?name=../../etc/evil", {**A, "X-Panel": "1"}, b"x"); check("traversal neutralised", not Path("/etc/evil").exists() and (st != 200 or (files / "evil").exists()))
        st, _, d = req("POST", "/api/files?name=.hidden", {**A, "X-Panel": "1"}, b"x"); check("hidden name rejected", st == 400)
        st, _, d = req("GET", "/api/files", A); check("list files", any(f["name"] == "a.txt" for f in json.loads(d)["files"]))
        st, h, d = req("GET", "/api/files/get?name=a.txt", A); check("download", st == 200 and d == b"hello world" and "attachment" in h.get("Content-Disposition", ""))
        st, h, d = req("GET", '/api/files/get?name=..%2F..%2Fetc%2Fpasswd', A); check("download traversal blocked", st == 404)
        st, _, d = req("POST", "/api/action", {**A, "X-Panel": "1"}, b'{"target":"bogus"}'); check("action unknown target -> 400", st == 400)
        # proxy
        st, _, d = req("GET", "/hypr/vnc.html?x=1", A); check("http proxy + backend auth injected", st == 200 and d == b"fake novnc /vnc.html?x=1", f"{st} {d[:60]}")
        st, _, _ = req("GET", "/hypr/vnc.html", {**A, "X-Forwarded-Proto": "http"}); check("plain http console -> 308", st == 308)
        st, _, _ = req("GET", "/xfce/vnc.html", A); check("dead backend -> 502", st == 502)
        # websocket
        s1, e1 = ws("/hypr/websockify", "https://127.0.0.1:%d" % PANEL if False else f"http://127.0.0.1:{PANEL}")
        check("websocket same-origin proxied + echoed", s1 == 101 and e1 == b"hello-bytes", f"{s1} {e1}")
        s2, _ = ws("/hypr/websockify", "https://evil.example"); check("websocket cross-origin -> 403", s2 == 403, str(s2))
    finally:
        proc.terminate()
        try: out = proc.communicate(timeout=5)[0]
        except Exception: out = ""
        if fails and out: print("--- server output ---\n" + out)
    print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
    sys.exit(1 if fails else 0)


main()
