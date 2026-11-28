"""Screenshot of the desktop shown in the "desktop" tab, as PNG bytes.

* Linux: the virtual X11 (XFCE/TigerVNC) desktop via scrot, maim or ImageMagick's
  `import`. Configure with XFCE_DISPLAY (default :2) and XFCE_XAUTHORITY.
* Windows / macOS: the interactive desktop via Pillow (`pip install Pillow`).
  This only works while a user is logged in and the screen is unlocked, so run
  TailDeck as a logon task, not as a Windows service.

Returns None when no capture method is available (the UI shows "no preview").
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _capture_pillow() -> bytes | None:
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    try:
        img = ImageGrab.grab()
        scale = float(os.getenv("THUMB_SCALE", "0.35"))
        img = img.convert("RGB")
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:  # locked screen, no display, permissions
        return None


def capture_png() -> bytes | None:
    if sys.platform in ("win32", "darwin"):
        return _capture_pillow()
    display = os.getenv("XFCE_DISPLAY", ":2")
    env = dict(os.environ, DISPLAY=display,
               XAUTHORITY=os.getenv("XFCE_XAUTHORITY", str(Path.home() / ".Xauthority")))
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    os.unlink(path)  # scrot refuses to overwrite; let the tool create it
    try:
        for cmd in (["scrot", "-o", "-z", path],
                    ["maim", path],
                    ["import", "-window", "root", path]):
            if not shutil.which(cmd[0]):
                continue
            try:
                r = subprocess.run(cmd, env=env, capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and Path(path).exists():
                return Path(path).read_bytes()
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
