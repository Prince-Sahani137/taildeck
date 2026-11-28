<p align="center">
  <img src="img/logo.svg" alt="TailDeck logo" width="120" height="120">
</p>

<h1 align="center">TailDeck</h1>

<p align="center">
  A lightweight, self-hosted control deck for your own Linux or Windows machine — reachable only over <a href="https://tailscale.com">Tailscale</a>.
</p>

<p align="center">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white">
  <img alt="Linux and Windows" src="https://img.shields.io/badge/platform-linux%20%7C%20windows-lightgrey">
  <img alt="No build step" src="https://img.shields.io/badge/build-none-brightgreen">
  <img alt="Dependencies" src="https://img.shields.io/badge/deps-stdlib%20%2B%20psutil-informational">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue">
  <img alt="Tailscale only" src="https://img.shields.io/badge/network-tailnet--only-black">
</p>

---

TailDeck puts your desktop, a full **terminal**, live hardware charts, file transfer and service logs behind one password-protected web page on your tailnet. On Linux that means your **Hyprland** session and a persistent **XFCE** desktop; on **Windows** it is your real Windows desktop. Nothing is exposed to your LAN or the internet.

It is plain Python (standard library + `psutil`) and a dependency-free vanilla JS UI. There is no build step, no bundler, no framework, and no external requests — you clone it, run it, and open it in a browser.

<p align="center">
  <img src="img/dashboard.png" alt="TailDeck dashboard: CPU, memory, temperature and GPU charts, hardware panel, Tailscale state and top processes" width="100%">
</p>

<p align="center"><sub><b>Overview</b> — live charts with 20 minutes of history, hardware details, Tailscale state, failed units and top processes.</sub></p>

## What you get

| | Feature |
|---|---|
| 🖥️ | **Hyprland console** — mirrors your real laptop/desktop screen through `wayvnc` + noVNC. Same keyboard, same windows. |
| 🪟 | **XFCE console** — a separate virtual desktop (TigerVNC) that stays logged in even when you disconnect. |
| ⌨️ | **Browser terminal** — a real shell over `ttyd`, no SSH client required. |
| 📊 | **Live metrics** — CPU, RAM, swap, disk, CPU package temperature, NVIDIA GPU (util, temp, VRAM), battery, load average, uptime and container count. Sparkline charts keep 20 minutes of history. |
| 🖼️ | **Screen previews** — live thumbnails of both desktops on the overview page (`grim` for Hyprland, `scrot`/`maim`/ImageMagick for XFCE). |
| 📂 | **Files** — drag-and-drop upload and download in a shared folder (defaults to `~/Downloads`), 512 MB per file. |
| 📜 | **Logs** — live streams for every service in the stack, straight into the browser via SSE (`journalctl` on systemd, built-in capture elsewhere). |
| 🔁 | **Restart buttons** — bounce a single console, its proxy, or the whole stack from the page. |
| 🪟 | **Windows support** — runs natively on Windows 10/11 with a built-in service manager; no WSL, no systemd. See [Windows](#windows). |
| 🔒 | **Tailnet-only** — binds loopback by default, HTTP Basic Auth everywhere, same-origin checks on every mutating request and WebSocket upgrade. |
| 📱 | **Installable** — a web manifest and icons, so the panel can live on your phone's home screen. |

Missing optional tools never break the page: no `nvidia-smi` means the GPU card shows `—`, no `docker` means zero containers, no screenshot tool means "no preview".

## Screenshots

### Hyprland — mirrors your real screen

<img src="img/hypr.png" alt="Hyprland console in TailDeck, showing the mirrored desktop inside noVNC" width="100%">

### XFCE — a separate desktop that stays logged in

<img src="img/xfce2.png" alt="XFCE virtual desktop inside the TailDeck console" width="100%">

When the VNC server has a password, noVNC asks for it once inside the console:

<img src="img/xfce.png" alt="noVNC credentials prompt for the XFCE console" width="60%">

### Terminal — a full shell in the browser

<img src="img/terminal.png" alt="Browser terminal running a shell inside TailDeck" width="100%">

### Files — move things between the machine and your browser

<img src="img/files.png" alt="Files tab with drag-and-drop upload and a downloadable file list" width="100%">

### Logs — watch any unit in the stack

<img src="img/logs.png" alt="Logs tab with a unit selector and a live log pane" width="100%">

## How it fits together

```
                    Tailscale (WireGuard, tailnet only)
                                   │
                     tailscale serve  →  HTTPS + /vnc mount
                                   │
                        ┌──────────▼──────────┐
                        │  TailDeck :6100     │   auth · CSRF · history · files
                        │  taildeck.py        │
                        └───┬──────┬──────┬───┘
              /hypr/*       │      │      │      /term/*
        ┌───────────────────┘      │      └────────────────────┐
        ▼                          ▼                           ▼
  websockify :6101          websockify :6102              ttyd :6103
        │                          │                           │
        ▼                          ▼                           ▼
  wayvnc :5900             TigerVNC :5902                 your shell
  (Hyprland screen)        (virtual XFCE desktop)
```

On Windows the picture is the same with two tabs: *Desktop* (a VNC server on `:5900` behind websockify) and *Terminal* (ttyd running PowerShell).

All console backends bind to `127.0.0.1` only. The panel proxies `/hypr/*`, `/xfce/*` and `/term/*` to them, injects the shared credentials and relays WebSockets, so the iframes never show a second Basic Auth prompt.

## Requirements

- **Tailscale**, plus one of:
  - Linux (systemd user units recommended; without systemd TailDeck supervises the consoles itself)
  - **Windows 10/11** (see [Windows](#windows))
  - macOS uses the same code path as Windows but is untested
- **Python 3.9+** (tested on 3.12) and `psutil` → `pip install -r requirements.txt`
- Optional, per feature:
  - `websockify` + `novnc` — the desktop consoles (`pip install websockify` on Windows)
  - `wayvnc` + `grim` — Hyprland console and thumbnail
  - `tigervnc-standalone-server` — virtual XFCE desktop
  - `ttyd` — browser terminal (1.7.0+ for Windows, needs Windows 10 or newer)
  - `scrot`, `maim` or ImageMagick — XFCE thumbnail on Linux; `Pillow` for the desktop thumbnail on Windows
  - `nvidia-smi` — GPU metrics · `docker` — container count

## Quick start

```sh
git clone https://github.com/Prince-Sahani137/taildeck.git ~/taildeck
cd ~/taildeck

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
$EDITOR .env        # set PANEL_USER and a long random PANEL_PASSWORD

scripts/run-taildeck.sh    # panel on http://127.0.0.1:6100
```

Generate a password with:

```sh
openssl rand -hex 16
```

> The panel refuses to start without a real `PANEL_PASSWORD`; the `CHANGE_ME` placeholder is rejected.

### Expose it on your tailnet (HTTPS)

noVNC and the terminal need a secure context, so serve the panel over HTTPS rather than hitting `:6100` directly:

```sh
tailscale serve --bg --set-path /vnc http://127.0.0.1:6100
tailscale serve status          # shows the exact URL
```

Open the printed URL:

```
https://<your-machine>.<your-tailnet>.ts.net/vnc/
```

The panel is mounted under `/vnc`; `tailscale serve` strips that prefix before proxying, and TailDeck accepts both the stripped and prefixed forms.

## Enabling the consoles

Each console is optional and independent. Start the pieces you want:

### Terminal

```sh
scripts/run-term.sh     # ttyd on 127.0.0.1:6103
```

### Hyprland (mirror your current session)

Start `wayvnc` **inside your running Hyprland session**:

```sh
wayvnc 127.0.0.1 5900
scripts/run-ws-hypr.sh  # websockify on 127.0.0.1:6101
```

### XFCE (separate persistent desktop)

One-time setup — create a VNC password (noVNC will ask for it):

```sh
vncpasswd
```

Then start the virtual desktop and its proxy:

```sh
systemctl --user enable --now taildeck-xfce   # TigerVNC :2 (port 5902)
scripts/run-ws-xfce.sh                        # websockify on 127.0.0.1:6102
```

`scripts/xfce-no-blank.sh` disables screen blanking and the lock screen so the remote desktop stays usable.

## Run as services

Copy the user units (they expect the project at `~/taildeck`):

```sh
mkdir -p ~/.config/systemd/user
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload

systemctl --user enable --now taildeck taildeck-term           # core + terminal
systemctl --user enable --now wayvnc taildeck-ws-hypr          # Hyprland (optional)
systemctl --user enable --now taildeck-xfce taildeck-ws-xfce   # XFCE (optional)

loginctl enable-linger "$USER"    # keep them running after logout
```

The unit names matter: the Restart buttons and the Logs tab call them by name. Review `systemd/taildeck-xfce.service` and `systemd/wayvnc.service` first — they are examples that depend on your distro, display server and session.

## Windows

TailDeck runs natively on Windows 10/11, with no WSL and no systemd. The server, login, proxy, files and UI are identical; only the plumbing differs:

| | Linux | Windows |
|---|---|---|
| Tabs | Hyprland, XFCE, Terminal | **Desktop** (your real desktop), Terminal |
| Services | systemd user units | Built-in manager: TailDeck starts, restarts and logs websockify and ttyd itself |
| Desktop thumbnail | `grim` / `scrot` | Pillow `ImageGrab` |
| Autostart | `systemctl --user enable` | Task Scheduler logon task |

### Prerequisites

1. **Python 3.9+** from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. **Tailscale for Windows**, signed in.
3. A **VNC server** such as TigerVNC or TightVNC. Windows has none built in. Set a VNC password and configure it to accept loopback (localhost) connections only; TailDeck expects it on `127.0.0.1:5900`.
4. **ttyd 1.7.0+** (first release with native Windows support, needs Windows 10 or newer): download the Windows `.exe` from the [releases page](https://github.com/tsl0922/ttyd/releases) and put it on `PATH`, or set `TTYD_BIN` in `.env`.
5. **noVNC**: the setup script can download it for you.

### Install

In PowerShell, from the project folder:

```powershell
git clone https://github.com/Prince-Sahani137/taildeck.git
cd taildeck
powershell -ExecutionPolicy Bypass -File windows\setup.ps1 -DownloadNoVNC
```

The script creates `.venv`, installs the dependencies, writes `.env` with a random `PANEL_PASSWORD` (readable only by you), downloads noVNC into `.\novnc`, and prints a checklist of anything still missing.

```powershell
windows\run.ps1                                         # run in this window, Ctrl+C to stop
tailscale serve --bg --set-path /vnc http://127.0.0.1:6100   # HTTPS over your tailnet
```

Open the URL shown by `tailscale serve status`, e.g. `https://<machine>.<tailnet>.ts.net/vnc/`.

### Start automatically at logon

```powershell
windows\install-task.ps1      # registers a Task Scheduler task and starts it
windows\uninstall-task.ps1    # stops it and removes the task
```

It is a **logon task, not a Windows service**, on purpose: services run in an isolated session and cannot capture your desktop for the live thumbnail. While it runs hidden, output goes to `taildeck.log` next to `taildeck.py`.

### Windows notes

- Leave `PANEL_BIND=127.0.0.1` (the default) to avoid a Windows Firewall prompt; `tailscale serve` reaches it over loopback.
- The terminal starts PowerShell. Use `TERM_SHELL=cmd.exe` (or a full path) in `.env` to change it.
- The Desktop thumbnail needs a logged-in, unlocked screen; otherwise the card shows "no preview" while the console itself still works.
- Rename or hide tabs with `WORKSPACES` and `WS_XFCE_LABEL` (see [Configuration](#configuration)). Custom service commands go in `services.json`; start from `services.windows.example.json`.
- If TailDeck is killed hard, its helper processes (websockify, ttyd) are cleaned up the next time it starts.

## Configuration

Everything lives in `.env` (copy `.env.example`). Real environment variables win over the file. `.env` is git-ignored.

| Variable | Default | Purpose |
|---|---|---|
| `PANEL_USER` | `admin` | Login user for the panel and console backends |
| `PANEL_PASSWORD` | — | **Required.** Shared password; `CHANGE_ME` is refused |
| `PANEL_BIND` | `127.0.0.1` | Bind address; `tailscale` means this node's tailnet IPv4 |
| `PANEL_PORT` | `6100` | Panel HTTP port |
| `PROJECT_NAME` | `TailDeck` | Name shown in the UI |
| `WS_HYPR_HOST` / `WS_HYPR_PORT` | `127.0.0.1` / `6101` | Hyprland websockify backend |
| `WS_XFCE_HOST` / `WS_XFCE_PORT` | `127.0.0.1` / `6102` | XFCE websockify backend |
| `TERM_HOST` / `TERM_PORT` | `127.0.0.1` / `6103` | ttyd backend |
| `VNC_HYPR_PORT` / `VNC_XFCE_PORT` | `5900` / `5902` (`5900` on Windows) | Real VNC servers the proxies connect to |
| `NOVNC_DIR` | `/usr/share/novnc` (Windows: `./novnc`) | noVNC web files |
| `TERM_SHELL` | `$SHELL` (Windows: PowerShell) | Shell the browser terminal starts |
| `TTYD_BIN` | `ttyd` | Full path to ttyd if it is not on `PATH` |
| `XFCE_DISPLAY` | `:2` | Linux: X display for thumbnails and the no-blank script |
| `WORKSPACES` | `hypr,xfce,term` (Windows: `xfce,term`) | Tabs to show, any of `hypr`, `xfce`, `term` |
| `WS_<KEY>_LABEL` / `_DESC` | per tab | Rename a tab, e.g. `WS_XFCE_LABEL=Desktop` |
| `SERVICE_MANAGER` | `auto` | `auto`, `systemd` or `process` (see below) |
| `SERVICES_FILE` | `services.json` | Custom service definitions for the built-in manager |
| `TAILSCALE_BIN` | auto | Full path to the tailscale CLI if it is not on `PATH` |
| `THUMB_SCALE` | `0.35` | Windows/macOS: desktop thumbnail size as a fraction of the screen |
| `FILES_DIR` | `~/Downloads` | Shared folder behind the Files tab |
| `MAX_UPLOAD` | `536870912` (512 MB) | Max upload size in bytes |
| `HTTPS_ORIGIN` | auto | Override the HTTPS mount URL (otherwise from `tailscale status`) |
| `STATS_TTL` / `THUMB_TTL` | `2` / `5` | Cache seconds for stats and thumbnails |
| `HISTORY_INTERVAL` / `HISTORY_SIZE` | `3` / `400` | Chart sample interval and ring-buffer size |

### Service manager

TailDeck needs to start, restart and read the logs of the console backends. With `SERVICE_MANAGER=auto` it uses **systemd user units** when `systemctl --user` works, and otherwise its **built-in manager**: it launches websockify and ttyd as child processes, restarts them with back-off when they crash, keeps their output in memory for the Logs tab, and stops them when it exits. On Windows the built-in manager is always used.

Without a `services.json` the built-in defaults are used (a websockify proxy per desktop tab and ttyd for the terminal). To customise, copy `services.example.json` (or `services.windows.example.json`) to `services.json`:

```json
{
  "services": [
    {"id": "taildeck-term", "label": "Terminal", "workspace": "term", "autostart": true,
     "cmd": ["ttyd", "-W", "-i", "127.0.0.1", "-p", "${TERM_PORT}",
             "-c", "${PANEL_USER}:${PANEL_PASSWORD}", "powershell.exe"]}
  ]
}
```

`${NAME}` is expanded from the environment (no shell involved), a leading `python` means the interpreter running TailDeck, and the password is masked in the Logs tab. `workspace` ties a service to a tab and `role` may be `proxy` for the websockify front of a desktop tab.

## API

All endpoints require HTTP Basic Auth.

| Endpoint | Purpose |
|---|---|
| `GET /` | Dashboard UI |
| `GET /api/stats` | Live CPU/RAM/disk/GPU/temps/processes/Tailscale |
| `GET /api/history` | Last 20 minutes of chart samples |
| `GET /api/units` | State of every service behind the cards |
| `GET /api/config` | Non-secret settings for the page |
| `GET /api/thumb?which=hypr\|xfce` | Live PNG desktop preview |
| `GET /api/logs?unit=<key>` | Live log stream (SSE) |
| `POST /api/action` | `{"target":"hypr\|xfce\|term\|vnc-stack"}` restart |
| `GET /api/files` | List the shared folder |
| `POST /api/files?name=<file>` | Upload one file (raw body, needs `X-Panel: 1`) |
| `GET /api/files/get?name=<file>` | Download one file |
| `* /hypr/*`, `/xfce/*`, `/term/*` | Proxied consoles (HTTP + WebSocket) |

## Project structure

```
taildeck/
├── taildeck.py            # HTTP server: routing, auth, CSRF/origin guards,
│                          # console proxying, history sampler, files, SSE logs
├── services.py            # service manager: systemd or the built-in process supervisor
├── metrics.py             # CPU/RAM/disk/GPU/temps/processes/Tailscale (psutil + CLI)
├── xcap.py                # desktop screenshot for the overview thumbnail (X11 or Pillow)
├── index.html             # single-page UI shell (all views + inline SVG icons)
├── style.css              # dark theme, layout, charts and console styling
├── app.js                 # vanilla JS: routing, polling, charts, consoles, files, logs
├── icon.svg               # favicon
├── manifest.webmanifest   # installable web app metadata
├── requirements.txt       # psutil (+ Pillow / websockify on Windows)
├── .env.example           # documented configuration template
├── services.example.json  # built-in manager examples (Linux / Windows)
├── services.windows.example.json
│
├── scripts/
│   ├── _env.sh            # sources ../.env, validates PANEL_PASSWORD
│   ├── run-taildeck.sh    # panel (uses ./.venv/bin/python when present)
│   ├── run-term.sh        # ttyd on 127.0.0.1:6103
│   ├── run-ws-hypr.sh     # websockify :6101 -> wayvnc :5900
│   ├── run-ws-xfce.sh     # websockify :6102 -> TigerVNC :5902
│   └── xfce-no-blank.sh   # disable XFCE blanking / lock screen
│
├── systemd/               # user units (expect the project in ~/taildeck)
│   ├── taildeck.service       # the panel
│   ├── taildeck-term.service  # ttyd
│   ├── taildeck-ws-hypr.service
│   ├── taildeck-ws-xfce.service
│   ├── taildeck-xfce.service  # TigerVNC virtual desktop (example)
│   └── wayvnc.service         # Hyprland VNC server (example)
│
├── windows/               # PowerShell helpers
│   ├── setup.ps1          # venv, dependencies, .env, optional noVNC download
│   ├── run.ps1            # run in the foreground
│   ├── install-task.ps1   # start at logon (Task Scheduler)
│   └── uninstall-task.ps1
│
├── tests/
│   ├── smoke_test.py      # end-to-end test with fake console backends
│   └── test_services.py   # service manager, Windows helpers, workspaces
│
├── .github/workflows/ci.yml   # tests on Ubuntu and Windows
│
├── img/                   # README screenshots, logo and social preview
│   ├── logo.svg · logo.png · social-preview.png
│   ├── dashboard.png · hypr.png · xfce.png · xfce2.png
│   └── terminal.png · files.png · logs.png
│
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE                # MIT
└── README.md
```

Runtime state — `.env`, `services.json`, `.tailnet-name`, `taildeck.log`, `novnc/`, `.venv/`, `__pycache__/` and partial uploads — is git-ignored.

## Security model

TailDeck hands out shell and desktop access, so treat it accordingly.

- **Tailnet only.** Listens on loopback by default; set `PANEL_BIND=tailscale` to bind the tailnet IP. Reach it through `tailscale serve` or the tailnet, never by opening a port to the internet or LAN.
- **HTTP Basic Auth** on every request; failed logins are delayed by 1 second.
- **CSRF protection.** Mutating requests need `X-Panel: 1`, and any request carrying an `Origin` header — including WebSocket upgrades — must be same-origin, so a malicious page cannot ride on cached credentials against your terminal.
- **Loopback backends.** websockify and ttyd bind `127.0.0.1` and require the same credentials. Their password appears on the process command line, so other local users can see it in `ps` (or Task Manager) — this is designed for a single-user machine. TailDeck masks it in the Logs tab.
- **Path safety.** Uploads and downloads are restricted to plain filenames inside `FILES_DIR`; hidden names and traversal attempts are rejected. Uploads also refuse names that are illegal on Windows (`CON`, `a:b`, trailing dots), so the shared folder stays portable.
- **No port stealing on Windows.** The server does not set `SO_REUSEADDR` there, so a second process cannot bind the same port.

Use a long random `PANEL_PASSWORD`, keep `.env` at `chmod 600`, and keep your Tailscale ACLs tight. To report a vulnerability, see [SECURITY.md](SECURITY.md) — please do not open a public issue.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `set a real PANEL_PASSWORD` on start | Copy `.env.example` to `.env` and set a password |
| Consoles say they need HTTPS | Open the `https://…ts.net/vnc/` URL from `tailscale serve`, not `http://IP:6100` |
| Console tab shows 502 | That backend isn't running: `systemctl --user status taildeck-ws-hypr` (or `-xfce`, `-term`) |
| Hyprland console is black | Start `wayvnc 127.0.0.1 5900` inside the Hyprland session |
| Hyprland thumbnail missing | Needs `grim` and a running Hyprland session |
| XFCE thumbnail missing | Install `scrot`, `maim` or ImageMagick and check `XFCE_DISPLAY` |
| XFCE console asks for a password | It's the VNC password from `vncpasswd` |
| Restart buttons do nothing | Unit names must match the files in `systemd/` |
| Upload fails with 413 | File exceeds `MAX_UPLOAD` |
| Windows: Desktop tab shows 502 or noVNC 404 | Run `windows\\setup.ps1 -DownloadNoVNC`, check the VNC server is on `127.0.0.1:5900`, then look at **Logs → Proxy** |
| Windows: Terminal tab shows 502 | ttyd is missing; the **Logs** tab says `executable not found: ttyd`. Install ttyd 1.7.0+ or set `TTYD_BIN` |
| Windows: a service shows *failed* | It crashed repeatedly; fix the cause in its log, then press **Restart** |
| Windows: no desktop thumbnail | Needs a logged-in, unlocked screen and `pip install Pillow` |
| Windows: nothing starts at logon | Run `windows\\install-task.ps1` again and check `taildeck.log` |

## Tests

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tests/smoke_test.py     # end-to-end, prints ALL PASSED
.venv/bin/python tests/test_services.py  # service manager and portability, prints ALL PASSED
node --check app.js                      # JS syntax check
```

On Windows use `.venv\Scripts\python` instead. The smoke test starts the panel with fake console services and checks auth, CSRF/origin rules, the API, log streaming and restart, file safety, workspace filtering, HTTP proxying and WebSocket echo/refusal. `test_services.py` covers crash back-off, process-tree cleanup, password masking and the Windows code paths. Neither needs Tailscale, Hyprland or a VNC server, and CI runs both on Ubuntu and Windows.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The short version: stay dependency-light, never commit secrets or machine-specific values, and add a test for anything security-sensitive.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
