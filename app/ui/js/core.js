/* PlaylistTransfer: what every screen shares. It talks to Python through window.pywebview.api
   (see app/api/). Every text comes from app/locales/<language>.json with t("key"): no UI file
   holds loose text. The files are plain scripts (not modules: the page is opened as a local
   file) and share these functions; the load order is in index.html. */
"use strict";

const SYMBOL = { ok: "✓", doubt: "?", miss: "✗", dup: "≈", rep: "×2", now: "…", removed: "−" };
const CLASS = { ok: "ok", dudoso: "doubt", dudoso_omitido: "doubt", no_encontrado: "miss", ya_estaba: "dup", repetido: "rep", quitado: "removed" };
// The services come from api.services() (app/connectors/registry.py) at startup
let NAMES = {};          // {tidal: "Tidal", ...} in the order they are shown
let USABLE = [];         // the ones that can be used now
let SETUP = {};          // the ones that need something set up first (Spotify): {sections, client_id}

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const mmss = s => (s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "");

/* ---------------------------------------------------------------- language */

let LANG = "es";
let STR = {};

/** Text in the current language. With {n: number} it uses the _one / _other variant if there is one. */
function t(key, vars = {}) {
  let k = key;
  if (typeof vars.n === "number") {
    const plural = `${key}_${vars.n === 1 ? "one" : "other"}`;
    if (plural in STR) k = plural;
  }
  const text = STR[k] ?? key;
  return text.replace(/\{(\w+)\}/g, (m, v) => (v in vars ? vars[v] : m));
}
const songs = n => t("songs", { n });
/** How many songs a playlist has. Without the figure it says so, because with no number it looks empty. */
const countLabel = n => (n == null ? t("playlists.count_unknown") : songs(n));
const listAnd = items => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} ${t("list.and")} ${items[items.length - 1]}`);
const dateLocale = () => (LANG === "es" ? "es-AR" : "en-US");

function applyI18n(root = document) {
  $$("[data-i18n]", root).forEach(el => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-aria]", root).forEach(el => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
  document.documentElement.lang = LANG;
  document.documentElement.classList.remove("i18n-pending");
}

let api = null;
const state = {
  status: {},            // tidal: "connected" | "expired" | "disconnected"
  from: null, to: null,  // the first two available services
  playlists: {},         // cache per service
  busy: false,
  login: null,           // sign-in in progress: {key, job, again, stopping, waitHTML}
  transfer: null,        // {job, total, result, ...}
  scan: null,
  dupService: null,
};

/* ---------------------------------------------------------------- utilities */

function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove("show"), 3200);
}

async function call(method, ...args) {
  try {
    const r = await api[method](...args);
    if (r && typeof r === "object" && !Array.isArray(r) && r.error) { toast(r.error); return null; }
    return r;
  } catch (e) {
    toast(t("common.error", { msg: e.message || e }));
    return null;
  }
}

/* ---------------------------------------------------------------- the services' waits: always announced */

/** "Spotify asked for a pause: continuing in 2:40." (or "it didn't answer properly: trying again in 5 s.") */
function waitText(e, left) {
  const time = left < 60 ? `${left} s` : mmss(left);
  return t(e.why === "retry" ? "wait.retry" : "wait.rate", { service: e.service, time });
}

/** A countdown in `el` while a service asks to wait. */
function showWaiting(el, e) {
  stopWaiting(el);
  const end = Date.now() + e.seconds * 1000;
  const tick = () => {
    const left = Math.max(0, Math.ceil((end - Date.now()) / 1000));
    el.textContent = waitText(e, left);
    if (!left) stopWaiting(el);
  };
  el.hidden = false; tick(); el._wait = setInterval(tick, 1000);
}
function stopWaiting(el) { clearInterval(el._wait); el._wait = null; }

/** What a task without its own progress screen (adding later, removing) does if a service asks to wait. */
const toastWaits = e => { if (e.type === "waiting") toast(waitText(e, e.seconds)); };

/** Follows a background task until it finishes. onEvent receives each event live. */
async function follow(job, onEvent = toastWaits) {
  let since = 0;
  setBusy(true);
  try {
    for (;;) {
      const r = await api.poll(job, since);
      for (const e of r.events) onEvent(e);
      since = r.next;
      if (r.state !== "running") return r;
      await sleep(180);
    }
  } finally { setBusy(false); }
}

function setBusy(b) {
  state.busy = b;
  $$("[data-needs-idle]").forEach(el => { el.disabled = b || el.dataset.blocked === "1"; });
}

const connected = key => state.status[key] === "connected";

/* ---------------------------------------------------------------- every screen saying the same thing */

let playlistsVersion = 0;   // an answer requested before the lists were forgotten is no longer stored

function forgetPlaylists(key = null) {
  playlistsVersion++;
  if (key) delete state.playlists[key]; else state.playlists = {};
}

/** Something changed in a service (songs were added, a playlist was created, it connected or disconnected):
 *  what was stored is forgotten and the screen on view is updated, without losing what was chosen. */
function serviceChanged(key) {
  forgetPlaylists(key);
  const screen = $(".screen.active")?.id;
  if (screen === "repetidos") renderDupControls();
  if (screen === "transferir" && !state.transfer) loadPickers();
  if (screen === "historial") renderHistory();
}

/* ---------------------------------------------------------------- navigation */

function go(id) {
  $$(".screen").forEach(s => s.classList.toggle("active", s.id === id));
  $$("nav button").forEach(b => (b.dataset.go === id ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  $("main").scrollTop = 0;
  // When going in to pick, the playlists are asked for again: they may have changed in the service (new likes).
  // What is stored only avoids repeating the request within the same screen (switching source and destination)
  if (id === "repetidos" || (id === "transferir" && !state.transfer)) forgetPlaylists();
  if (id === "transferir" && !state.transfer) loadPickers();
  if (id === "repetidos") renderDupControls();
  if (id === "historial") renderHistory();
  if (id === "conexiones") renderConnections();
  if (id === "ajustes") renderSettings();
}
document.addEventListener("click", e => { const el = e.target.closest("[data-go]"); if (el && !el.disabled) go(el.dataset.go); });

/* ---------------------------------------------------------------- the services' status */

async function refreshStatus() {
  $("#chips").innerHTML = Object.values(NAMES).map(n => `<span class="chip checking"><span class="dot"></span>${n} <span class="state">…</span></span>`).join("");
  const list = await call("status") || [];
  for (const s of list) state.status[s.key] = s.status;
  $("#chips").innerHTML = list.map(s => {
    const [cls, label] = { connected: ["ok", "✓"], expired: ["warn", `<span class="state">${esc(t("status.expired"))}</span>`],
      disconnected: ["off", `<span class="state">${esc(t("status.disconnected"))}</span>`],
      setup: ["off", `<span class="state">${esc(t("status.setup"))}</span>`],
      soon: ["soon", `<span class="state">${esc(t("status.soon"))}</span>`] }[s.status];
    const target = s.status === "soon" ? "" : `data-go="conexiones"`;
    return `<button class="chip ${cls}" ${target} title="${esc(s.name)}"><span class="dot"></span>${esc(s.name)} ${label}</button>`;
  }).join("");
  // Moving music takes two services. With two or more nothing is asked for: each person uses the ones
  // they want, and someone who doesn't use one of them shouldn't see it asked for forever
  const missing = USABLE.filter(connected).length < 2;
  $("#home-connect").hidden = !missing;
  $("#home-transfer").classList.toggle("primary", !missing);       // a single main button
  $("#home-transfer").classList.toggle("ghost", missing);
}

/* ---------------------------------------------------------------- formatted texts and numbered steps */

/** Dictionary text with the names in «» (or “”) in bold: fields and buttons that have to be found. */
const richText = (key, vars) => esc(t(key, vars)).replace(/([«“])([^»”]+)([»”])/g, "$1<b>$2</b>$3");

/** Numbered steps (1, 2, 3...) with the same format as the setup ones. */
const stepsHTML = (keys, vars) => `<ol class="setup-steps">${keys.map((k, i) =>
  `<li><span class="step-n num">${i + 1}</span><div class="step-body"><p>${richText(k, vars)}</p></div></li>`).join("")}</ol>`;

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* no permission: plan B */ }
  const area = document.createElement("textarea");
  area.value = text; area.setAttribute("readonly", ""); area.className = "offscreen";
  document.body.appendChild(area); area.select();
  const ok = document.execCommand("copy"); area.remove();
  return ok;
}
