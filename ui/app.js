/* FurScraper UI. Vanilla, no build step.

   All URLs are relative: the page is served both at / (direct) and under
   /furscraper/ (through the Tsidia hub), and relative paths resolve correctly
   at either. */

const TOKEN = window.__TOKEN__;
const $ = (id) => document.getElementById(id);

let state = null;      // last /api/state payload
let config = null;     // working copy, edited by the form
let dirty = false;
let gal = { source: "all", page: 1, entries: [], pages: 1 };

/* ---------- transport ---------- */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      "X-FurScraper-Token": TOKEN,
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(opts.headers || {}),
    },
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* not every response has a body */ }
  if (!res.ok && !data) throw new Error(`${res.status} ${res.statusText}`);
  return { ok: res.ok, status: res.status, data };
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/* ---------- config binding ---------- */

const lines = (s) => s.split("\n").map((l) => l.trim()).filter(Boolean);

function fillForm(cfg) {
  const m = cfg.modules;
  $("e621-enabled").checked = !!m.e621.enabled;
  $("e621-username").value = m.e621.username || "";
  $("e621-apikey").value = m.e621.api_key || "";
  $("e621-searches").value = (m.e621.searches || []).join("\n");

  $("fa_artists-enabled").checked = !!m.fa_artists.enabled;
  $("fa_artists-list").value = (m.fa_artists.artists || []).join("\n");
  $("fa_watchlist-enabled").checked = !!m.fa_watchlist.enabled;
  $("fa_search-enabled").checked = !!m.fa_search.enabled;
  $("fa_search-list").value = (m.fa_search.queries || []).join("\n");

  $("cookie-a").value = cfg.fa_auth.cookie_a || "";
  $("cookie-b").value = cfg.fa_auth.cookie_b || "";
  $("interval").value = cfg.interval_minutes;
  $("ui-port").value = cfg.ui_port || 47821;
  $("keep-ui").checked = cfg.keep_ui_running !== false;
  $("output-dir").value = cfg.output_dir || "";
  $("blacklist").value = (cfg.blacklist || []).join("\n");
  $("lan-enabled").checked = !!cfg.lan_enabled;
  $("hub-name").value = cfg.hub_name || "tsidia";

  document.querySelectorAll(".source").forEach(syncSourceCard);
  syncNetCard();
}

function readForm() {
  return {
    output_dir: $("output-dir").value.trim(),
    interval_minutes: parseInt($("interval").value, 10) || 0,
    ui_port: parseInt($("ui-port").value, 10) || 47821,
    keep_ui_running: $("keep-ui").checked,
    lan_enabled: $("lan-enabled").checked,
    hub_name: $("hub-name").value.trim().toLowerCase() || "tsidia",
    blacklist: lines($("blacklist").value),
    fa_auth: {
      cookie_a: $("cookie-a").value.trim(),
      cookie_b: $("cookie-b").value.trim(),
    },
    modules: {
      e621: {
        enabled: $("e621-enabled").checked,
        username: $("e621-username").value.trim(),
        api_key: $("e621-apikey").value.trim(),
        searches: lines($("e621-searches").value),
      },
      fa_artists: {
        enabled: $("fa_artists-enabled").checked,
        artists: lines($("fa_artists-list").value),
      },
      fa_watchlist: { enabled: $("fa_watchlist-enabled").checked },
      fa_search: {
        enabled: $("fa_search-enabled").checked,
        queries: lines($("fa_search-list").value),
      },
    },
  };
}

function syncSourceCard(card) {
  const key = card.dataset.module;
  card.classList.toggle("off", !$(`${key}-enabled`).checked);
}

function syncNetCard() {
  const on = $("lan-enabled").checked;
  $("net-card").classList.toggle("off", !on);
  $("net-body").hidden = !on;
}

function markDirty() {
  dirty = true;
  $("savebar").classList.add("show");
  $("save-msg").textContent = "";
  $("save-msg").className = "";
}

function clearDirty() {
  dirty = false;
  $("savebar").classList.remove("show");
}

/* ---------- rendering ---------- */

function renderDashboard() {
  const cfg = config;
  const enabled = Object.values(cfg.modules).filter((m) => m.enabled).length;
  $("stat-files").textContent = state.file_count.toLocaleString();
  $("stat-sources").textContent = `${enabled} / 4`;
  $("stat-interval").textContent = formatInterval(cfg.interval_minutes);

  const r = state.run;
  $("stat-last").textContent = r.finished_at ? relTime(r.finished_at) : "never";
  $("run-status").textContent = r.running ? r.message : (r.error ? "Last run failed." : r.message);
  $("run-progress").hidden = !r.running;
  $("run-btn").disabled = r.running;
  $("run-btn").textContent = r.running ? "Running…" : "Run now";

  const out = $("run-result");
  out.innerHTML = "";
  if (r.error) {
    out.appendChild(resultLine("bad", r.error));
  } else if (r.result) {
    const { new: n, errors, failures } = r.result;
    out.appendChild(resultLine("ok",
      `${n} new file${n === 1 ? "" : "s"}${errors ? `, ${errors} download error${errors === 1 ? "" : "s"}` : ""}.`));
    (failures || []).forEach((f) => out.appendChild(resultLine("bad", f)));
  }

  const pill = $("sched-pill");
  pill.textContent = state.scheduled ? state.schedule_detail : "Not scheduled";
  pill.classList.toggle("on", state.scheduled);
  $("sched-detail").textContent = state.scheduled
    ? `${state.schedule_detail} The scheduled task only runs while you are logged in.`
    : "Not scheduled yet. Save to register the background task.";

  $("path-config").textContent = state.paths.config;
  $("path-log").textContent = state.paths.log;

  // Only overwrite the URL box when it is not focused, so a mid-copy refresh
  // does not yank the selection away.
  if (document.activeElement !== $("app-url")) $("app-url").value = state.url;
  const warn = $("port-warning");
  warn.hidden = !state.port_warning;
  if (state.port_warning) warn.textContent = state.port_warning;

  $("keep-ui-hint").textContent = state.ui_always_on
    ? "Active. FurScraper comes up when you sign in, so the bookmark works without launching anything."
    : $("keep-ui").checked
      ? "Takes effect once you save, which registers the log-in trigger."
      : "Off. The bookmark only works while FurScraper is open, and it closes with the tab.";

  renderNetwork();
}

const FIREWALL_TEXT = {
  pending: "Waiting for Windows Firewall permission. Watch for the prompt.",
  missing: "Windows Firewall has no rule for FurScraper yet, so other devices " +
           "will be blocked. Click 'Allow through firewall'.",
  declined: "The firewall prompt was declined, so other devices are blocked. " +
            "Click 'Allow through firewall' to try again.",
  failed: "The firewall rule could not be added. Click 'Allow through firewall' " +
          "to retry, or add an inbound rule for FurScraper yourself.",
  unknown: "Could not check Windows Firewall. If other devices cannot connect, " +
           "click 'Allow through firewall'.",
};

function renderNetwork() {
  const net = state.network || { lan_enabled: false };
  const saved = net.lan_enabled;
  const dl = $("net-addresses");
  const hint = $("net-hint");
  const warn = $("net-warning");

  hint.hidden = !($("lan-enabled").checked && !saved);
  hint.textContent = hint.hidden ? "" : "Save to turn on network access.";
  $("pair-btn").disabled = !saved;
  $("forget-btn").disabled = !saved;

  dl.innerHTML = "";
  const warnings = [];
  if (saved) {
    const add = (dt, dd) => {
      const t = document.createElement("dt");
      t.textContent = dt;
      const d = document.createElement("dd");
      d.textContent = dd;
      dl.append(t, d);
    };
    if (net.hub_url) add("Tsidia address", net.hub_url);
    (net.direct_urls || []).forEach((u) => add("Direct address", u));
    add("Paired devices", String(net.paired || 0));

    if (net.firewall && net.firewall !== "ok") warnings.push(FIREWALL_TEXT[net.firewall] || "");
    if (net.hub && net.hub.warning) warnings.push(net.hub.warning);
    if (net.hub && net.hub.role === "leader" && net.hub.mdns === false) {
      warnings.push("The .local name could not be advertised on this network; " +
                    "use the direct address or the QR code instead.");
    }
  }
  warn.hidden = !warnings.length;
  warn.textContent = warnings.join(" ");
  $("fw-btn").hidden = !(saved && net.firewall && !["ok", "pending"].includes(net.firewall));
  if (!saved) $("pair-box").hidden = true;
}

async function pairDevice() {
  const { ok, data } = await api("api/pair/new", { method: "POST" });
  if (!ok) {
    ((data && data.problems) || ["Could not start pairing."]).forEach((p) => toast(p, "bad"));
    return;
  }
  $("pair-code").textContent = data.code;
  $("pair-url").textContent = data.friendly_url || data.qr_url || "";
  $("pair-box").hidden = false;
  $("qr-box").hidden = false;
  $("qr-box").innerHTML = "";
  try {
    const res = await fetch("api/pair/qr.svg", {
      headers: { "X-FurScraper-Token": TOKEN },
    });
    if (res.ok) $("qr-box").innerHTML = await res.text();
    else $("qr-box").hidden = true;
  } catch (_) {
    $("qr-box").hidden = true;
  }
}

function resultLine(kind, text) {
  const el = document.createElement("div");
  el.className = `result-line ${kind}`;
  el.textContent = text;
  return el;
}

function formatInterval(mins) {
  if (!mins) return "–";
  if (mins < 60) return `${mins} min`;
  if (mins % 60 === 0) return `${mins / 60} hr`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function relTime(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/* ---------- gallery ---------- */

async function loadGallery(page = 1) {
  const { data } = await api(`api/gallery?source=${gal.source}&page=${page}`);
  gal.entries = data.entries;
  gal.page = data.page;
  gal.pages = data.pages;

  const grid = $("grid");
  grid.innerHTML = "";
  $("gal-empty").hidden = data.total > 0;
  $("gal-count").textContent = data.total
    ? `${data.total.toLocaleString()} file${data.total === 1 ? "" : "s"}`
    : "";

  data.entries.forEach((e, i) => {
    const tile = document.createElement("div");
    tile.className = "tile";
    tile.onclick = () => openLightbox(i);
    const media = e.is_video
      ? `<video src="media/${encodeURIComponent(e.name)}?k=${TOKEN}" preload="metadata" muted></video>
         <span class="vid">VIDEO</span>`
      : `<img loading="lazy" src="media/${encodeURIComponent(e.name)}?k=${TOKEN}" alt="">`;
    tile.innerHTML = `${media}<span class="badge ${e.source}">${e.source === "fa" ? "FA" : "e621"}</span>`;
    grid.appendChild(tile);
  });

  const pager = $("pager");
  pager.innerHTML = "";
  if (data.pages > 1) {
    for (const p of pageNumbers(data.page, data.pages)) {
      const b = document.createElement("button");
      b.textContent = p === "…" ? "…" : p;
      if (p === data.page) b.className = "curr";
      if (p !== "…") b.onclick = () => loadGallery(p);
      else b.disabled = true;
      pager.appendChild(b);
    }
  }
}

function pageNumbers(cur, total) {
  const out = new Set([1, total, cur, cur - 1, cur + 1]);
  const list = [...out].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
  const withGaps = [];
  list.forEach((n, i) => {
    if (i && n - list[i - 1] > 1) withGaps.push("…");
    withGaps.push(n);
  });
  return withGaps;
}

let lbIndex = -1;

function openLightbox(i) {
  const e = gal.entries[i];
  if (!e) return;
  lbIndex = i;
  const src = `media/${encodeURIComponent(e.name)}?k=${TOKEN}`;
  $("lb-stage").innerHTML = e.is_video
    ? `<video src="${src}" controls autoplay loop></video>`
    : `<img src="${src}" alt="">`;
  $("lb-info").innerHTML =
    `<span>${e.source === "fa" ? "FurAffinity" : "e621"} · ${e.id}</span>` +
    `<a href="${e.post_url}" target="_blank" rel="noreferrer noopener">View original</a>`;
  $("lightbox").hidden = false;
}

function closeLightbox() {
  $("lightbox").hidden = true;
  $("lb-stage").innerHTML = "";  // stop any playing video
  lbIndex = -1;
}

function stepLightbox(d) {
  if (lbIndex < 0) return;
  const next = lbIndex + d;
  if (next >= 0 && next < gal.entries.length) openLightbox(next);
}

/* ---------- actions ---------- */

async function refreshState() {
  const { data } = await api("api/state");
  state = data;
  if (!config) {
    config = data.config;
    fillForm(config);
  } else if (!dirty) {
    config = data.config;
  }
  renderDashboard();
}

async function save() {
  const cfg = readForm();
  const prevPort = state && state.config ? state.config.ui_port : null;
  const { ok, data } = await api("api/config", {
    method: "POST",
    body: JSON.stringify(cfg),
  });
  if (!ok) {
    $("save-msg").textContent = (data.problems || ["Could not save."]).join("\n");
    $("save-msg").className = "bad";
    return false;
  }
  config = cfg;
  clearDirty();
  (data.problems || []).forEach((p) => toast(p, "bad"));
  if (!data.problems || !data.problems.length) toast("Saved and scheduled.", "good");

  // The server rebinds immediately on a port change. If this page is on the
  // old port, follow it; behind the hub the address does not change at all.
  if (prevPort && cfg.ui_port !== prevPort && String(location.port) === String(prevPort)) {
    toast(`Moving to port ${cfg.ui_port}…`);
    setTimeout(() => {
      location.href = `${location.protocol}//${location.hostname}:${cfg.ui_port}/?k=${TOKEN}`;
    }, 1500);
    return true;
  }
  await refreshState().catch(() => {});  // a LAN toggle rebinds; poll recovers
  return true;
}

async function runNow() {
  const cfg = readForm();
  const { ok, data } = await api("api/run", {
    method: "POST",
    body: JSON.stringify(cfg),
  });
  if (!ok) {
    (data.problems || ["Could not start."]).forEach((p) => toast(p, "bad"));
    return;
  }
  config = cfg;
  clearDirty();
  await refreshState();
}

async function refreshLog() {
  const { data } = await api("api/log?tail=300");
  const el = $("log");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.textContent = data.lines.join("\n") || "Nothing logged yet.";
  if ($("log-follow").checked && atBottom) el.scrollTop = el.scrollHeight;
}

/* ---------- wiring ---------- */

function showView(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "gallery") loadGallery(gal.page);
}

function wire() {
  $("tabs").addEventListener("click", (e) => {
    if (e.target.dataset.view) showView(e.target.dataset.view);
  });

  $("copy-url").onclick = async () => {
    const el = $("app-url");
    try {
      await navigator.clipboard.writeText(el.value);
    } catch (_) {
      el.select();                 // clipboard API needs a secure context
      document.execCommand("copy");
    }
    toast("Address copied. Paste it into a bookmark.", "good");
  };

  document.querySelectorAll("input, textarea").forEach((el) => {
    if (el.id === "log-follow" || el.id === "app-url") return;
    el.addEventListener("input", markDirty);
    el.addEventListener("change", markDirty);
  });
  document.querySelectorAll(".switch input").forEach((el) => {
    el.addEventListener("change", () => {
      const card = el.closest(".source");
      if (card) syncSourceCard(card);
    });
  });
  $("lan-enabled").addEventListener("change", () => { syncNetCard(); renderNetwork(); });

  $("pair-btn").onclick = pairDevice;
  $("forget-btn").onclick = async () => {
    if (!confirm("Forget every paired device? They will need to pair again.")) return;
    const { ok, data } = await api("api/network/forget", { method: "POST" });
    if (ok) toast(`Forgot ${data.forgotten} device${data.forgotten === 1 ? "" : "s"}.`, "good");
    $("pair-box").hidden = true;
    await refreshState().catch(() => {});
  };
  $("fw-btn").onclick = async () => {
    await api("api/network/firewall", { method: "POST" });
    toast("Watch for the Windows permission prompt.");
  };

  $("save-btn").onclick = save;
  $("revert-btn").onclick = () => { fillForm(config); clearDirty(); };
  $("run-btn").onclick = runNow;

  $("gal-filter").addEventListener("click", (e) => {
    if (!e.target.dataset.source) return;
    document.querySelectorAll(".seg").forEach((s) => s.classList.remove("active"));
    e.target.classList.add("active");
    gal.source = e.target.dataset.source;
    loadGallery(1);
  });

  $("open-output").onclick = () => api("api/open-folder", {
    method: "POST", body: JSON.stringify({ what: "output" }),
  }).then(({ ok, data }) => { if (!ok) toast(data.problems[0], "bad"); });

  $("open-appdata").onclick = () => api("api/open-folder", {
    method: "POST", body: JSON.stringify({ what: "appdata" }),
  });

  $("quit-btn").onclick = async () => {
    if (dirty && !confirm("You have unsaved changes. Quit anyway?")) return;
    await api("api/quit", { method: "POST" });
    document.body.innerHTML =
      '<div class="boot">FurScraper has closed. You can close this tab.</div>';
  };

  $("lb-close").onclick = closeLightbox;
  $("lb-prev").onclick = () => stepLightbox(-1);
  $("lb-next").onclick = () => stepLightbox(1);
  $("lightbox").addEventListener("click", (e) => {
    if (e.target.id === "lightbox") closeLightbox();
  });

  document.addEventListener("keydown", (e) => {
    if ($("lightbox").hidden) return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft") stepLightbox(-1);
    if (e.key === "ArrowRight") stepLightbox(1);
  });

  window.addEventListener("beforeunload", (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });
}

/* ---------- boot ---------- */

(async function boot() {
  try {
    await refreshState();
  } catch (err) {
    $("boot").textContent = "Could not reach FurScraper. Close this tab and reopen the app.";
    return;
  }

  // The server already granted a session cookie for whatever the query
  // carried (key or pairing code), so drop it from the address bar: what is
  // left is the clean, bookmarkable URL.
  const params = new URLSearchParams(location.search);
  if (params.has("k") || params.has("token") || params.has("pair")) {
    history.replaceState(null, "", location.pathname);
  }

  wire();
  await refreshLog();
  $("boot").hidden = true;
  $("app").hidden = false;

  // The tab is the app window: this keepalive is what tells the process we are
  // still here. Stop sending it and the app shuts itself down.
  setInterval(() => api("api/heartbeat", { method: "POST" }).catch(() => {}), 15000);

  setInterval(async () => {
    const wasRunning = state && state.run.running;
    await refreshState().catch(() => {});
    if (state.run.running || wasRunning) await refreshLog().catch(() => {});
  }, 2000);
})();
