#!/usr/bin/env python3
"""Unit tests for the service manager, Windows portability helpers and the
workspace-aware page renderer. Cross-platform; needs only psutil.

    python3 tests/test_services.py
"""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PANEL_PASSWORD", "unit-test-password")
os.environ["SERVICE_MANAGER"] = "process"   # never touch systemctl in unit tests

import psutil  # noqa: E402
import services  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def wait(pred, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def logs(mgr, unit):
    return "\n".join(mgr._rt[unit].buffer.tail(500)[0])


def spec(sid, code, **kw):
    return services.ServiceSpec(id=sid, label=sid, cmd=["python", "-c", code], **kw)


SLEEPER = "import os,time;print('pid=%d'%os.getpid(),flush=True);time.sleep(600)"


def test_lifecycle():
    mgr = services.ProcessManager([spec("svc", SLEEPER, workspace="term")])
    mgr.start()
    try:
        check("service becomes active", wait(lambda: mgr.states(["svc"])["svc"]["active"]))
        check("stdout is captured", wait(lambda: "pid=" in logs(mgr, "svc")), logs(mgr, "svc"))
        pid1 = mgr._rt["svc"].popen.pid
        mgr.restart(["svc"])
        check("restart gives a new process",
              wait(lambda: mgr._rt["svc"].popen and mgr._rt["svc"].popen.pid != pid1))
        check("old process is gone after restart", not psutil.pid_exists(pid1)
              or psutil.Process(pid1).status() == psutil.STATUS_ZOMBIE)
        check("state reports enabled", mgr.states(["svc"])["svc"]["enabled"] is True)
    finally:
        pid = mgr._rt["svc"].popen.pid if mgr._rt["svc"].popen else None
        mgr.stop()
    check("stop terminates the child", pid is None or wait(
        lambda: not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE))


def test_tree_kill():
    code = ("import subprocess,sys,time;"
            "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(600)']);"
            "print('child=%d'%c.pid,flush=True);time.sleep(600)")
    mgr = services.ProcessManager([spec("tree", code)])
    mgr.start()
    try:
        pat = re.compile(r"\schild=(\d+)\s*$", re.M)
        check("child pid logged", wait(lambda: pat.search(logs(mgr, "tree")) is not None))
        child = int(pat.search(logs(mgr, "tree")).group(1))
    finally:
        mgr.stop()
    check("grandchild killed with the tree", wait(
        lambda: not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE))


def test_crash_backoff_and_giveup():
    class Fast(services.ProcessManager):
        BACKOFF_BASE = 0.02
        MAX_FAILS = 3
    mgr = Fast([spec("boom", "import sys;print('bye');sys.exit(3)")])
    mgr.start()
    try:
        check("crash loop ends in failed", wait(lambda: mgr.states(["boom"])["boom"]["state"] == "failed"))
        text = logs(mgr, "boom")
        check("exit code is logged", "exited with code 3" in text, text)
        check("gives up after repeated crashes", "giving up" in text, text)
        check("failed_units lists it", mgr.failed_units() == ["boom"])
        mgr.restart(["boom"])
        check("manual restart revives a failed service", "restart requested" in logs(mgr, "boom"))
    finally:
        mgr.stop()


def test_missing_executable():
    mgr = services.ProcessManager([services.ServiceSpec(id="nope", label="nope", cmd=["definitely-not-installed-xyz"])])
    mgr.start()
    try:
        check("missing exe reported", wait(lambda: "executable not found" in logs(mgr, "nope")))
        check("missing exe is failed", mgr.states(["nope"])["nope"]["state"] == "failed")
    finally:
        mgr.stop()


def test_redaction_and_expansion():
    os.environ["PANEL_PASSWORD"] = "hunter2-hunter2"
    mgr = services.ProcessManager([spec("sec", "import sys;print(sys.argv[1]);import time;time.sleep(2)")])
    mgr._rt["sec"].spec.cmd = ["python", "-c", "import sys;print(sys.argv[1]);import time;time.sleep(2)", "user:${PANEL_PASSWORD}"]
    mgr.start()
    try:
        check("output is logged", wait(lambda: "user:" in logs(mgr, "sec")))
        text = logs(mgr, "sec")
        check("password never appears in logs", "hunter2" not in text and "****" in text, text)
    finally:
        mgr.stop()


def test_log_follow():
    mgr = services.ProcessManager([spec("f", "import time\nfor i in range(50):\n print('line',i,flush=True);time.sleep(0.1)")])
    mgr.start()
    try:
        gen = mgr.log_lines("f")
        got = []
        end = time.time() + 10
        for line in gen:
            if line:
                got.append(line)
            if len(got) >= 3 or time.time() > end:
                break
        gen.close()
        check("log_lines streams new lines", len(got) >= 3, str(got))
        check("unknown unit yields a message", next(mgr.log_lines("zzz")).startswith("no such service"))
    finally:
        mgr.stop()


def test_reap_stale():
    dead_parent = {"pid": 99999999, "created": 0}
    sleeper = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(600)"])
    bystander = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(600)"])
    live = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(600)"])
    try:
        with tempfile.TemporaryDirectory() as d:
            pf = Path(d) / "pids.json"
            me = psutil.Process(os.getpid())
            # 1) the recording TailDeck is still alive: leave its children alone
            pf.write_text(json.dumps({"parent": {"pid": me.pid, "created": me.create_time()},
                                      "children": [{"pid": live.pid, "created": psutil.Process(live.pid).create_time()}]}))
            check("children of a live parent are not reaped", services.ProcessManager([], pf).reap_stale() == 0 and live.poll() is None)
            # 2) the parent is gone: reap, but never a recycled PID
            pf.write_text(json.dumps({"parent": dead_parent, "children": [
                {"pid": sleeper.pid, "created": psutil.Process(sleeper.pid).create_time()},
                {"pid": bystander.pid, "created": psutil.Process(bystander.pid).create_time() - 1000}]}))
            n = services.ProcessManager([], pf).reap_stale()
            check("stale child from a dead run is killed", n == 1 and wait(lambda: sleeper.poll() is not None), str(n))
            check("recycled PID is not touched", bystander.poll() is None)
            check("pid file is reset", json.loads(pf.read_text()) == {})
            # 3) a running manager records itself and its child
            mgr = services.ProcessManager([spec("rec", SLEEPER)], pf)
            mgr.start()
            try:
                check("manager records its children", wait(lambda: len(json.loads(pf.read_text() or "{}").get("children", [])) == 1))
                check("manager records its own pid", json.loads(pf.read_text())["parent"]["pid"] == os.getpid())
            finally:
                mgr.stop()
    finally:
        for p in (sleeper, bystander, live):
            if p.poll() is None:
                p.kill()
            p.wait()


def test_specs_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "services.json"
        check("missing file -> None", services.load_specs(p) is None)
        p.write_text('{"services":[{"id":"a","cmd":["x","y"],"workspace":"term","role":"proxy"}]}', encoding="utf-8-sig")
        s = services.load_specs(p)
        check("valid file parsed (BOM tolerated)", s and s[0].id == "a" and s[0].role == "proxy")
        for bad, why in (('{"services":[{"id":"a b","cmd":["x"]}]}', "bad id"),
                         ('{"services":[{"id":"a","cmd":[]}]}', "empty cmd"),
                         ('{"services":[{"id":"a","cmd":["x"]},{"id":"a","cmd":["x"]}]}', "duplicate"),
                         ("not json", "invalid json")):
            p.write_text(bad)
            try:
                services.load_specs(p)
                check(f"rejects {why}", False)
            except ValueError:
                check(f"rejects {why}", True)


def test_default_specs():
    os.environ.update(WS_XFCE_PORT="6102", VNC_XFCE_PORT="5900", TERM_PORT="6103")
    specs = services.default_specs(["xfce", "term"], ROOT)
    ids = [s.id for s in specs]
    check("default services for xfce+term", ids == ["taildeck-ws-xfce", "taildeck-term"], str(ids))
    ws = specs[0]
    check("websockify module used via python", ws.cmd[:3] == ["python", "-m", "websockify"])
    check("auth + ports use ${VAR} placeholders",
          "${PANEL_USER}:${PANEL_PASSWORD}" in ws.cmd and "127.0.0.1:${WS_XFCE_PORT}" in ws.cmd)
    check("ttyd runs writable on loopback", specs[1].cmd[1:5] == ["-W", "-i", "127.0.0.1", "-p"])


def test_windows_helpers():
    import metrics
    metrics.IS_WINDOWS = True
    try:
        check("SYSTEM is a system process", metrics._is_system({"username": "NT AUTHORITY\\SYSTEM"}))
        check("domain user is a user process", not metrics._is_system({"username": "PC\\someuser"}))
        check("unreadable owner counts as system", metrics._is_system({"username": None}))
        try:
            metrics.top_procs(2)
            ok = True
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(exc)
        check("top_procs works on the Windows code path", ok)
    finally:
        metrics.IS_WINDOWS = sys.platform == "win32"
    check("cpu() survives a missing getloadavg", isinstance(metrics.cpu()["load"], tuple))
    check("tailscale_bin() returns a command", bool(metrics.tailscale_bin()))


def _in_subprocess(code, **env):
    e = dict(os.environ, PANEL_PASSWORD="unit-test-password", SERVICE_MANAGER="process")
    e.update(env)
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=e,
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def test_safe_path_and_render():
    code = ("import taildeck as t, json\n"
            "res = {}\n"
            "for n in ['report.pdf','CON','nul.txt','a:b','x.','x ','a*b','.hidden','..','a\\\\b','LPT1.log','ok name (1).png']:\n"
            "    res[n] = bool(t.safe_path(n, for_write=True))\n"
            "print(json.dumps(res))")
    rc, out, err = _in_subprocess(code)
    res = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("safe_path accepts normal names", res.get("report.pdf") and res.get("ok name (1).png"), err[-300:])
    for bad in ("CON", "nul.txt", "a:b", "x.", "x ", "a*b", ".hidden", "..", "a\\b", "LPT1.log"):
        check(f"safe_path(write) rejects {bad!r}", res.get(bad) is False)
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    code = "import taildeck as t, sys; sys.stdout.write(t.render_index(sys.stdin.read()))"
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), input=html, capture_output=True, text=True,
                       env=dict(os.environ, PANEL_PASSWORD="x", SERVICE_MANAGER="process",
                                WORKSPACES="xfce,term", WS_XFCE_LABEL="My PC"), timeout=60)
    page = r.stdout
    check("disabled workspace is removed from the page",
          'data-view="hypr"' not in page and 'href="#/hypr"' not in page and 'value="proxy-hypr"' not in page, r.stderr[-300:])
    check("enabled workspaces stay", 'data-view="xfce"' in page and 'data-view="term"' in page)
    check("custom label is applied", "My PC" in page and "__WS_" not in page)
    check("workspace config is injected", 'name="taildeck-workspaces"' in page and "&quot;key&quot;: &quot;xfce&quot;" in page)
    r = subprocess.run([sys.executable, "-c", "import taildeck"], cwd=str(ROOT), capture_output=True, text=True,
                       env=dict(os.environ, PANEL_PASSWORD="x", SERVICE_MANAGER="process", WORKSPACES="bogus"), timeout=60)
    check("unknown workspace name is a clear error", r.returncode != 0 and "unknown workspace" in r.stderr)


def main():
    for t in (test_lifecycle, test_tree_kill, test_crash_backoff_and_giveup, test_missing_executable, test_reap_stale,
              test_redaction_and_expansion, test_log_follow, test_specs_file, test_default_specs,
              test_windows_helpers, test_safe_path_and_render):
        print(f"-- {t.__name__}")
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            check(f"{t.__name__} raised {exc!r}", False)
    print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
    sys.exit(1 if fails else 0)


main()
