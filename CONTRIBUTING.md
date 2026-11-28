# Contributing to TailDeck

Thanks for helping out! Bug reports, fixes and ideas are all welcome.

## Ground rules

- Keep it dependency-light: the server uses the Python standard library plus
  `psutil` (Pillow and websockify are only needed on Windows), and the web UI
  has no build step and no external requests.
- Keep it cross-platform: code must run on Linux and Windows. Use `pathlib`,
  pass `subprocess` arguments as lists, never assume `systemctl`, `/proc` or
  `os.getuid()` outside Linux-only branches. CI runs both.
- Never commit secrets or machine-specific values (passwords, tailnet names,
  IPs, usernames, home paths). Configuration belongs in `.env` / `.env.example`.
- Security-sensitive changes (auth, proxying, file handling) need a test.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tests/test_services.py  # must print ALL PASSED
.venv/bin/python tests/smoke_test.py     # must print ALL PASSED
node --check app.js                      # JS syntax check
```

On Windows use `.venv\Scripts\python`. The tests start the panel with fake
services, so they need no Tailscale, Hyprland or VNC server.

## Pull requests

1. Fork and create a branch.
2. Make a focused change and run the checks above.
3. Describe what changed and how you tested it.
