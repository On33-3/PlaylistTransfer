/* PlaylistTransfer: lógica de la UI. Habla con Python por window.pywebview.api (ver app/api.py).
   Todos los textos salen de app/locales/<idioma>.json con t("clave"): acá no va texto suelto. */
"use strict";

const SYMBOL = { ok: "✓", doubt: "?", miss: "✗", dup: "≈", now: "…", removed: "−" };
const CLASS = { ok: "ok", dudoso: "doubt", dudoso_omitido: "doubt", no_encontrado: "miss", ya_estaba: "dup", quitado: "removed" };
// Los servicios salen de api.services() (app/connectors/registry.py) al arrancar
let NAMES = {};          // {tidal: "Tidal", ...} en el orden en que se muestran
let USABLE = [];         // los que ya se pueden usar
let SETUP = {};          // los que piden configurar algo antes (Spotify): {dashboard, redirect_uri, client_id}

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
    const target = s.status === "soon" ? "" : `data-go="ajustes"`;
    return `<button class="chip ${cls}" ${target} title="${esc(s.name)}"><span class="dot"></span>${esc(s.name)} ${label}</button>`;
  }).join("");
  // Lo que nunca se configuró (Spotify pide Premium y una app propia) no se reclama en Inicio
  const missing = USABLE.filter(k => !connected(k) && state.status[k] !== "setup").map(k => NAMES[k]);
  $("#home-connect").hidden = missing.length === 0;
  $("#home-connect-text").textContent = missing.length ? t("home.connect_missing", { services: listAnd(missing) }) : "";
}

/* ---------------------------------------------------------------- inicio */

function fitSelect(sel) {
  const probe = document.createElement("span");
  const cs = getComputedStyle(sel);
  probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${cs.font};letter-spacing:${cs.letterSpacing}`;
  probe.textContent = sel.options[sel.selectedIndex]?.text || "";
  document.body.appendChild(probe);
  // Ancho del nombre + los rellenos (el de la derecha deja lugar a la flecha ▾)
  const pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  sel.style.width = `${Math.ceil(probe.getBoundingClientRect().width + pad + 2)}px`;
  probe.remove();
}

function setupRoute() {
  const opts = sel => Object.entries(NAMES).map(([k, n]) => `<option value="${k}" ${k === sel ? "selected" : ""} ${USABLE.includes(k) ? "" : "disabled"}>${n}</option>`).join("");
  $("#from").innerHTML = opts(state.from); $("#to").innerHTML = opts(state.to);
  const onChange = which => () => {
    const other = which === "from" ? "to" : "from", prev = state[which];
    state[which] = $("#" + which).value;
    // Si quedan iguales, se dan vuelta (con más de dos servicios, el otro toma el que había)
    if (state.from === state.to) { state[other] = prev; $("#" + other).value = state[other]; }
    fitSelect($("#from")); fitSelect($("#to"));
    state.transfer = null; resetTransferView();
  };
  $("#from").addEventListener("change", onChange("from"));
  $("#to").addEventListener("change", onChange("to"));
  // ⇅ da vuelta la dirección (el destino pasa a ser el origen)
  $("#swap").addEventListener("click", () => {
    [state.from, state.to] = [state.to, state.from];
    $("#from").value = state.from; $("#to").value = state.to;
    updateSwapTitle();
    fitSelect($("#from")); fitSelect($("#to"));
    state.transfer = null; resetTransferView();
  });
  $("#from").addEventListener("change", updateSwapTitle);
  $("#to").addEventListener("change", updateSwapTitle);
  updateSwapTitle();
  fitSelect($("#from")); fitSelect($("#to"));
}

function updateSwapTitle() {
  $("#swap").title = t("home.swap_title", { src: NAMES[state.to], dst: NAMES[state.from] });
}

function opTitle(op) {
  return op.kind === "dedupe" ? t("op.dedupe_title", { name: op.source_name })
    : t("op.transfer_title", { src: op.source_name, dst: op.target_name });
}
function opRoute(op) {
  return op.kind === "dedupe" ? op.source_label : t("op.route", { src: op.source_label, dst: op.target_label });
}
function opSub(op) {
  if (op.kind === "dedupe") return t("op.removed", { n: op.removed });
  // Los dudosos ya están entre los agregados: se aclaran entre paréntesis, no se suman aparte
  const added = t("op.added", { n: op.added });
  const parts = [op.doubtful ? t("op.added_with_doubts", { added, doubtful: t("op.doubtful", { n: op.doubtful }) }) : added];
  if (op.already) parts.push(t("op.already", { n: op.already }));
  if (op.skipped) parts.push(t("op.skipped", { n: op.skipped }));
  if (op.not_found) parts.push(t("op.not_found", { n: op.not_found }));
  return parts.join(", ");
}
function opDate(op) {
  const d = new Date(op.created_at);
  return isNaN(d) ? op.created_at : d.toLocaleString(dateLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
const emptyHistory = () => `<li class="empty"><b>${esc(t("history.empty_title"))}</b> ${esc(t("history.empty_text"))} <button class="btn ghost" data-go="transferir">${esc(t("history.empty_btn"))}</button></li>`;
/* ---- mosaicos: un cuadrado por tema, con su color; al pasar el mouse dice qué tema es ---- */

const LEGEND = [["ok", "ok", "--mint"], ["doubt", "doubt", "--peach"], ["miss", "miss", "--rose"],
                ["dup", "dup", "--sage"], ["removed", "removed", "--lav"]];
/** El nombre del estado. En una prueba sin cambios, lo encontrado "se agregaría" (no se agregó nada). */
const statusLabel = (s, dry = false) => {
  if (s === "dudoso_omitido") return t(dry ? "legend.skipped_dry" : "legend.skipped");
  const key = CLASS[s] || "ok";
  return t(dry && key === "ok" ? "legend.ok_dry" : `legend.${key}`);
};

/** Qué significa cada color (una sola vez por pantalla, arriba de la lista). En el historial el "?"
 *  puede ser agregado o no (según la casilla de esa transferencia): cada cuadrado dice cuál. */
function legendHTML() {
  return LEGEND.map(([key, cls, color]) =>
    `<span><i style="background:var(${color})"></i>${SYMBOL[cls]} ${esc(t(key === "doubt" ? "legend.doubt_any" : `legend.${key}`))}</span>`).join("");
}

function tileTitle(status, song, dry = false) {
  const label = statusLabel(status, dry);
  return song?.[0] ? t("tile.tooltip", { title: song[0], artist: song[1] || "?", status: label }) : label;
}

/** El mosaico chico de una operación del historial (con "+N" si tiene más temas de los que se dibujan). */
function mosaicHTML(op) {
  const statuses = op.tiles || [], songsOf = op.tile_songs || [];
  const tiles = statuses.map((s, i) => `<i class="${CLASS[s] || "ok"}" title="${esc(tileTitle(s, songsOf[i]))}"></i>`).join("");
  const extra = (op.tiles_total || 0) - statuses.length;
  const more = extra > 0 ? `<span class="more" title="${esc(t("tiles.more_title", { n: extra }))}">${esc(t("tiles.more", { n: extra }))}</span>` : "";
  return `<span class="mosaic-wrap"><span class="mosaic small" role="img" aria-label="${esc(opSub(op))}">${tiles}</span>${more}</span>`;
}

function showLegend(id, visible) {
  const el = $(id);
  el.hidden = !visible;
  if (visible) el.innerHTML = legendHTML();
}

async function renderHomeHistory() {
  const ops = await call("history", 3) || [];
  showLegend("#home-legend", ops.length > 0);
  $("#home-history").innerHTML = ops.length ? ops.map(op => `
    <li><div><div class="what" title="${esc(opTitle(op))}">${esc(opTitle(op))}</div><div class="when">${esc(opRoute(op))}, ${esc(opDate(op))}</div></div>
    ${mosaicHTML(op)}</li>`).join("") : emptyHistory();
}

/* ---------------------------------------------------------------- transferir: elegir */

async function getPlaylists(key, force = false) {
  if (!force && state.playlists[key]) return state.playlists[key];
  const r = await call("playlists", key);
  if (Array.isArray(r)) state.playlists[key] = r;
  return Array.isArray(r) ? r : null;
}

async function loadPickers() {
  const { from, to } = state;
  $("#route-lede").textContent = t("transfer.lede", { src: NAMES[from], dst: NAMES[to] });
  $("#src-title").textContent = t("transfer.src_title", { service: NAMES[from] });
  $("#dst-title").textContent = t("transfer.dst_title", { service: NAMES[to] });
  const missing = [from, to].filter(k => !connected(k));
  $("#pick-connect").hidden = missing.length === 0;
  $("#pickers").hidden = missing.length > 0;
  if (missing.length) {
    $("#pick-connect-text").textContent = t("transfer.connect_needed", { services: listAnd(missing.map(k => NAMES[k])) });
    updateGo(); return;
  }
  $("#src-list").innerHTML = $("#dst-list").innerHTML = `<li class="loading">${esc(t("transfer.loading"))}</li>`;
  const [src, dst] = await Promise.all([getPlaylists(from), getPlaylists(to)]);
  const count = c => (c == null ? "" : songs(c));
  // El nombre va en su propio span: si es muy largo se corta con "…" y se ve completo al pasar el mouse
  const item = (p, type) => `<li data-filter="${esc(p.name.toLowerCase())}"><label title="${esc(p.name)}"><input type="${type}" name="${type === "radio" ? "src" : "dst"}" value="${esc(p.id)}" data-name="${esc(p.name)}" data-count="${p.count ?? ""}"> <span class="name">${esc(p.name)}</span> <span class="count num">${count(p.count)}</span></label></li>`;
  $("#src-list").innerHTML = !src ? `<li class="loading error">${esc(t("transfer.read_error"))}</li>`
    : src.length ? src.map(p => item(p, "radio")).join("")
    : `<li class="loading">${esc(t("transfer.no_playlists", { service: NAMES[from] }))}</li>`;
  // "Crear una playlist nueva" va primero (y queda fija arriba): con cientos de playlists no se pierde al final
  $("#dst-list").innerHTML = `<li class="new"><label><input type="checkbox" id="dst-new"> ${esc(t("transfer.new"))}</label><input type="text" id="dst-new-name" aria-label="${esc(t("transfer.new_aria"))}" placeholder="${esc(t("transfer.new_placeholder"))}" hidden></li>`
    + (dst || []).map(p => item(p, "checkbox")).join("");
  // Si alguna de las dos listas es larga, las dos llevan buscador: así las cajas quedan alineadas
  const withFilter = Math.max((src || []).length, (dst || []).length) > FILTER_FROM;
  addFilter("#src-list", withFilter, t("transfer.src_title", { service: NAMES[from] }));
  addFilter("#dst-list", withFilter, t("transfer.dst_title", { service: NAMES[to] }));
  updateGo();
}

const FILTER_FROM = 8;   // con más playlists que esto, aparece el buscador

/** Buscador arriba de una lista de playlists. Oculta las que no coinciden (no las destilda). */
function addFilter(listSel, show, listName) {
  const list = $(listSel), picker = list.closest(".picker");
  $(".filter", picker)?.remove();
  if (!show) return;
  const input = document.createElement("input");
  input.type = "search"; input.className = "filter";
  input.placeholder = t("transfer.filter"); input.setAttribute("aria-label", t("transfer.filter_aria", { list: listName }));
  list.before(input);
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    let shown = 0;
    $$("li[data-filter]", list).forEach(li => { li.hidden = !!q && !li.dataset.filter.includes(q); shown += li.hidden ? 0 : 1; });
    $(".no-match", list)?.remove();
    if (!shown) list.insertAdjacentHTML("beforeend", `<li class="no-match">${esc(t("transfer.no_match"))}</li>`);
  });
}

function selection() {
  const src = $("input[name=src]:checked");
  const targets = $$("input[name=dst]:checked").map(i => ({ id: i.value, name: i.dataset.name }));
  if ($("#dst-new")?.checked) targets.push({ id: null, name: $("#dst-new-name").value.trim() || src?.dataset.name || t("transfer.new_default") });
  return { src, targets };
}

function updateGo() {
  const { src, targets } = selection();
  const ok = !!src && targets.length > 0;
  for (const b of [$("#go"), $("#dry")]) { b.dataset.blocked = ok ? "0" : "1"; b.disabled = !ok || state.busy; }
  const n = src && src.dataset.count ? Number(src.dataset.count) : null;
  $("#go").textContent = n != null ? t("transfer.go_n", { songs: songs(n) }) : t("transfer.go");
}

$("#pick").addEventListener("change", e => {
  if (e.target.id === "dst-new") {
    const name = $("#dst-new-name"); name.hidden = !e.target.checked;
    if (e.target.checked) { name.value ||= $("input[name=src]:checked")?.dataset.name || ""; name.focus(); }
  }
  if (e.target.name === "src" && $("#dst-new")?.checked && !$("#dst-new-name").dataset.touched) $("#dst-new-name").value = e.target.dataset.name;
  updateGo();
});
$("#pick").addEventListener("input", e => { if (e.target.id === "dst-new-name") e.target.dataset.touched = "1"; updateGo(); });

/* ---------------------------------------------------------------- transferir: en vivo */

function setStep(n) { [...$("#steps").children].forEach((li, i) => { li.className = i < n ? "done" : (i === n ? "current" : ""); }); }

function resetTransferView() {
  $("#run").hidden = true; $("#pick").hidden = false; $("#done").classList.remove("show"); setStep(0);
  if ($("#transferir").classList.contains("active")) loadPickers();
}

function trackHTML(e) {
  const cls = CLASS[e.status] || "now";
  const s = e.song, m = e.match;
  let note = "";
  if (e.status === "dudoso_omitido" && m) {
    note = t(state.transfer?.dryRun ? "transfer.note_skipped_dry" : "transfer.note_skipped", { title: m.title, artist: m.artist || "?" });
  }
  if (e.status === "no_encontrado") note = e.alternatives?.length ? t("transfer.note_miss_alts", { artist: s.artist }) : t("transfer.note_miss");
  if (e.as) note = t("transfer.note_already_as", { title: e.as.title });
  else if (e.status === "ya_estaba") note = t("transfer.note_already");
  let alts = e.status === "no_encontrado" && e.alternatives?.length
    ? `<div class="alts">${e.alternatives.map((a, k) => `<button data-alt="${e.n}:${k}" disabled title="${esc(t("transfer.alt_title"))}">+ ${esc(a.artist || "?")}</button>`).join("")}</div>` : "";
  if (e.status === "dudoso_omitido" && m && !state.transfer?.dryRun) {   // el usuario decide: "+ Agregar igual"
    alts = `<div class="alts"><button data-add-doubtful="${e.n}" disabled title="${esc(t("transfer.add_anyway_title", { title: m.title, artist: m.artist || "?" }))}">${esc(t("transfer.add_anyway"))}</button></div>`;
  }
  const score = e.score != null && !["ya_estaba", "no_encontrado"].includes(e.status) ? Number(e.score).toFixed(2) : "";
  return `<span class="badge ${cls}">${SYMBOL[cls]}</span><span><span class="t">${esc(s.title)}</span> <span class="a">${esc(s.artist)}</span>${note ? `<span class="note">. ${esc(note)}</span>` : ""}${alts}</span><span class="score num">${score}</span>`;
}

async function startTransfer(dryRun) {
  const { src, targets } = selection();
  if (!src || !targets.length) return;
  const r = await call("start_transfer", state.from, state.to, src.value, targets, dryRun);
  if (!r) return;
  const tr = state.transfer = { job: r.job, dryRun, total: 0, n: { ok: 0, doubt: 0, miss: 0, dup: 0 }, rows: {}, tracks: {} };
  $("#add-all").hidden = true;
  $("#pick").hidden = true; $("#run").hidden = false; $("#done").classList.remove("show"); setStep(1);
  $("#mosaic").innerHTML = ""; $("#tracks").innerHTML = ""; $("#count").textContent = "0";
  $("#count-of").textContent = t("transfer.songs"); $("#run-actions").hidden = false;
  $("#run-route").innerHTML = `${esc(src.dataset.name)} <span class="muted">${esc(t("transfer.route_sep"))}</span> ${esc(targets.map(x => x.name).join(", "))}`;
  for (const k in tr.n) $("#n-" + k).textContent = 0;
  // La leyenda dice lo que va a pasar: "Agregado" / "Se agregaría" (prueba). Los "?" nunca se agregan solos
  const dry = dryRun ? "_dry" : "";
  $('#run .legend [data-i18n="transfer.legend_ok"]').textContent = t(`transfer.legend_ok${dry}`);
  $('#run .legend [data-i18n="transfer.legend_skipped"]').textContent = t(`transfer.legend_skipped${dry}`);

  const done = await follow(tr.job, e => onTransferEvent(tr, e));
  $("#run-actions").hidden = true; setStep(2);
  if (done.state === "done") {
    showTransferResult(tr, done.result);
  } else {
    const cancelled = done.state === "cancelled";
    $("#done-title").textContent = t(cancelled ? "transfer.cancelled_title" : "transfer.failed_title");
    $("#done-detail").textContent = cancelled ? t("transfer.cancelled_text") : (done.error || "");
    $("#open-dest").hidden = true; $("#done").classList.add("show");
  }
  renderHomeHistory();
}

function onTransferEvent(tr, e) {
  if (e.type === "source") {
    tr.total = e.total; $("#count-of").textContent = t("transfer.of", { songs: songs(e.total) });
    $("#mosaic").innerHTML = Array.from({ length: e.total }, (_, i) => `<i data-n="${i + 1}"></i>`).join("");
    // Con muchos temas, cuadrados más chicos: el mosaico no tapa la pantalla
    $("#mosaic").classList.toggle("dense", e.total > 90 && e.total <= 300);
    $("#mosaic").classList.toggle("denser", e.total > 300);
  } else if (e.type === "searching") {
    const tile = $("#mosaic").children[e.n - 1]; if (tile) tile.className = "now";
    const li = document.createElement("li");
    li.innerHTML = `<span class="badge now">…</span><span><span class="t">${esc(e.song.title)}</span> <span class="a">${esc(t("transfer.searching", { service: NAMES[state.to] }))}</span></span><span></span>`;
    $("#tracks").prepend(li); tr.rows[e.n] = li;
  } else if (e.type === "track") {
    tr.tracks[e.n] = e;
    const cls = CLASS[e.status]; tr.n[cls]++;
    setBigTile(e.n, e.status, e.song);
    if (tr.rows[e.n]) tr.rows[e.n].innerHTML = trackHTML(e);
    $("#count").textContent = e.n; for (const k in tr.n) $("#n-" + k).textContent = tr.n[k];
  } else if (e.type === "already") {
    const prev = tr.tracks[e.n]; if (!prev || prev.status === "ya_estaba") return;
    tr.n[CLASS[prev.status]]--; tr.n.dup++;
    const upd = { ...prev, status: "ya_estaba", as: e.as };
    tr.tracks[e.n] = upd;
    setBigTile(e.n, "ya_estaba", prev.song);
    if (tr.rows[e.n]) tr.rows[e.n].innerHTML = trackHTML(upd);
    for (const k in tr.n) $("#n-" + k).textContent = tr.n[k];
  }
}

/** Un cuadrado del mosaico grande: color, qué tema es, y se puede tocar para ir a ese tema en la lista. */
function setBigTile(n, status, song) {
  const tile = $("#mosaic").children[n - 1]; if (!tile) return;
  tile.className = `${CLASS[status]} jump`;
  tile.title = `${tileTitle(status, [song.title, song.artist], state.transfer?.dryRun)}. ${t("tile.jump")}`;
}

$("#mosaic").addEventListener("click", e => {
  const tile = e.target.closest("i.jump"); if (!tile || !state.transfer) return;
  const row = state.transfer.rows[Number(tile.dataset.n)]; if (!row) return;
  row.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");   // reinicia el resaltado
});

function showTransferResult(tr, res) {
  tr.result = res;
  const lines = res.targets.map(x => {
    const extras = [];
    if (x.already) extras.push(t("transfer.extra_already", { n: x.already }));
    if (x.versions) extras.push(t("transfer.extra_versions", { n: x.versions }));
    return t(res.dry_run ? "transfer.line_dry" : "transfer.line", {
      name: x.name, new: x.created ? t("transfer.new_mark") : "", songs: songs(x.added),
      extra: extras.length ? `; ${extras.join(", ")}` : "",
    });
  });
  $("#done-title").textContent = t(res.dry_run ? "transfer.dry_title" : "transfer.done_title");
  const s = res.summary, notes = [];
  if (s.skipped) notes.push(t(res.dry_run ? "transfer.notes_skipped_dry" : "transfer.notes_skipped", { n: s.skipped }));
  if (s.not_found) notes.push(t("transfer.notes_not_found", { n: s.not_found }));
  $("#done-detail").innerHTML = lines.map(esc).join("<br>") + (notes.length ? `<br>${esc(notes.join(" "))}` : "");
  const first = res.targets.find(x => x.url);
  $("#open-dest").hidden = !first || res.dry_run;
  $("#open-dest").textContent = t("transfer.open", { service: NAMES[state.to] });
  $("#open-dest").onclick = () => first && call("open_url", first.url);
  $("#done").classList.add("show");
  if (!res.dry_run) $$("#tracks [data-alt], #tracks [data-add-doubtful]").forEach(b => { b.disabled = false; });
  updateAddAll();
}

/** "Agregar los N que pueden ser otra versión": solo si quedan "?" sin agregar en una transferencia real. */
function updateAddAll() {
  const pending = $$("#tracks [data-add-doubtful]:not(.done)").map(b => Number(b.dataset.addDoubtful));
  const btn = $("#add-all");
  btn.hidden = pending.length === 0 || !state.transfer?.result || state.transfer.result.dry_run;
  btn.textContent = t("transfer.add_all", { n: pending.length });
  btn.dataset.ns = pending.join(",");
}

async function addDoubtful(ns) {
  const buttons = ns.map(n => $(`#tracks [data-add-doubtful="${n}"]`)).filter(Boolean);
  buttons.forEach(b => { b.disabled = true; });
  const r = await call("add_doubtful", state.transfer.result.transfer, ns);
  if (!r) { buttons.forEach(b => { b.disabled = false; }); return; }
  const done = await follow(r.job);
  if (done.state !== "done") { buttons.forEach(b => { b.disabled = false; }); toast(done.error || t("transfer.alt_error")); return; }
  const added = new Set(done.result.added_ns);
  for (const n of done.result.ns) {
    const b = $(`#tracks [data-add-doubtful="${n}"]`); if (!b) continue;
    b.classList.add("done");
    b.textContent = t(added.has(n) ? "transfer.added_anyway" : "transfer.add_anyway_already");
    const prev = state.transfer.tracks[n];
    if (prev && added.has(n)) setBigTile(n, "dudoso", prev.song);
  }
  toast(added.size ? t("transfer.add_doubtful_toast", { n: added.size }) : t("transfer.add_doubtful_already"));
  updateAddAll();
  renderHomeHistory();
}

$("#add-all").addEventListener("click", () => {
  const ns = ($("#add-all").dataset.ns || "").split(",").filter(Boolean).map(Number);
  if (ns.length) addDoubtful(ns);
});

$("#tracks").addEventListener("click", async e => {
  const one = e.target.closest("[data-add-doubtful]");
  if (one && !one.disabled && !one.classList.contains("done") && state.transfer?.result) { addDoubtful([Number(one.dataset.addDoubtful)]); return; }
  const b = e.target.closest("[data-alt]"); if (!b || b.disabled || !state.transfer?.result) return;
  const [n, k] = b.dataset.alt.split(":").map(Number);
  b.disabled = true;
  const r = await call("add_alternative", state.transfer.result.transfer, n, k);
  if (!r) { b.disabled = false; return; }
  const done = await follow(r.job);
  if (done.state === "done") {
    const a = done.result.added;
    b.textContent = t("transfer.alt_added", { artist: a.artist });
    toast(t("transfer.alt_toast", { title: a.title, artist: a.artist }));
    renderHomeHistory();
  } else { b.disabled = false; toast(done.error || t("transfer.alt_error")); }
});

$("#go").addEventListener("click", () => startTransfer(false));
$("#dry").addEventListener("click", () => startTransfer(true));
$("#cancel").addEventListener("click", () => {
  if (state.transfer) { api.cancel(state.transfer.job); $("#cancel").disabled = true; $("#cancel").textContent = t("transfer.cancelling"); }
});

$("#again").addEventListener("click", () => {
  state.transfer = null;
  $("#cancel").disabled = false; $("#cancel").textContent = t("transfer.cancel");
  delete state.playlists[state.to]; resetTransferView();
});

/* ---------------------------------------------------------------- quitar repetidos */

async function renderDupControls() {
  const avail = USABLE.filter(connected);
  $("#dup-connect").hidden = avail.length > 0;
  $("#dup-controls").hidden = avail.length === 0;
  if (!avail.length) return;
  if (!avail.includes(state.dupService)) state.dupService = avail[0];
  $("#dup-svc").innerHTML = USABLE.map(k => `<button data-svc="${k}" aria-pressed="${k === state.dupService}" ${connected(k) ? "" : "disabled"}>${NAMES[k]}</button>`).join("");
  $("#dup-playlist").innerHTML = `<option>${esc(t("common.loading"))}</option>`;
  const pls = await getPlaylists(state.dupService) || [];
  $("#dup-playlist").innerHTML = pls.length ? pls.map(p => `<option value="${esc(p.id)}">${esc(p.name)}${p.count != null ? ` (${songs(p.count)})` : ""}</option>`).join("")
    : `<option value="">${esc(t("dedupe.no_playlists"))}</option>`;
  const scan = $("#dup-scan"); scan.dataset.needsIdle = ""; scan.dataset.blocked = pls.length ? "0" : "1"; scan.disabled = !pls.length || state.busy;
}

$("#dup-svc").addEventListener("click", e => {
  const b = e.target.closest("[data-svc]"); if (!b || b.disabled) return;
  state.dupService = b.dataset.svc; clearDups(); renderDupControls();
});
$("#dup-playlist").addEventListener("change", clearDups);

function clearDups() { state.scan = null; $("#dups").innerHTML = ""; $("#dup-summary").hidden = true; $("#dup-actions").hidden = true; }

async function scanDups() {
  const pid = $("#dup-playlist").value; if (!pid) return;
  clearDups();
  $("#dup-summary").hidden = false; $("#dup-summary").innerHTML = `<span class="muted">${esc(t("dedupe.reading"))}</span>`;
  const r = await call("scan_duplicates", state.dupService, pid);
  if (!r) { $("#dup-summary").hidden = true; return; }
  const done = await follow(r.job);
  if (done.state !== "done") { $("#dup-summary").innerHTML = `<span class="error">${esc(done.error || t("dedupe.read_error"))}</span>`; return; }
  const res = state.scan = done.result;
  if (!res.duplicates.length) {
    $("#dup-summary").innerHTML = `<b>${esc(t("dedupe.clean", { name: res.name }))}</b> <span class="muted">${esc(t("dedupe.checked", { songs: songs(res.total) }))}</span>`;
    return;
  }
  const versions = res.duplicates.filter(d => d.kind !== "misma").length;
  const hints = (versions ? `${t("dedupe.versions_hint")} ` : "") + t("dedupe.uncheck_hint");
  $("#dup-summary").innerHTML = `<b>${esc(t("dedupe.found", { n: res.duplicates.length, name: res.name }))}</b> <span class="muted">${esc(hints)}</span>`;
  const meta = s => [s.artist, s.album, mmss(s.duration)].filter(Boolean).map(esc).join(", ");
  $("#dups").innerHTML = res.duplicates.map(d => `
    <li><input type="checkbox" ${d.kind === "misma" ? "checked" : ""} aria-label="${esc(t("dedupe.remove_aria", { title: d.song.title }))}" data-i="${d.i}">
      <div class="side remove"><span class="tag">${esc(t("dedupe.tag_remove"))}</span>${d.kind !== "misma" ? `<span class="tag version">${esc(t("dedupe.tag_version"))}</span>` : ""}<div class="t">${esc(d.song.title)}</div><div class="meta">${meta(d.song)}</div></div>
      <div class="side keep"><span class="tag">${esc(t("dedupe.tag_keep"))}</span><div class="t">${esc(d.kept.title)}</div><div class="meta">${meta(d.kept)}</div></div></li>`).join("");
  $("#dup-actions").hidden = false; updateRemove();
}

const checkedDups = () => $$("#dups input:checked").map(i => Number(i.dataset.i));
function updateRemove() {
  const c = checkedDups().length, label = t("dedupe.remove_n", { n: c });
  $("#remove").textContent = label; $("#confirm-yes").textContent = label;
  $("#remove").dataset.blocked = c ? "0" : "1"; $("#remove").disabled = !c || state.busy;
  $("#c-title").textContent = t("dedupe.confirm_title", { action: label, name: state.scan?.name || "" });
  // Spotify quita todas las copias de una canción: la que queda vuelve a agregarse en su lugar
  const readd = checkedDups().filter(i => state.scan?.duplicates[i]?.readd).length;
  $("#c-readd").hidden = !readd;
  $("#c-readd").textContent = readd ? t("dedupe.confirm_readd", { n: readd, service: NAMES[state.dupService] }) : "";
}
$("#dups").addEventListener("change", updateRemove);
$("#dup-scan").addEventListener("click", scanDups);
$("#remove").addEventListener("click", () => { if (checkedDups().length) $("#confirm").showModal(); });
$("#confirm-no").addEventListener("click", () => $("#confirm").close());
$("#confirm-yes").addEventListener("click", async () => {
  const selected = checkedDups(); $("#confirm").close();
  if (!selected.length || !state.scan) return;
  const r = await call("remove_duplicates", state.scan.scan, selected, true);   // true: el usuario confirmó en el diálogo
  if (!r) return;
  const done = await follow(r.job);
  if (done.state === "done") {
    toast(t("dedupe.removed_toast", { songs: songs(done.result.removed), name: done.result.name }));
    delete state.playlists[state.dupService]; renderHomeHistory(); await renderDupControls(); scanDups();
  } else toast(done.error || t("dedupe.remove_error"));
});

/* ---------------------------------------------------------------- historial */

async function renderHistory() {
  const kind = state.histKind || "";
  const ops = await call("history", null, kind || null) || [];
  $("#hist-actions").hidden = ops.length === 0;
  $("#export").title = t("history.download_title");
  showLegend("#hist-legend", ops.length > 0);
  $("#hist").innerHTML = ops.length ? ops.map(op => `
    <li data-op="${op.id}"><button class="row" aria-expanded="false">
      <span class="date">${esc(opDate(op))}</span>
      <span><div class="what" title="${esc(opTitle(op))}">${esc(opTitle(op))}</div><div class="sub">${esc(opRoute(op))}. ${esc(opSub(op))}</div></span>
      ${mosaicHTML(op)}</button>
      <div class="detail"><ul><li class="muted">${esc(t("common.loading"))}</li></ul></div></li>`).join("")
    : (kind ? `<li class="empty">${esc(t(kind === "dedupe" ? "history.empty_dedupe" : "history.empty_transfer"))}</li>` : emptyHistory());
}
$("#hist").addEventListener("click", async e => {
  const b = e.target.closest("button.row"); if (!b) return;
  const li = b.parentElement, open = li.classList.toggle("open"); b.setAttribute("aria-expanded", open);
  if (!open || li.dataset.loaded) return;
  const tracks = await call("history_detail", Number(li.dataset.op)) || [];
  li.dataset.loaded = "1";
  $(".detail ul", li).innerHTML = tracks.length ? tracks.map(x => {
    const cls = CLASS[x.status] || "ok";
    const to = x.match_title && x.status !== "no_encontrado" ? ` <span class="muted">→ ${esc(x.match_title)}${x.match_artist ? `, ${esc(x.match_artist)}` : ""}</span>` : "";
    return `<li><span class="badge ${cls}">${SYMBOL[cls]}</span><span><b>${esc(x.title)}</b> <span class="muted">${esc(x.artist)}</span>${to}</span></li>`;
  }).join("") : `<li class="muted">${esc(t("history.no_detail"))}</li>`;
});

$("#hist-filter").addEventListener("click", e => {
  const b = e.target.closest("[data-kind]"); if (!b) return;
  state.histKind = b.dataset.kind;
  $$("#hist-filter button").forEach(x => x.setAttribute("aria-pressed", x === b));
  renderHistory();
});
$("#export").addEventListener("click", async () => {
  const r = await call("export_history", state.histKind || null);
  if (!r || r.cancelled) return;
  $("#open-export").hidden = false;
  $("#export-note").hidden = false;
  $("#export-note").textContent = t("history.download_note", { n: r.operations, path: r.path });
  toast(t("history.download_toast", { name: r.name }));
});
$("#open-export").addEventListener("click", () => call("open_export"));

/* ---------------------------------------------------------------- ajustes */

async function renderSettings() {
  $("#svc-list").innerHTML = USABLE.map(k => {
    const st = state.status[k];
    const label = { connected: t("settings.connected"), expired: t("settings.expired"), disconnected: t("settings.disconnected"),
                    setup: t("settings.setup_needed") }[st] || "…";
    let btns = st === "connected"
      ? `<button class="btn ghost" data-disconnect="${k}" data-needs-idle>${esc(t("settings.disconnect"))}</button>`
      : st === "setup" ? `<button class="btn primary" data-setup="${k}" aria-expanded="false">${esc(t("settings.setup"))}</button>`
      : `<button class="btn primary" data-connect="${k}" data-needs-idle>${esc(t(st === "expired" ? "settings.reconnect" : "settings.connect"))}</button>`;
    if (SETUP[k] && st !== "setup") btns = `<button class="btn ghost" data-setup="${k}" aria-expanded="false">${esc(t("settings.setup_again"))}</button>${btns}`;
    return `<div class="svc"><span><b>${NAMES[k]}</b> <span class="muted">${esc(label)}</span><div class="login-wait" id="wait-${k}" hidden></div></span><span class="actions-inline">${btns}</span></div>`
      + (SETUP[k] ? setupPanelHTML(k) : "");
  }).join("") + Object.keys(NAMES).filter(k => !USABLE.includes(k)).map(k =>
    `<div class="svc"><span><b>${esc(NAMES[k])}</b> <span class="muted">${esc(t("settings.soon"))}</span></span><button class="btn ghost" disabled>${esc(t("settings.connect"))}</button></div>`).join("");
  setBusy(state.busy);

  const langs = await call("languages");
  if (langs) {
    $("#language-list").innerHTML = langs.available.map(l => `
      <label class="opt"><input type="radio" name="lang" value="${esc(l.code)}" ${l.code === langs.current ? "checked" : ""}> <span lang="${esc(l.code)}">${esc(l.name)}</span></label>`).join("");
  }
  const b = await call("browsers");
  if (b) {
    const chosen = b.chosen || b.suggested;
    $("#browser-list").innerHTML = b.browsers.length ? b.browsers.map(x => `
      <label class="opt"><input type="radio" name="br" value="${esc(x.exe)}" ${x.exe === chosen ? "checked" : ""}>
      <span>${esc(x.name)}${x.default ? ` <span class="muted">${esc(t("settings.default"))}</span>` : ""}</span></label>`).join("")
      : `<p class="muted">${esc(t("settings.no_browsers"))}</p>`;
  }
  const info = await call("info");
  if (info) {
    $("#data-path").textContent = info.data_dir;
    $("#licenses").innerHTML = info.licenses.map(l => `<li>${esc(l.name)} <span class="muted">(${esc(l.license)})</span></li>`).join("");
  }
  renderThresholds(await call("settings"));
}

function renderThresholds(st) {
  if (!st) return;
  const lim = st.limits, f = x => Number(x).toFixed(2);
  $("#th-ok").value = f(st.score_ok);
  $("#th-min").value = f(st.score_min);
  $("#th-ok").min = lim.ok_min; $("#th-ok").max = lim.ok_max; $("#th-min").min = lim.min_min;
  $("#th-help").textContent = t("settings.th_help", { ok: f(st.defaults.score_ok), min: f(st.defaults.score_min),
    ok_min: f(lim.ok_min), ok_max: f(lim.ok_max), min_min: f(lim.min_min) });
  $("#th-error").hidden = true;
}
async function saveThresholds(ok, min) {
  $("#th-error").hidden = true;
  try {
    const r = await api.save_thresholds(ok, min);
    if (r.error) { $("#th-error").textContent = r.error; $("#th-error").hidden = false; return; }
    toast(t("settings.th_saved", { ok: r.score_ok.toFixed(2), min: r.score_min.toFixed(2) }));
    renderThresholds(await call("settings"));
  } catch (e) { toast(t("common.error", { msg: e.message || e })); }
}
$("#th-save").addEventListener("click", () => saveThresholds($("#th-ok").value, $("#th-min").value));
$("#th-reset").addEventListener("click", async () => {
  const st = await call("settings");
  if (st) saveThresholds(st.defaults.score_ok, st.defaults.score_min);
});

$("#language-list").addEventListener("change", async e => {
  if (e.target.name !== "lang") return;
  const r = await call("set_language", e.target.value);
  if (!r) { renderSettings(); return; }          // no se pudo (por ejemplo, hay una tarea en curso)
  // Se recarga la ventana: así todo, incluso lo que ya estaba dibujado, sale en el idioma nuevo
  try { sessionStorage.setItem("pt-screen", "ajustes"); } catch (_) { /* sin almacenamiento: vuelve a Inicio */ }
  location.reload();
});

$("#browser-list").addEventListener("change", async e => {
  if (e.target.name !== "br") return;
  const r = await call("choose_browser", e.target.value);
  if (r) toast(t("settings.browser_toast", { name: r.name }));
});

/** Los pasos para usar Spotify con una app propia (lo exige Spotify desde 2026), en la misma fila. */
function setupPanelHTML(k) {
  const s = SETUP[k];
  return `<div class="setup-panel" id="setup-${k}" hidden>
    <ol class="setup-steps">
      <li><b>${esc(t("settings.sp_step1_title"))}</b> ${esc(t("settings.sp_step1"))}</li>
      <li><b>${esc(t("settings.sp_step2_title"))}</b> ${esc(t("settings.sp_step2"))}
        <div class="setup-row"><code class="uri" id="redirect-${k}">${esc(s.redirect_uri)}</code>
          <button class="btn ghost" data-copy="${k}">${esc(t("settings.copy"))}</button>
          <button class="btn ghost" data-dashboard="${k}">${esc(t("settings.sp_open_dashboard"))}</button></div></li>
      <li><b>${esc(t("settings.sp_step3_title"))}</b> <label for="client-${k}">${esc(t("settings.sp_step3"))}</label>
        <div class="setup-row"><input type="text" id="client-${k}" class="client-id" spellcheck="false" autocomplete="off" maxlength="64"
          placeholder="${esc(t("settings.sp_client_placeholder"))}" value="${esc(s.client_id || "")}">
          <button class="btn primary" data-save-client="${k}" data-needs-idle>${esc(t("settings.sp_save"))}</button></div>
        <p class="error" id="client-error-${k}" hidden></p></li>
    </ol>
    <p class="muted setup-note">${esc(t("settings.sp_note"))}</p></div>`;
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* sin permiso: plan B */ }
  const area = document.createElement("textarea");
  area.value = text; area.setAttribute("readonly", ""); area.className = "offscreen";
  document.body.appendChild(area); area.select();
  const ok = document.execCommand("copy"); area.remove();
  return ok;
}

async function saveClientId(key) {
  const input = $("#client-" + key), err = $("#client-error-" + key);
  err.hidden = true;
  const r = await api.set_client_id(key, input.value);
  if (r.error) { err.textContent = r.error; err.hidden = false; input.focus(); return; }
  SETUP[key].client_id = r.client_id;
  await refreshStatus(); await renderSettings();
  connectService(key);                 // ya está configurado: sigue con el inicio de sesión
}

async function connectService(key) {
  const c = $(`[data-connect="${key}"]`); if (!c) return;
  const r = await call("connect", key); if (!r) return;
  const wait = $("#wait-" + key); wait.hidden = false; wait.textContent = t("settings.opening");
  // Mientras espera el login se puede cancelar (si el usuario cerró el navegador o cambió de idea)
  const stop = document.createElement("button");
  stop.className = "btn ghost"; stop.id = "cancel-login"; stop.textContent = t("settings.cancel_login");
  stop.addEventListener("click", () => { stop.disabled = true; api.cancel(r.job); });
  $(".actions-inline", c.closest(".svc")).append(stop);
  const done = await follow(r.job, ev => {
    if (ev.type === "login") wait.textContent = t("settings.login_wait", { browser: ev.browser, service: ev.service });
  });
  wait.hidden = true; stop.remove();
  if (done.state === "done") toast(t("settings.connected_toast", { service: NAMES[key] }));
  else if (done.state === "cancelled") toast(t("settings.login_cancelled"));
  else toast(done.error || t("settings.connect_error"));
  delete state.playlists[key];
  await refreshStatus(); renderSettings();
}

$("#svc-list").addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.matches("input.client-id")) saveClientId(e.target.id.replace("client-", ""));
});

$("#svc-list").addEventListener("click", async e => {
  const c = e.target.closest("[data-connect]"), d = e.target.closest("[data-disconnect]");
  const setup = e.target.closest("[data-setup]");
  if (setup) {
    const panel = $("#setup-" + setup.dataset.setup), open = panel.hidden;
    panel.hidden = !open; setup.setAttribute("aria-expanded", open);
    if (open) $("input", panel).focus();
  }
  const copy = e.target.closest("[data-copy]");
  if (copy && await copyText(SETUP[copy.dataset.copy].redirect_uri)) toast(t("settings.copied"));
  const dash = e.target.closest("[data-dashboard]");
  if (dash) call("open_url", SETUP[dash.dataset.dashboard].dashboard);
  const save = e.target.closest("[data-save-client]");
  if (save && !save.disabled) saveClientId(save.dataset.saveClient);
  if (c) connectService(c.dataset.connect);
  if (d) {
    const key = d.dataset.disconnect;
    if (await call("disconnect", key)) {
      toast(t("settings.disconnected_toast", { service: NAMES[key] }));
      delete state.playlists[key]; await refreshStatus(); renderSettings();
    }
  }
});

$("#open-data").addEventListener("click", () => call("open_data_dir"));

/* ---------------------------------------------------------------- arranque */

async function start() {
  api = window.pywebview.api;
  try {
    const i = await api.ui_strings();
    LANG = i.language; STR = i.strings;
  } catch (_) { /* sin textos: quedan los de respaldo del HTML */ }
  applyI18n();
  const services = await call("services") || [];
  NAMES = Object.fromEntries(services.map(s => [s.key, s.name]));
  USABLE = services.filter(s => s.available).map(s => s.key);
  SETUP = Object.fromEntries(services.filter(s => s.setup).map(s => [s.key, s.setup]));
  [state.from, state.to] = USABLE;
  ["#go", "#dry", "#remove", "#dup-scan"].forEach(s => { $(s).dataset.needsIdle = ""; });
  setupRoute();
  const info = await call("info");
  if (info) {
    $("#version").textContent = t("nav.version", { v: info.version });
    $("#about-version").textContent = t("settings.about_version", { v: info.version });
  }
  renderHomeHistory();
  await refreshStatus();
  let screen = null;
  try { screen = sessionStorage.getItem("pt-screen"); sessionStorage.removeItem("pt-screen"); } catch (_) { /* nada */ }
  if (screen) go(screen);
  api.ui_ready();
}

if (window.pywebview?.api) start();
else window.addEventListener("pywebviewready", start, { once: true });
