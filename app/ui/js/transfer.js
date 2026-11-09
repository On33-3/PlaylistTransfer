/* Transferir: de dónde y a dónde, elegir playlists, en vivo y el resultado. */
"use strict";

/* ---------------------------------------------------------------- transferir: de dónde y a dónde */

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
  $("#swap").title = t("transfer.swap_title", { src: NAMES[state.to], dst: NAMES[state.from] });
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
  $("#route-lede").textContent = t("transfer.lede");
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
    // Si falló porque un servicio dejó de responder (sesión vencida), los chips lo muestran
    if (!cancelled) refreshStatus();
  }
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
