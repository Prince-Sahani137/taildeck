"""System metrics for TailDeck (psutil + a few optional CLI tools).

Every function degrades gracefully: a missing tool (nvidia-smi, docker,
tailscale, a battery...) yields an empty/None result instead of an exception.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import psutil

_SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "efivarfs", "ramfs",
            "proc", "sysfs", "cgroup2", "autofs", "fuse.portal"}


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# --------------------------------------------------------------------- cpu --
psutil.cpu_percent(None)  # prime the counter


def cpu() -> dict:
    freq = psutil.cpu_freq()
    try:
        load = os.getloadavg()
    except OSError:
        load = ()
    return {
        "percent": psutil.cpu_percent(None),
        "cores": psutil.cpu_count(logical=True),
        "physical": psutil.cpu_count(logical=False),
        "freq_mhz": round(freq.current) if freq else None,
        "load": load,
    }


def memory() -> dict:
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    return {"percent": vm.percent, "used": vm.used, "total": vm.total,
            "swap_percent": sw.percent, "swap_used": sw.used,
            "swap_total": sw.total}


def disks() -> list[dict]:
    out, seen = [], set()
    for part in psutil.disk_partitions(all=False):
        if part.fstype in _SKIP_FS or part.mountpoint in seen:
            continue
        if part.mountpoint.startswith(("/snap", "/boot/efi", "/run")):
            continue
        seen.add(part.mountpoint)
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        out.append({"mount": part.mountpoint, "percent": u.percent,
                    "used": u.used, "total": u.total})
    return out


def temperatures() -> list[dict]:
    try:
        raw = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return []
    out = []
    for chip, entries in raw.items():
        for e in entries:
            if e.current:
                out.append({"chip": chip, "label": e.label or "",
                            "current": float(e.current)})
    return out


def gpu() -> dict | None:
    """First NVIDIA GPU via nvidia-smi; memory figures are in MB."""
    out = _run(["nvidia-smi",
                "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"], timeout=4)
    if not out:
        return None
    try:
        name, util, temp, used, total = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"name": name, "util": float(util), "temp": float(temp),
                "mem_used": float(used), "mem_total": float(total)}
    except (ValueError, IndexError):
        return None


def battery() -> dict | None:
    try:
        b = psutil.sensors_battery()
    except (AttributeError, OSError):
        return None
    if not b:
        return None
    return {"percent": round(b.percent), "plugged": bool(b.power_plugged)}


def uptime_secs() -> int:
    return int(time.time() - psutil.boot_time())


# ---------------------------------------------------------------- services --

def docker_containers() -> list[dict]:
    out = _run(["docker", "ps", "-a", "--format", "{{.State}}"], timeout=4)
    return [{"state": s.strip()} for s in (out or "").splitlines() if s.strip()]


def failed_units() -> list[str]:
    names: list[str] = []
    for cmd in (["systemctl", "--failed", "--no-legend", "--plain"],
                ["systemctl", "--user", "--failed", "--no-legend", "--plain"]):
        out = _run(cmd, timeout=5)
        for line in (out or "").splitlines():
            parts = line.split()
            if parts:
                names.append(parts[0])
    return sorted(set(names))


def tailscale() -> dict | None:
    out = _run(["tailscale", "status", "--json"], timeout=6)
    if not out:
        return None
    try:
        info = json.loads(out)
    except ValueError:
        return None
    me = info.get("Self") or {}
    ips = info.get("TailscaleIPs") or me.get("TailscaleIPs") or []
    ip4 = next((i for i in ips if "." in i), None)
    return {"backend": info.get("BackendState", "Unknown"), "ip": ip4,
            "name": (me.get("DNSName") or me.get("HostName") or "").rstrip(".")}


# --------------------------------------------------------------- processes --
_procs: dict[int, psutil.Process] = {}


def top_procs(n: int = 3) -> tuple[list[dict], list[dict]]:
    """Return (system_procs, user_procs): top *n* by CPU each, root vs others."""
    system, user = [], []
    alive = set()
    for p in psutil.process_iter(["pid", "name", "uids", "memory_percent"]):
        try:
            pid = p.info["pid"]
            alive.add(pid)
            proc = _procs.setdefault(pid, p)
            cpu_pct = proc.cpu_percent(None)
            row = {"name": p.info["name"] or "?", "cpu_percent": cpu_pct,
                   "memory_percent": p.info["memory_percent"] or 0.0}
            uids = p.info["uids"]
            (system if uids and uids.real == 0 else user).append(row)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for pid in set(_procs) - alive:
        _procs.pop(pid, None)
    key = lambda r: r["cpu_percent"]
    return (sorted(system, key=key, reverse=True)[:n],
            sorted(user, key=key, reverse=True)[:n])
