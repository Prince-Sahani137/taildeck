/* VNC dashboard — plain JS, no build step, no dependencies. */
(() => {
  "use strict";

  // The page is mounted at /vnc/ but can be opened at /vnc without the slash,
  // where relative asset and API URLs would resolve against the site root.
  const lastPart = location.pathname.split("/").pop();
  if (lastPart && !lastPart.includes(".")) {
    location.replace(location.pathname + "/" + location.search + location.hash);
    return;
  }

  // HTTPS origin of the tailscale-serve mount, injected by the server.
  const metaOrigin = (document.querySelector('meta[name="taildeck-origin"]') || {}).content || "";
  const SECURE = location.protocol === "https:";
  const HTTPS_ORIGIN = metaOrigin.startsWith("https://") ? metaOrigin : "";
  const PANEL_URL = SECURE ? location.origin + "/vnc/" : (HTTPS_ORIGIN ? HTTPS_ORIGIN + "/vnc/" : "");

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const icon = (id, cls = "icon") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;

  const fmtBytes = (n) => {
    n = Number(n) || 0;
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return (i === 0 ? n : n.toFixed(1)) + " " + units[i];
  };
  const fmtDur = (s) => {
    s = Math.floor(s);
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
  };
  const fmtDate = (secs) => {
    const d = new Date(secs * 1000);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))}m ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };

  const setText = (id, text) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };
  const setBar = (id, pct, bad = false, idle = false) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.width = `${Math.max(0, Math.min(100, Number(pct) || 0))}%`;
    el.classList.toggle("is-bad", Boolean(bad));
    el.classList.toggle("is-idle", Boolean(idle));
  };
  const metricBad = (id, bad) => {
    const el = document.getElementById(id);
    if (el) el.closest(".metric").classList.toggle("is-bad", Boolean(bad));
  };

  // ---------------------------------------------------------------- toast
  let toastTimer = null;
  function toast(message) {
    const el = $("#toast");
    el.textContent = message;
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add("on"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.remove("on");
      setTimeout(() => { el.hidden = true; }, 200);
    }, 1800);
  }

  // --------------------------------------------------------------- sidebar
  const openSidebar = () => {
    $("#sidebar").classList.add("open");
    $("#scrim").hidden = false;
  };
  const closeSidebar = () => {
    $("#sidebar").classList.remove("open");
    $("#scrim").hidden = true;
  };
  $("#menu-btn").addEventListener("click", openSidebar);
  $("#close-menu").addEventListener("click", closeSidebar);
  $("#scrim").addEventListener("click", closeSidebar);
  $$(".nav-item").forEach((a) => a.addEventListener("click", closeSidebar));

  // ----------------------------------------------------------------- router
  // Enabled workspaces (and their labels) are injected by the server.
  let WS_CFG = [];
  try {
    WS_CFG = JSON.parse((document.querySelector('meta[name="taildeck-workspaces"]') || {}).content || "[]");
  } catch (e) { /* fall through to defaults */ }
  if (!WS_CFG.length) {
    WS_CFG = [{ key: "hypr", label: "Hyprland" }, { key: "xfce", label: "XFCE" }, { key: "term", label: "Terminal" }];
  }
  const WORKSPACES = WS_CFG.map((w) => w.key);
  const VIEWS = { overview: ["Overview", "System overview"] };
  WS_CFG.forEach((w) => { VIEWS[w.key] = ["Workspaces", w.label]; });
  VIEWS.files = ["Manage", "Files"];
  VIEWS.logs = ["Manage", "Logs"];

  function currentView() {
    const raw = location.hash.replace(/^#\/?/, "").replace(/\/+$/, "");
    return VIEWS[raw] ? raw : "overview";
  }

  function route() {
    const name = currentView();
    $$(".view").forEach((view) => { view.hidden = view.dataset.view !== name; });
    $$("[data-nav]").forEach((a) => a.classList.toggle("is-active", a.dataset.nav === name));

    const [kicker, title] = VIEWS[name];
    setText("page-kicker", kicker);
    setText("page-title", title);
    document.title = `${title} · TailDeck`;

    for (const key of WORKSPACES) {
      if (key === name) {
        ensureConsole(key);
        requestAnimationFrame(() => resizeConsole(key));
      } else {
        stopLog(key);
      }
    }
    if (name !== "logs") stopLog("all");
    if (name === "files") pollFiles();
    closeSidebar();
  }

  // --------------------------------------------------------------- consoles
  const CONSOLES = {
    hypr: "hypr/vnc.html?autoconnect=true&reconnect=true&resize=scale&path=websockify",
    xfce: "xfce/vnc.html?autoconnect=true&reconnect=true&resize=scale&path=websockify",
    term: "term/",
  };

  function ensureConsole(key) {
    const host = $(`[data-console="${key}"]`);
    if (!host || host.dataset.loaded) return;
    host.dataset.loaded = "1";
    if (!SECURE) {
      host.innerHTML = `<span class="console-placeholder">Consoles need HTTPS. <a href="${PANEL_URL}#/${key}">Open the HTTPS panel</a>.</span>`;
      return;
    }
    const frame = document.createElement("iframe");
    frame.src = CONSOLES[key];
    frame.title = `${key} console`;
    frame.allow = "clipboard-read; clipboard-write; fullscreen";
    frame.referrerPolicy = "no-referrer";
    host.replaceChildren(frame);
  }

  function resizeConsole(key) {
    const frame = $(`[data-console="${key}"] iframe`);
    if (!frame) return;
    try { frame.contentWindow.dispatchEvent(new Event("resize")); } catch { /* not ready */ }
  }

  function reloadConsole(key) {
    const frame = $(`[data-console="${key}"] iframe`);
    if (frame) frame.src = frame.src;
    toast(`${key} console reloaded`);
  }

  async function fullscreenConsole(key) {
    const host = $(`[data-console="${key}"]`);
    if (!host) return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await host.requestFullscreen();
    } catch { /* denied */ }
  }

  function activateTab(group, name) {
    $$(`[data-tabs="${group}"] .tab`).forEach((tab) => {
      const on = tab.dataset.tab === name;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    $$(`[data-panes="${group}"] > .pane`).forEach((pane) => {
      pane.classList.toggle("is-active", pane.dataset.pane === name);
    });
    if (name === "console") resizeConsole(group);
    if (name === "logs" && !logStreams[group]) startLog(group);
  }

  // ------------------------------------------------------------------ stats
  let lastStats = null;

  function setStatus(key, active) {
    $$(`[data-status="${key}"]`).forEach((el) => {
      el.classList.toggle("up", active);
      el.classList.toggle("down", !active);
      const label = el.querySelector("span");
      if (label) label.textContent = active ? "running" : "stopped";
    });
  }

  async function pollStats() {
    let s;
    try {
      s = await (await fetch("api/stats", { cache: "no-store" })).json();
    } catch {
      return;
    }
    lastStats = s;

    setText("cpu-value", Math.round(s.cpu));
    setText("cpu-sub", `${s.cores || "?"} cores${s.freq ? ` · ${s.freq} MHz` : ""}`);
    metricBad("cpu-value", s.cpu > 90);

    setText("ram-value", Math.round(s.ram));
    setText("ram-sub", `${fmtBytes(s.ram_used)} / ${fmtBytes(s.ram_total)}`);
    metricBad("ram-value", s.ram > 90);

    setText("temp-value", s.temp ? Math.round(s.temp) : "–");
    setText("temp-sub", !s.temp ? "no sensor" : s.temp > 85 ? "running hot" : "cpu package");
    metricBad("temp-value", s.temp > 88);

    const g = s.gpu;
    if (g) {
      setText("gpu-value", Math.round(g.util));
      setText("gpu-sub", `${Math.round(g.temp)}°C · ${Math.round(g.used)}/${Math.round(g.total)} MB`);
      metricBad("gpu-value", g.temp > 85);
    } else {
      setText("gpu-value", "—");
      setText("gpu-sub", "no nvidia-smi");
      metricBad("gpu-value", false);
    }

    setText("hw-meta", `${fmtBytes(s.ram_total)} RAM`);
    setText("hw-cpu", `${s.cores || "?"} cores${s.freq ? ` · ${s.freq} MHz` : ""}`);
    setText("hw-load", (s.load || []).map((x) => x.toFixed(2)).join("  ") || "—");
    setText("hw-ram", `${fmtBytes(s.ram_used)} / ${fmtBytes(s.ram_total)} · ${Math.round(s.ram)}%`);
    setBar("hw-ram-bar", s.ram, s.ram > 90);
    setText("hw-swap", s.swap_total
      ? `${fmtBytes(s.swap_used)} / ${fmtBytes(s.swap_total)} · ${Math.round(s.swap)}%`
      : "none");
    setBar("hw-swap-bar", s.swap || 0, (s.swap || 0) > 80, !s.swap_total);
    setText("hw-disk", `${s.disk_mount || "/"} · ${fmtBytes(Math.max(0, (s.disk_total || 0) - (s.disk_used || 0)))} free of ${fmtBytes(s.disk_total || 0)}`);
    setBar("hw-disk-bar", s.disk, s.disk > 90);

    if (g) {
      setText("hw-gpu", `${g.name} · ${Math.round(g.used)}/${Math.round(g.total)} MB`);
      setBar("hw-gpu-bar", g.total ? (g.used / g.total) * 100 : 0, g.temp > 85);
    } else {
      setText("hw-gpu", "no nvidia-smi");
      setBar("hw-gpu-bar", 0, false, true);
    }

    if (s.battery) {
      setText("hw-batt", `${s.battery.percent}%${s.battery.plugged ? " · charging" : ""}`);
      setBar("hw-batt-bar", s.battery.percent, s.battery.percent < 15);
    } else {
      setText("hw-batt", "no battery");
      setBar("hw-batt-bar", 0, false, true);
    }

    setText("f-uptime", fmtDur(s.uptime));
    setText("f-ts", s.tailscale
      ? `${s.tailscale.online ? "online" : "offline"} · ${s.tailscale.ip || "no ip"}`
      : "not running");
    setText("f-containers", `${s.containers} running`);
    setText("f-backend", s.tailscale ? s.tailscale.backend : "—");

    const host = (s.tailscale && (s.tailscale.dns || s.tailscale.name)) || "";
    if (host) {
      const short = host.split(".")[0];
      setText("host-name", short);
      setText("brand-host", short);
    }
    const dot = $("#ts-dot");
    const label = $("#ts-label");
    if (s.tailscale) {
      dot.classList.toggle("up", s.tailscale.online);
      dot.classList.toggle("down", !s.tailscale.online);
      label.textContent = s.tailscale.online ? "tailnet online" : "tailnet offline";
    } else {
      dot.classList.remove("up");
      dot.classList.add("down");
      label.textContent = "tailnet down";
    }

    renderProcs(s.procs || []);

    const failed = s.failed_list || [];
    $("#alert").hidden = failed.length === 0;
    setText("alert-text", `${failed.length} ${failed.length === 1 ? "unit has" : "units have"} failed`);
    $("#alert-list").replaceChildren(...failed.map((unit) => {
      const li = document.createElement("li");
      li.textContent = unit;
      return li;
    }));
  }

  async function pollUnits() {
    let data;
    try {
      data = await (await fetch("api/units", { cache: "no-store" })).json();
    } catch {
      return;
    }
    const services = data.services || [];
    for (const svc of services) setStatus(svc.key, svc.active);
    const down = services.filter((s) => !s.active);
    setText("screens-meta", down.length
      ? `${down.length} of ${services.length} services down`
      : `${services.length} services up`);
  }

  // --------------------------------------------------------------- charts
  let history = [];
  const HISTORY_WINDOW = 120;

  function series(key) {
    return history.slice(-HISTORY_WINDOW).map((s) => (
      typeof s[key] === "number" ? s[key] : null
    ));
  }

  function drawSpark(svg, values, opts = {}) {
    if (!svg) return;
    const W = opts.width || 240;
    const H = opts.height || 56;
    const pad = opts.pad === undefined ? 3 : opts.pad;
    const nums = values.filter((v) => typeof v === "number" && isFinite(v));
    if (!nums.length) {
      svg.innerHTML = `<line class="spark-line spark-empty" x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}"/>`;
      return;
    }
    const lo = opts.min === undefined ? Math.min(...nums) : opts.min;
    const hi = opts.max === undefined ? Math.max(...nums) : opts.max;
    const span = (hi - lo) || 1;
    const n = values.length;
    const px = (i) => (n <= 1 ? W : (i / (n - 1)) * W);
    const py = (v) => H - pad - ((Math.min(hi, Math.max(lo, v)) - lo) / span) * (H - pad * 2);

    const runs = [];
    let run = [];
    values.forEach((v, i) => {
      if (typeof v === "number" && isFinite(v)) {
        run.push([px(i), py(v)]);
      } else if (run.length) {
        runs.push(run);
        run = [];
      }
    });
    if (run.length) runs.push(run);

    if (runs.length === 1 && runs[0].length === 1) {
      const [x, y] = runs[0][0];
      svg.innerHTML = `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.6" fill="var(--accent)"/>`;
      return;
    }

    const line = runs.map((points) => points
      .map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`)
      .join("")).join(" ");
    const areas = runs.map((points) => {
      if (points.length < 2) return "";
      const body = points.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
      return `M${points[0][0].toFixed(1)} ${H}${body}L${points[points.length - 1][0].toFixed(1)} ${H}Z`;
    }).join(" ");

    svg.innerHTML = `<path class="spark-area" d="${areas}"/><path class="spark-line" d="${line}"/>`;
  }

  function updateCharts() {
    drawSpark($('[data-spark="cpu"]'), series("cpu"), { min: 0, max: 100 });
    drawSpark($('[data-spark="ram"]'), series("ram"), { min: 0, max: 100 });
    drawSpark($('[data-spark="temp"]'), series("temp"), { min: 20, max: 100 });
    drawSpark($('[data-spark="gpu"]'), series("gpu"), { min: 0, max: 100 });
  }

  async function pollHistory() {
    let data;
    try {
      data = await (await fetch("api/history", { cache: "no-store" })).json();
    } catch {
      return;
    }
    history = data.samples || [];
    updateCharts();
    if (lastStats) renderProcs(lastStats.procs || []);
  }

  // -------------------------------------------------------------- processes
  function procSeries(name) {
    return history.slice(-40).map((sample) => {
      const hit = (sample.procs || []).find((p) => p.name === name);
      return hit ? hit.cpu : null;
    });
  }

  function renderProcs(list) {
    const ul = $("#procs");
    if (!list.length) {
      const li = document.createElement("li");
      li.className = "proc-empty";
      li.textContent = "no process data yet";
      ul.replaceChildren(li);
      return;
    }
    ul.replaceChildren(...list.map((p) => {
      const li = document.createElement("li");

      const name = document.createElement("span");
      name.className = "proc-name";
      name.textContent = p.name;
      name.title = p.name;

      const cpu = document.createElement("span");
      cpu.className = "proc-cpu";
      cpu.textContent = `${p.cpu.toFixed(1)}%`;

      const spark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      spark.setAttribute("class", "proc-spark");
      spark.setAttribute("viewBox", "0 0 64 18");
      spark.setAttribute("preserveAspectRatio", "none");
      drawSpark(spark, procSeries(p.name), { width: 64, height: 18, pad: 2, min: 0 });

      const mem = document.createElement("span");
      mem.className = "proc-mem";
      mem.textContent = `${p.mem.toFixed(1)}%`;

      li.append(name, cpu, spark, mem);
      return li;
    }));
  }

  // ------------------------------------------------------------- thumbnails
  function refreshThumbs() {
    for (const key of WORKSPACES.filter((k) => k !== "term")) {
      const img = document.querySelector(`[data-shot="${key}"]`);
      if (!img) continue;
      const empty = img.parentElement.querySelector(".preview-empty");
      img.onload = () => {
        img.classList.add("is-loaded");
        if (empty) empty.hidden = true;
      };
      img.onerror = () => {
        img.classList.remove("is-loaded");
        if (empty) {
          empty.hidden = false;
          empty.textContent = "preview unavailable";
        }
      };
      img.src = `api/thumb?which=${key}&t=${Date.now()}`;
    }
  }

  // ------------------------------------------------------------------- logs
  const logStreams = {};

  function logParts(key) {
    return {
      select: $(`[data-log-select="${key}"]`),
      pre: $(`[data-log="${key}"]`),
      state: $(`[data-log-state="${key}"]`),
      button: $(`[data-log-toggle="${key}"]`),
    };
  }

  function startLog(key) {
    const { select, pre, state, button } = logParts(key);
    if (!pre) return;
    stopLog(key, true);
    pre.textContent = "";
    if (state) {
      state.textContent = "connecting";
      state.classList.add("on");
    }
    if (button) button.innerHTML = icon("i-stop") + "Stop";

    const unit = select ? select.value : "";
    const es = new EventSource(`api/logs?unit=${encodeURIComponent(unit)}`);
    logStreams[key] = es;
    es.onopen = () => {
      if (state) state.textContent = "live";
    };
    es.onmessage = (ev) => {
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 40;
      pre.textContent += ev.data + "\n";
      if (pre.textContent.length > 200000) pre.textContent = pre.textContent.slice(-150000);
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    };
    es.onerror = () => {
      es.close();
      delete logStreams[key];
      if (state) {
        state.textContent = "disconnected";
        state.classList.remove("on");
      }
      if (button) button.innerHTML = icon("i-play") + "Stream";
    };
  }

  function stopLog(key, keepState = false) {
    if (logStreams[key]) {
      logStreams[key].close();
      delete logStreams[key];
    }
    if (keepState) return;
    const { state, button } = logParts(key);
    if (state) {
      state.textContent = "idle";
      state.classList.remove("on");
    }
    if (button) button.innerHTML = icon("i-play") + "Stream";
  }

  function toggleLog(key) {
    if (logStreams[key]) stopLog(key);
    else startLog(key);
  }

  // ---------------------------------------------------------------- actions
  async function restart(target, button) {
    if (target === "vnc-stack" && !confirm("Restart wayvnc, XFCE and both noVNC proxies?")) return;
    const original = button.innerHTML;
    button.disabled = true;
    button.textContent = "restarting";
    try {
      const res = await fetch("api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Panel": "1" },
        body: JSON.stringify({ target }),
      });
      if (!res.ok) throw new Error(await res.text());
      toast(`${target === "vnc-stack" ? "stack" : target} restarting`);
      setTimeout(pollUnits, 2500);
      setTimeout(pollUnits, 9000);
    } catch {
      toast("restart failed");
    } finally {
      setTimeout(() => {
        button.disabled = false;
        button.innerHTML = original;
      }, 2500);
    }
  }

  document.addEventListener("click", (ev) => {
    const tab = ev.target.closest("[data-tab]");
    if (tab) {
      activateTab(tab.closest("[data-tabs]").dataset.tabs, tab.dataset.tab);
      return;
    }
    const logBtn = ev.target.closest("[data-log-toggle]");
    if (logBtn) {
      toggleLog(logBtn.dataset.logToggle);
      return;
    }
    const reload = ev.target.closest("[data-reload]");
    if (reload) {
      reloadConsole(reload.dataset.reload);
      return;
    }
    const fs = ev.target.closest("[data-fullscreen]");
    if (fs) {
      fullscreenConsole(fs.dataset.fullscreen);
      return;
    }
    const restartBtn = ev.target.closest("[data-restart]");
    if (restartBtn) {
      restart(restartBtn.dataset.restart, restartBtn);
    }
  });

  document.addEventListener("change", (ev) => {
    const sel = ev.target.closest("[data-log-select]");
    if (sel && logStreams[sel.dataset.logSelect]) startLog(sel.dataset.logSelect);
  });

  $("#log-clear").addEventListener("click", () => {
    const pre = $('[data-log="all"]');
    if (pre) pre.textContent = "";
  });

  // ------------------------------------------------------------------ files
  async function pollFiles() {
    let data;
    try {
      data = await (await fetch("api/files", { cache: "no-store" })).json();
    } catch {
      return;
    }
    if (data.dir) setText("files-dir", data.dir);

    const list = $("#files");
    const files = data.files || [];
    setText("files-count", files.length
      ? `${files.length} file${files.length === 1 ? "" : "s"}`
      : "empty");

    if (!files.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "nothing in ~/Downloads yet";
      list.replaceChildren(li);
      return;
    }

    const head = document.createElement("li");
    head.className = "head";
    head.innerHTML = '<span>Name</span><span class="size">Size</span><span class="date">Modified</span><span></span>';

    list.replaceChildren(head, ...files.map((f) => {
      const li = document.createElement("li");

      const name = document.createElement("span");
      name.className = "name";
      name.textContent = f.name;
      name.title = f.name;

      const size = document.createElement("span");
      size.className = "size";
      size.textContent = fmtBytes(f.size);

      const date = document.createElement("span");
      date.className = "date";
      date.textContent = fmtDate(f.mtime);
      date.title = new Date(f.mtime * 1000).toLocaleString();

      const link = document.createElement("a");
      link.className = "get";
      link.href = `api/files/get?name=${encodeURIComponent(f.name)}`;
      link.title = `Download ${f.name}`;
      link.setAttribute("download", f.name);
      link.innerHTML = icon("i-download");

      li.append(name, size, date, link);
      return li;
    }));
  }

  const fileInput = $("#file");
  const uploadBtn = $("#upload");
  let selected = [];

  fileInput.addEventListener("change", () => {
    selected = [...fileInput.files];
    setText("upload-note", selected.length ? `${selected.length} selected` : "");
    uploadBtn.disabled = selected.length === 0;
  });

  const drop = $("#drop");
  for (const type of ["dragenter", "dragover"]) {
    drop.addEventListener(type, (ev) => {
      ev.preventDefault();
      drop.classList.add("is-over");
    });
  }
  for (const type of ["dragleave", "drop"]) {
    drop.addEventListener(type, () => drop.classList.remove("is-over"));
  }
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer && ev.dataTransfer.files.length) {
      selected = [...ev.dataTransfer.files];
      setText("upload-note", `${selected.length} selected`);
      uploadBtn.disabled = false;
    }
  });

  uploadBtn.addEventListener("click", () => {
    const files = selected;
    if (!files.length) return;
    const bar = $("#upload-bar");
    const progress = $("#progress");
    let done = 0;
    progress.classList.add("on");
    uploadBtn.disabled = true;

    const next = () => {
      if (done >= files.length) {
        progress.classList.remove("on");
        bar.style.width = "0%";
        setText("upload-note", "");
        fileInput.value = "";
        selected = [];
        uploadBtn.disabled = true;
        pollFiles();
        toast("upload complete");
        return;
      }
      const file = files[done];
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `api/files?name=${encodeURIComponent(file.name)}`);
      xhr.setRequestHeader("X-Panel", "1");
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (ev) => {
        if (!ev.lengthComputable) return;
        const overall = (done + ev.loaded / ev.total) / files.length;
        bar.style.width = `${(overall * 100).toFixed(1)}%`;
      };
      xhr.onloadend = () => {
        done += 1;
        next();
      };
      xhr.send(file);
    };
    next();
  });

  // ----------------------------------------------------------------- header
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch {
        ok = false;
      }
      ta.remove();
      return ok;
    }
  }

  $("#copy-url").addEventListener("click", async () => {
    const ok = await copyText(PANEL_URL || location.href);
    toast(ok ? "panel address copied" : "copy failed");
  });

  if (!SECURE) {
    $("#insecure").hidden = false;
    if (PANEL_URL) $("#insecure-link").href = PANEL_URL;
    else $("#insecure-link").removeAttribute("href");
  }

  // ------------------------------------------------------------------- loop
  const tick = (fn, ms) => {
    fn();
    setInterval(() => {
      if (!document.hidden) fn();
    }, ms);
  };

  tick(pollStats, 4000);
  tick(pollHistory, 3000);
  tick(pollUnits, 6000);
  tick(refreshThumbs, 20000);
  pollFiles();

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    pollStats();
    pollHistory();
    pollUnits();
  });

  window.addEventListener("hashchange", route);
  route();
})();
