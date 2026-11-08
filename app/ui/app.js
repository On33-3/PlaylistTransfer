/* PlaylistTransfer: lógica de la UI. Habla con Python por window.pywebview.api (ver app/api.py). */
"use strict";

const SYMBOL = { ok: "✓", doubt: "?", miss: "✗", dup: "≈", now: "…", removed: "−" };
const CLASS = { ok: "ok", dudoso: "doubt", no_encontrado: "miss", ya_estaba: "dup", quitado: "removed" };
const NAMES = { tidal: "Tidal", ytmusic: "YouTube Music", spotify: "Spotify" };
const USABLE = ["tidal", "ytmusic"];

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const temas = n => (n === 1 ? "1 tema" : `${n} temas`);
const mmss = s => (s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "");

let api = null;
const state = {
  status: {},            // tidal: "connected" | "expired" | "disconnected"
  from: "tidal", to: "ytmusic",
  playlists: {},         // cache por servicio
  busy: false,
  transfer: null,        // {job, total, result, ...}
  scan: null,
  dupService: null,
};

/* ---------------------------------------------------------------- utilidades */

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

async function call(method, ...args) {
  try {
    const r = await api[method](...args);
    if (r && typeof r === "object" && !Array.isArray(r) && r.error) { toast(r.error); return null; }
    return r;
  } catch (e) {
    toast(`Algo salió mal: ${e.message || e}`);
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
document.addEventListener("click", e => { const t = e.target.closest("[data-go]"); if (t && !t.disabled) go(t.dataset.go); });

/* ---------------------------------------------------------------- estado de los servicios */

async function refreshStatus() {
  $("#chips").innerHTML = ["Tidal", "YouTube Music", "Spotify"].map(n => `<span class="chip checking"><span class="dot"></span>${n} <span class="state">…</span></span>`).join("");
  const list = await call("status") || [];
  for (const s of list) state.status[s.key] = s.status;
  $("#chips").innerHTML = list.map(s => {
    const [cls, label] = { connected: ["ok", "✓"], expired: ["warn", `<span class="state">⚠ venció</span>`],
      disconnected: ["off", `<span class="state">sin conectar</span>`], soon: ["soon", `<span class="state">próximamente</span>`] }[s.status];
    const go = s.status === "soon" ? "" : `data-go="ajustes"`;
    return `<button class="chip ${cls}" ${go} title="${esc(s.name)}"><span class="dot"></span>${esc(s.name)} ${label}</button>`;
  }).join("");
  const missing = USABLE.filter(k => !connected(k)).map(k => NAMES[k]);
  $("#home-connect").hidden = missing.length === 0;
  $("#home-connect-text").textContent = missing.length ? `Falta ${missing.join(" y ")}.` : "";
}

/* ---------------------------------------------------------------- inicio */

function fitSelect(sel) {
  const probe = document.createElement("span");
  const cs = getComputedStyle(sel);
  probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${cs.font};letter-spacing:${cs.letterSpacing}`;
  probe.textContent = sel.options[sel.selectedIndex]?.text || "";
  document.body.appendChild(probe);
  sel.style.width = `calc(${probe.getBoundingClientRect().width}px + .7em)`;
  probe.remove();
}

function setupRoute() {
  const opts = sel => Object.entries(NAMES).map(([k, n]) => `<option value="${k}" ${k === sel ? "selected" : ""} ${USABLE.includes(k) ? "" : "disabled"}>${n}</option>`).join("");
  $("#from").innerHTML = opts(state.from); $("#to").innerHTML = opts(state.to);
  const onChange = which => () => {
    state[which] = $("#" + which).value;
    const other = which === "from" ? "to" : "from";
    if (state.from === state.to) { state[other] = USABLE.find(k => k !== state[which]); $("#" + other).value = state[other]; }
    fitSelect($("#from")); fitSelect($("#to"));
    state.transfer = null; resetTransferView();
  };
  $("#from").addEventListener("change", onChange("from"));
  $("#to").addEventListener("change", onChange("to"));
  fitSelect($("#from")); fitSelect($("#to"));
}

function opTitle(op) {
  return op.kind === "dedupe" ? `Repetidos en ${op.source_name}` : `${op.source_name} a ${op.target_name}`;
}
function opRoute(op) {
  return op.kind === "dedupe" ? op.source_label : `${op.source_label} a ${op.target_label}`;
}
function opSub(op) {
  if (op.kind === "dedupe") return op.removed === 1 ? "1 quitado" : `${op.removed} quitados`;
  const parts = [`${op.added} ${op.added === 1 ? "agregado" : "agregados"}`];
  if (op.already) parts.push(`${op.already} ya ${op.already === 1 ? "estaba" : "estaban"}`);
  if (op.doubtful) parts.push(`${op.doubtful} para revisar`);
  if (op.not_found) parts.push(`${op.not_found} no ${op.not_found === 1 ? "encontrado" : "encontrados"}`);
  return parts.join(", ");
}
function opDate(op) {
  const d = new Date(op.created_at);
  return isNaN(d) ? op.created_at : d.toLocaleString("es-AR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
const EMPTY_HISTORY = `<li class="empty"><b>Todavía no hiciste transferencias.</b> Cuando hagas la primera, aparece acá. <button class="btn ghost" data-go="transferir">Transferir</button></li>`;

async function renderHomeHistory() {
  const ops = await call("history", 3) || [];
  $("#home-history").innerHTML = ops.length ? ops.map(op => `
    <li><div><div class="what">${esc(opTitle(op))}</div><div class="when">${esc(opRoute(op))}, ${esc(opDate(op))}</div></div>
    <div class="mosaic small" aria-hidden="true" title="${esc(opSub(op))}">${tilesHTML(op.tiles)}</div></li>`).join("") : EMPTY_HISTORY;
}

const tilesHTML = tiles => (tiles || []).map(s => `<i class="${CLASS[s] || "ok"}"></i>`).join("");

/* ---------------------------------------------------------------- transferir: elegir */

async function getPlaylists(key, force = false) {
  if (!force && state.playlists[key]) return state.playlists[key];
  const r = await call("playlists", key);
  if (Array.isArray(r)) state.playlists[key] = r;
  return Array.isArray(r) ? r : null;
}

async function loadPickers() {
  const { from, to } = state;
  $("#route-lede").textContent = `De ${NAMES[from]} a ${NAMES[to]}.`;
  $("#src-title").textContent = `Origen en ${NAMES[from]}`;
  $("#dst-title").textContent = `Destino en ${NAMES[to]}`;
  const missing = [from, to].filter(k => !connected(k));
  $("#pick-connect").hidden = missing.length === 0;
  $("#pickers").hidden = missing.length > 0;
  if (missing.length) {
    $("#pick-connect-text").textContent = `Para elegir playlists, conectá ${missing.map(k => NAMES[k]).join(" y ")}.`;
    updateGo(); return;
  }
  $("#src-list").innerHTML = $("#dst-list").innerHTML = `<li class="loading">Cargando tus playlists…</li>`;
  const [src, dst] = await Promise.all([getPlaylists(from), getPlaylists(to)]);
  const count = c => (c == null ? "" : temas(c));
  $("#src-list").innerHTML = !src ? `<li class="loading error">No pude leer tus playlists.</li>`
    : src.length ? src.map(p => `<li><label><input type="radio" name="src" value="${esc(p.id)}" data-name="${esc(p.name)}" data-count="${p.count ?? ""}"> ${esc(p.name)} <span class="count num">${count(p.count)}</span></label></li>`).join("")
    : `<li class="loading">No tenés playlists en ${NAMES[from]}.</li>`;
  $("#dst-list").innerHTML = (dst || []).map(p => `<li><label><input type="checkbox" name="dst" value="${esc(p.id)}" data-name="${esc(p.name)}"> ${esc(p.name)} <span class="count num">${count(p.count)}</span></label></li>`).join("")
    + `<li class="new"><label><input type="checkbox" id="dst-new"> Crear una playlist nueva</label><input type="text" id="dst-new-name" aria-label="Nombre de la playlist nueva" placeholder="Nombre" hidden></li>`;
  updateGo();
}

function selection() {
  const src = $("input[name=src]:checked");
  const targets = $$("input[name=dst]:checked").map(i => ({ id: i.value, name: i.dataset.name }));
  if ($("#dst-new")?.checked) targets.push({ id: null, name: $("#dst-new-name").value.trim() || src?.dataset.name || "Nueva playlist" });
  return { src, targets };
}

function updateGo() {
  const { src, targets } = selection();
  const ok = !!src && targets.length > 0;
  for (const b of [$("#go"), $("#dry")]) { b.dataset.blocked = ok ? "0" : "1"; b.disabled = !ok || state.busy; }
  const n = src && src.dataset.count ? Number(src.dataset.count) : null;
  $("#go").textContent = n != null ? `Transferir ${temas(n)}` : "Transferir";
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

function trackHTML(e, extra = "") {
  const cls = CLASS[e.status] || "now";
  const s = e.song, m = e.match;
  let note = "";
  if (e.status === "dudoso" && m) note = `. Puede ser otra versión: «${esc(m.title)}» de ${esc(m.artist || "?")}`;
  if (e.status === "no_encontrado") note = e.alternatives?.length
    ? `. No está la de ${esc(s.artist)}. Hay de otros artistas:` : ". No lo encontré";
  if (e.as) note = `. Ya estaba como «${esc(e.as.title)}»`;
  else if (e.status === "ya_estaba") note = ". Ya estaba";
  const alts = e.status === "no_encontrado" && e.alternatives?.length
    ? `<div class="alts">${e.alternatives.map((a, k) => `<button data-alt="${e.n}:${k}" disabled title="Agregar esta versión">+ ${esc(a.artist || "?")}</button>`).join("")}</div>` : "";
  const score = e.score != null && !["ya_estaba", "no_encontrado"].includes(e.status) ? Number(e.score).toFixed(2) : "";
  return `<span class="badge ${cls}">${SYMBOL[cls]}</span><span><span class="t">${esc(s.title)}</span> <span class="a">${esc(s.artist)}</span><span class="note">${note}</span>${alts}${extra}</span><span class="score num">${score}</span>`;
}

async function startTransfer(dryRun) {
  const { src, targets } = selection();
  if (!src || !targets.length) return;
  const r = await call("start_transfer", state.from, state.to, src.value, targets, dryRun);
  if (!r) return;
  const t = state.transfer = { job: r.job, dryRun, total: 0, n: { ok: 0, doubt: 0, miss: 0, dup: 0 }, rows: {}, tracks: {} };
  $("#pick").hidden = true; $("#run").hidden = false; $("#done").classList.remove("show"); setStep(1);
  $("#mosaic").innerHTML = ""; $("#tracks").innerHTML = ""; $("#count").textContent = "0";
  $("#count-of").textContent = "temas"; $("#run-actions").hidden = false;
  $("#run-route").innerHTML = `${esc(src.dataset.name)} <span class="muted">a</span> ${esc(targets.map(x => x.name).join(", "))}`;
  for (const k in t.n) $("#n-" + k).textContent = 0;

  const done = await follow(t.job, e => onTransferEvent(t, e));
  $("#run-actions").hidden = true; setStep(2);
  if (done.state === "done") {
    showTransferResult(t, done.result);
    if ($("#want-report").checked) await saveTransferReport(done.result.transfer);
  }
  else if (done.state === "cancelled") { $("#done-title").textContent = "Cancelaste la transferencia."; $("#done-detail").textContent = "No se modificó ninguna playlist."; $("#open-dest").hidden = true; $("#done").classList.add("show"); }
  else { $("#done-title").textContent = "No se pudo terminar."; $("#done-detail").textContent = done.error || ""; $("#open-dest").hidden = true; $("#done").classList.add("show"); }
  renderHomeHistory();
}

function onTransferEvent(t, e) {
  if (e.type === "source") {
    t.total = e.total; $("#count-of").textContent = `de ${temas(e.total)}`;
    $("#mosaic").innerHTML = Array.from({ length: e.total }, () => "<i></i>").join("");
  } else if (e.type === "searching") {
    const tile = $("#mosaic").children[e.n - 1]; if (tile) tile.className = "now";
    const li = document.createElement("li");
    li.innerHTML = `<span class="badge now">…</span><span><span class="t">${esc(e.song.title)}</span> <span class="a">buscando en ${NAMES[state.to]}</span></span><span></span>`;
    $("#tracks").prepend(li); t.rows[e.n] = li;
  } else if (e.type === "track") {
    t.tracks[e.n] = e;
    const cls = CLASS[e.status]; t.n[cls]++;
    const tile = $("#mosaic").children[e.n - 1]; if (tile) tile.className = cls;
    if (t.rows[e.n]) t.rows[e.n].innerHTML = trackHTML(e);
    $("#count").textContent = e.n; for (const k in t.n) $("#n-" + k).textContent = t.n[k];
  } else if (e.type === "already") {
    const prev = t.tracks[e.n]; if (!prev || prev.status === "ya_estaba") return;
    t.n[CLASS[prev.status]]--; t.n.dup++;
    const upd = { ...prev, status: "ya_estaba", as: e.as };
    t.tracks[e.n] = upd;
    const tile = $("#mosaic").children[e.n - 1]; if (tile) tile.className = "dup";
    if (t.rows[e.n]) t.rows[e.n].innerHTML = trackHTML(upd);
    for (const k in t.n) $("#n-" + k).textContent = t.n[k];
  }
}

function showTransferResult(t, res) {
  t.result = res;
  const lines = res.targets.map(x => {
    const extra = [];
    if (x.already) extra.push(`${x.already} ya ${x.already === 1 ? "estaba" : "estaban"}`);
    if (x.versions) extra.push(`${x.versions} ya ${x.versions === 1 ? "estaba" : "estaban"} en otra versión`);
    const verb = res.dry_run ? "se agregarían" : "agregaste";
    return `${res.dry_run ? "En" : "A"} «${x.name}»${x.created ? " (nueva)" : ""}: ${verb} ${temas(x.added)}${extra.length ? `; ${extra.join(", ")}` : ""}.`;
  });
  $("#done-title").textContent = res.dry_run ? "Prueba terminada: no se modificó nada." : "Listo.";
  const s = res.summary, notes = [];
  if (s.doubtful) notes.push(`Revisá ${s.doubtful === 1 ? "el tema marcado" : `los ${s.doubtful} temas marcados`} con ?: puede ser otra versión.`);
  if (s.not_found) notes.push(`${s.not_found === 1 ? "Un tema no se encontró" : `${s.not_found} temas no se encontraron`}.`);
  $("#done-detail").innerHTML = lines.map(esc).join("<br>") + (notes.length ? `<br>${esc(notes.join(" "))}` : "");
  const first = res.targets.find(x => x.url);
  $("#open-dest").hidden = !first || res.dry_run;
  $("#open-dest").textContent = `Abrir en ${NAMES[state.to]}`;
  $("#open-dest").onclick = () => first && call("open_url", first.url);
  $("#done").classList.add("show");
  if (!res.dry_run) $$("#tracks [data-alt]").forEach(b => { b.disabled = false; });
}

$("#tracks").addEventListener("click", async e => {
  const b = e.target.closest("[data-alt]"); if (!b || b.disabled || !state.transfer?.result) return;
  const [n, k] = b.dataset.alt.split(":").map(Number);
  b.disabled = true;
  const r = await call("add_alternative", state.transfer.result.transfer, n, k);
  if (!r) { b.disabled = false; return; }
  const done = await follow(r.job);
  if (done.state === "done") { b.textContent = `✓ Agregada la de ${done.result.added.artist}`; toast(`Agregaste «${done.result.added.title}» de ${done.result.added.artist}.`); renderHomeHistory(); }
  else { b.disabled = false; toast(done.error || "No se pudo agregar."); }
});

$("#go").addEventListener("click", () => startTransfer(false));
$("#dry").addEventListener("click", () => startTransfer(true));
$("#cancel").addEventListener("click", () => { if (state.transfer) { api.cancel(state.transfer.job); $("#cancel").disabled = true; $("#cancel").textContent = "Cancelando…"; } });
async function saveTransferReport(transfer) {
  const r = await call("export_transfer", transfer);
  if (!r || r.cancelled) return;
  $("#open-report").hidden = false;
  toast(`Guardé el reporte: ${r.name}`);
}
$("#open-report").addEventListener("click", () => call("open_export"));

$("#again").addEventListener("click", () => { $("#open-report").hidden = true; state.transfer = null; $("#cancel").disabled = false; $("#cancel").textContent = "Cancelar"; delete state.playlists[state.to]; resetTransferView(); });

/* ---------------------------------------------------------------- quitar repetidos */

async function renderDupControls() {
  const avail = USABLE.filter(connected);
  $("#dup-connect").hidden = avail.length > 0;
  $("#dup-controls").hidden = avail.length === 0;
  if (!avail.length) return;
  if (!avail.includes(state.dupService)) state.dupService = avail[0];
  $("#dup-svc").innerHTML = USABLE.map(k => `<button data-svc="${k}" aria-pressed="${k === state.dupService}" ${connected(k) ? "" : "disabled"}>${NAMES[k]}</button>`).join("");
  $("#dup-playlist").innerHTML = `<option>Cargando…</option>`;
  const pls = await getPlaylists(state.dupService) || [];
  $("#dup-playlist").innerHTML = pls.length ? pls.map(p => `<option value="${esc(p.id)}">${esc(p.name)}${p.count != null ? ` (${temas(p.count)})` : ""}</option>`).join("")
    : `<option value="">No tenés playlists</option>`;
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
  $("#dup-summary").hidden = false; $("#dup-summary").innerHTML = `<span class="muted">Leyendo la playlist…</span>`;
  const r = await call("scan_duplicates", state.dupService, pid);
  if (!r) { $("#dup-summary").hidden = true; return; }
  const done = await follow(r.job);
  if (done.state !== "done") { $("#dup-summary").innerHTML = `<span class="error">${esc(done.error || "No se pudo leer la playlist.")}</span>`; return; }
  const res = state.scan = done.result;
  if (!res.duplicates.length) { $("#dup-summary").innerHTML = `<b>«${esc(res.name)}» no tiene temas repetidos.</b> <span class="muted">Revisé ${temas(res.total)}.</span>`; return; }
  const versions = res.duplicates.filter(d => d.kind !== "misma").length;
  $("#dup-summary").innerHTML = `<b>Encontré ${res.duplicates.length} ${res.duplicates.length === 1 ? "repetido" : "repetidos"} en «${esc(res.name)}».</b> <span class="muted">${versions ? "Las otras versiones (en vivo, remix…) quedan sin tildar. " : ""}Destildá los que quieras conservar.</span>`;
  const meta = s => [s.artist, s.album, mmss(s.duration)].filter(Boolean).map(esc).join(", ");
  $("#dups").innerHTML = res.duplicates.map(d => `
    <li><input type="checkbox" ${d.kind === "misma" ? "checked" : ""} aria-label="Quitar ${esc(d.song.title)}" data-i="${d.i}">
      <div class="side remove"><span class="tag">Se quita</span>${d.kind !== "misma" ? `<span class="tag version">Otra versión</span>` : ""}<div class="t">${esc(d.song.title)}</div><div class="meta">${meta(d.song)}</div></div>
      <div class="side keep"><span class="tag">Queda</span><div class="t">${esc(d.kept.title)}</div><div class="meta">${meta(d.kept)}</div></div></li>`).join("");
  $("#dup-actions").hidden = false; updateRemove();
}

const checkedDups = () => $$("#dups input:checked").map(i => Number(i.dataset.i));
function updateRemove() {
  const c = checkedDups().length, label = c === 1 ? "Quitar 1 tema" : `Quitar ${c} temas`;
  $("#remove").textContent = label; $("#confirm-yes").textContent = label;
  $("#remove").dataset.blocked = c ? "0" : "1"; $("#remove").disabled = !c || state.busy;
  $("#c-title").textContent = `¿${label} de «${state.scan?.name || ""}»?`;
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
    toast(`Quitaste ${temas(done.result.removed)} de «${done.result.name}».`);
    delete state.playlists[state.dupService]; renderHomeHistory(); await renderDupControls(); scanDups();
  } else toast(done.error || "No se pudo quitar.");
});

/* ---------------------------------------------------------------- historial */

async function renderHistory() {
  const kind = state.histKind || "";
  const ops = await call("history", null, kind || null) || [];
  $("#hist-actions").hidden = ops.length === 0;
  $("#hist").innerHTML = ops.length ? ops.map(op => `
    <li data-op="${op.id}"><button class="row" aria-expanded="false">
      <span class="date">${esc(opDate(op))}</span>
      <span><div class="what">${esc(opTitle(op))}</div><div class="sub">${esc(opRoute(op))}. ${esc(opSub(op))}</div></span>
      <span class="mosaic small" aria-hidden="true">${tilesHTML(op.tiles)}</span></button>
      <div class="detail"><ul><li class="muted">Cargando…</li></ul></div></li>`).join("")
    : (kind ? `<li class="empty">No hay ${kind === "dedupe" ? "limpiezas de repetidos" : "transferencias"} en el historial.</li>` : EMPTY_HISTORY);
}
$("#hist").addEventListener("click", async e => {
  const b = e.target.closest("button.row"); if (!b) return;
  const li = b.parentElement, open = li.classList.toggle("open"); b.setAttribute("aria-expanded", open);
  if (!open || li.dataset.loaded) return;
  const tracks = await call("history_detail", Number(li.dataset.op)) || [];
  li.dataset.loaded = "1";
  $(".detail ul", li).innerHTML = tracks.length ? tracks.map(t => {
    const cls = CLASS[t.status] || "ok";
    const to = t.match_title && t.status !== "no_encontrado" ? ` <span class="muted">→ ${esc(t.match_title)}${t.match_artist ? `, ${esc(t.match_artist)}` : ""}</span>` : "";
    return `<li><span class="badge ${cls}">${SYMBOL[cls]}</span><span><b>${esc(t.title)}</b> <span class="muted">${esc(t.artist)}</span>${to}</span></li>`;
  }).join("") : `<li class="muted">Sin detalle.</li>`;
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
  $("#export-note").textContent = `Guardé ${r.rows === 1 ? "1 fila" : `${r.rows} filas`} en ${r.path}`;
  toast(`Exportaste el historial: ${r.name}`);
});
$("#open-export").addEventListener("click", () => call("open_export"));

/* ---------------------------------------------------------------- ajustes */

async function renderSettings() {
  $("#svc-list").innerHTML = USABLE.map(k => {
    const st = state.status[k];
    const label = { connected: "conectado", expired: "la sesión venció", disconnected: "sin conectar" }[st] || "…";
    const btns = st === "connected"
      ? `<button class="btn ghost" data-disconnect="${k}" data-needs-idle>Desconectar</button>`
      : `<button class="btn primary" data-connect="${k}" data-needs-idle>${st === "expired" ? "Reconectar" : "Conectar"}</button>`;
    return `<div class="svc"><span><b>${NAMES[k]}</b> <span class="muted">${label}</span><div class="login-wait" id="wait-${k}" hidden></div></span><span class="actions-inline">${btns}</span></div>`;
  }).join("") + `<div class="svc"><span><b>Spotify</b> <span class="muted">próximamente</span></span><button class="btn ghost" disabled>Conectar</button></div>`;
  setBusy(state.busy);

  const b = await call("browsers");
  if (b) {
    const chosen = b.chosen || b.suggested;
    $("#browser-list").innerHTML = b.browsers.length ? b.browsers.map(x => `
      <label class="opt"><input type="radio" name="br" value="${esc(x.exe)}" ${x.exe === chosen ? "checked" : ""}>
      <span>${esc(x.name)}${x.default ? ` <span class="muted">(predeterminado)</span>` : ""}</span></label>`).join("")
      : `<p class="muted">No encontré navegadores instalados.</p>`;
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
  const lim = st.limits;
  $("#th-ok").value = Number(st.score_ok).toFixed(2);
  $("#th-min").value = Number(st.score_min).toFixed(2);
  $("#th-ok").min = lim.ok_min; $("#th-ok").max = lim.ok_max; $("#th-min").min = lim.min_min;
  $("#th-help").textContent = `De fábrica: ${st.defaults.score_ok.toFixed(2)} y ${st.defaults.score_min.toFixed(2)}. `
    + `Más alto es más estricto (menos temas agregados, menos errores). "Sin dudar" va de ${lim.ok_min.toFixed(2)} a ${lim.ok_max.toFixed(2)} `
    + `y "para revisar" desde ${lim.min_min.toFixed(2)}: así nunca se agregan solas otras versiones ni canciones de otro artista.`;
  $("#th-error").hidden = true;
}
async function saveThresholds(ok, min) {
  $("#th-error").hidden = true;
  try {
    const r = await api.save_thresholds(ok, min);
    if (r.error) { $("#th-error").textContent = r.error; $("#th-error").hidden = false; return; }
    toast(`Guardado: sin dudar desde ${r.score_ok.toFixed(2)}, para revisar desde ${r.score_min.toFixed(2)}.`);
    renderThresholds(await call("settings"));
  } catch (e) { toast(`Algo salió mal: ${e.message || e}`); }
}
$("#th-save").addEventListener("click", () => saveThresholds($("#th-ok").value, $("#th-min").value));
$("#th-reset").addEventListener("click", async () => {
  const st = await call("settings");
  if (st) saveThresholds(st.defaults.score_ok, st.defaults.score_min);
});

$("#browser-list").addEventListener("change", async e => {
  if (e.target.name !== "br") return;
  const r = await call("choose_browser", e.target.value);
  if (r) toast(`Las ventanas para iniciar sesión se abren en ${r.name}.`);
});

$("#svc-list").addEventListener("click", async e => {
  const c = e.target.closest("[data-connect]"), d = e.target.closest("[data-disconnect]");
  if (c) {
    const key = c.dataset.connect;
    const r = await call("connect", key); if (!r) return;
    const wait = $("#wait-" + key); wait.hidden = false; wait.textContent = "Abriendo el navegador…";
    const done = await follow(r.job, ev => {
      if (ev.type === "login") wait.textContent = `Se abrió ${ev.browser}. Iniciá sesión en ${ev.service} y volvé acá; se completa solo.`;
    });
    wait.hidden = true;
    if (done.state === "done") toast(`${NAMES[key]} conectado.`); else toast(done.error || "No se pudo conectar.");
    delete state.playlists[key];
    await refreshStatus(); renderSettings();
  }
  if (d) {
    const key = d.dataset.disconnect;
    if (await call("disconnect", key)) { toast(`Desconectaste ${NAMES[key]}.`); delete state.playlists[key]; await refreshStatus(); renderSettings(); }
  }
});

$("#open-data").addEventListener("click", () => call("open_data_dir"));

/* ---------------------------------------------------------------- arranque */

async function start() {
  api = window.pywebview.api;
  ["#go", "#dry", "#remove", "#dup-scan"].forEach(s => { $(s).dataset.needsIdle = ""; });
  setupRoute();
  const info = await call("info");
  if (info) { $("#version").textContent = `Versión ${info.version}`; $("#about-version").textContent = `PlaylistTransfer ${info.version}, open source con licencia MIT.`; }
  renderHomeHistory();
  await refreshStatus();
  api.ui_ready();
}

if (window.pywebview?.api) start();
else window.addEventListener("pywebviewready", start, { once: true });
