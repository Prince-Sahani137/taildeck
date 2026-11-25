"""Screenshot of the virtual X11 (XFCE/TigerVNC) desktop, as PNG bytes.

Tries scrot, maim, then ImageMagick's `import`. Configure with
XFCE_DISPLAY (default :2) and XFCE_XAUTHORITY (default ~/.Xauthority).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def capture_png() -> bytes | None:
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
