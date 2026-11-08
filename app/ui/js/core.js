/* PlaylistTransfer: lo común a todas las pantallas. Habla con Python por window.pywebview.api
   (ver app/api.py). Todos los textos salen de app/locales/<idioma>.json con t("clave"): en ningún
   archivo de la UI va texto suelto. Los archivos son scripts comunes (no módulos: la página se abre
   como archivo local) y comparten estas funciones; el orden de carga está en index.html. */
"use strict";

const SYMBOL = { ok: "✓", doubt: "?", miss: "✗", dup: "≈", now: "…", removed: "−" };
const CLASS = { ok: "ok", dudoso: "doubt", dudoso_omitido: "doubt", no_encontrado: "miss", ya_estaba: "dup", quitado: "removed" };
// Los servicios salen de api.services() (app/connectors/registry.py) al arrancar
let NAMES = {};          // {tidal: "Tidal", ...} en el orden en que se muestran
let USABLE = [];         // los que ya se pueden usar
let SETUP = {};          // los que piden configurar algo antes (Spotify): {sections, client_id}

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const mmss = s => (s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "");

/* ---------------------------------------------------------------- idioma */

let LANG = "es";
let STR = {};

/** Texto en el idioma actual. Con {n: número} usa la variante _one / _other si existe. */
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
  from: null, to: null,  // los dos primeros servicios disponibles
  playlists: {},         // cache por servicio
  busy: false,
  transfer: null,        // {job, total, result, ...}
  scan: null,
  dupService: null,
};

/* ---------------------------------------------------------------- utilidades */

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

/** Sigue una tarea en segundo plano hasta que termina. onEvent recibe cada evento en vivo. */
async function follow(job, onEvent = () => {}) {
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

/* ---------------------------------------------------------------- navegación */

function go(id) {
  $$(".screen").forEach(s => s.classList.toggle("active", s.id === id));
  $$("nav button").forEach(b => (b.dataset.go === id ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  $("main").scrollTop = 0;
  if (id === "transferir" && !state.transfer) loadPickers();
  if (id === "repetidos") renderDupControls();
  if (id === "historial") renderHistory();
  if (id === "conexiones") renderConnections();
  if (id === "ajustes") renderSettings();
}
document.addEventListener("click", e => { const el = e.target.closest("[data-go]"); if (el && !el.disabled) go(el.dataset.go); });

/* ---------------------------------------------------------------- estado de los servicios */

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
  // Para pasar música hacen falta dos servicios. Con dos o más no se reclama nada: cada uno usa los que
  // quiere, y quien no usa uno de ellos no tiene por qué verlo reclamado para siempre
  $("#home-connect").hidden = USABLE.filter(connected).length >= 2;
}

/* ---------------------------------------------------------------- textos con formato y pasos numerados */

/** Texto del diccionario con los nombres entre «» (o “”) en negrita: campos y botones que hay que encontrar. */
const richText = (key, vars) => esc(t(key, vars)).replace(/([«“])([^»”]+)([»”])/g, "$1<b>$2</b>$3");

/** Pasos numerados (1, 2, 3...) con el mismo formato que los de configuración. */
const stepsHTML = (keys, vars) => `<ol class="setup-steps">${keys.map((k, i) =>
  `<li><span class="step-n num">${i + 1}</span><div class="step-body"><p>${richText(k, vars)}</p></div></li>`).join("")}</ol>`;

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* sin permiso: plan B */ }
  const area = document.createElement("textarea");
  area.value = text; area.setAttribute("readonly", ""); area.className = "offscreen";
  document.body.appendChild(area); area.select();
  const ok = document.execCommand("copy"); area.remove();
  return ok;
}
