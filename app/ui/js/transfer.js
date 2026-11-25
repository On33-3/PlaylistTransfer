/* Transfer: from where and to where, picking playlists, live progress and the result. */
"use strict";

/* ---------------------------------------------------------------- transfer: from where and to where */

function fitSelect(sel) {
  const probe = document.createElement("span");
  const cs = getComputedStyle(sel);
  probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${cs.font};letter-spacing:${cs.letterSpacing}`;
  probe.textContent = sel.options[sel.selectedIndex]?.text || "";
  document.body.appendChild(probe);
  // Width of the name + the padding (the right one leaves room for the ▾ arrow)
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
    // If they end up the same, they swap (with more than two services, the other takes the previous one)
    if (state.from === state.to) { state[other] = prev; $("#" + other).value = state[other]; }
    fitSelect($("#from")); fitSelect($("#to"));
    state.transfer = null; resetTransferView();
  };
  $("#from").addEventListener("change", onChange("from"));
  $("#to").addEventListener("change", onChange("to"));
  // ⇅ flips the direction (the destination becomes the source)
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

/* ---------------------------------------------------------------- transfer: picking */

async function getPlaylists(key, force = false) {
  if (!force && state.playlists[key]) return state.playlists[key];
  const version = playlistsVersion;
  const r = await call("playlists", key);
  if (Array.isArray(r) && version === playlistsVersion) state.playlists[key] = r;
  return Array.isArray(r) ? r : null;
}

/** What is ticked in the lists, to redraw them without losing it (if it's still the same route). */
function rememberPicks(route) {
  if (!$("#src-list input")) return;
  state.picked = { route, src: $("input[name=src]:checked")?.value, dst: $$("input[name=dst]:checked").map(i => i.value),
                   isNew: !!$("#dst-new")?.checked, newName: $("#dst-new-name")?.value || "", touched: $("#dst-new-name")?.dataset.touched };
}

function restorePicks(route) {
  const p = state.picked; if (!p || p.route !== route) return;
  $$("input[name=src]").forEach(i => { i.checked = i.value === p.src; });
  $$("input[name=dst]").forEach(i => { i.checked = p.dst.includes(i.value); });
  const name = $("#dst-new-name");
  if ($("#dst-new")) $("#dst-new").checked = p.isNew;
  name.hidden = !p.isNew; name.value = p.newName;
  if (p.touched) name.dataset.touched = p.touched;
}

async function loadPickers() {
  const turn = loadPickers.turn = (loadPickers.turn || 0) + 1;     // only the latest request counts
  const { from, to } = state, route = `${from}>${to}`;
  rememberPicks(route);
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
  const [all, dst] = await Promise.all([getPlaylists(from), getPlaylists(to)]);
  if (turn !== loadPickers.turn) return;
  // As a source, only those with something to move (unknown counts stay: the service doesn't say)
  const src = all && all.filter(p => p.count !== 0);
  // The name goes in its own span: if it's very long it's cut with "…" and shown in full on hover
  const item = (p, type) => `<li data-filter="${esc(p.name.toLowerCase())}"><label title="${esc(p.name)}"><input type="${type}" name="${type === "radio" ? "src" : "dst"}" value="${esc(p.id)}" data-name="${esc(p.name)}" data-count="${p.count ?? ""}"> <span class="name">${esc(p.name)}</span> <span class="count num">${esc(countLabel(p.count))}</span></label></li>`;
  $("#src-list").innerHTML = !src ? `<li class="loading error">${esc(t("transfer.read_error"))}</li>`
    : src.length ? src.map(p => item(p, "radio")).join("")
    : `<li class="loading">${esc(t(all.length ? "transfer.no_playlists_with_songs" : "transfer.no_playlists", { service: NAMES[from] }))}</li>`;
  // "Create a new playlist" goes first (and stays pinned on top): with hundreds of playlists it isn't lost at the end
  $("#dst-list").innerHTML = `<li class="new"><label><input type="checkbox" id="dst-new"> ${esc(t("transfer.new"))}</label><input type="text" id="dst-new-name" aria-label="${esc(t("transfer.new_aria"))}" placeholder="${esc(t("transfer.new_placeholder"))}" hidden></li>`
    + (dst || []).map(p => item(p, "checkbox")).join("");
  // If either list is long, both get a search box: that way the boxes stay aligned
  const withFilter = Math.max((src || []).length, (dst || []).length) > FILTER_FROM;
  addFilter("#src-list", withFilter, t("transfer.src_title", { service: NAMES[from] }));
  addFilter("#dst-list", withFilter, t("transfer.dst_title", { service: NAMES[to] }));
  restorePicks(route);
  updateGo();
}

const FILTER_FROM = 8;   // with more playlists than this, the search box appears

/** A search box above a playlist list. Hides the ones that don't match (doesn't untick them). */
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

/* ---------------------------------------------------------------- transfer: live */

function setStep(n) { [...$("#steps").children].forEach((li, i) => { li.className = i < n ? "done" : (i === n ? "current" : ""); }); }

function resetTransferView() {
  $("#run").hidden = true; $("#pick").hidden = false; $("#done").classList.remove("show"); setStep(0);
  $("#review").hidden = true; $("#review-list").innerHTML = "";
  if ($("#transferir").classList.contains("active")) loadPickers();
}

function trackHTML(e) {
  const cls = CLASS[e.status] || "now";
  const s = e.song, m = e.match;
  let note = "";
  if (e.status === "dudoso_omitido" && m) {
    note = t(state.transfer?.dryRun ? "transfer.note_skipped_dry" : "transfer.note_skipped", { title: m.title, artist: m.artist || "?" });
  }
  if (e.status === "no_encontrado") note = e.alternatives?.length ? t("transfer.note_miss_alts") : t("transfer.note_miss");
  if (e.status === "repetido") note = e.as ? t("transfer.note_repeated_as", { title: e.as.title }) : t("transfer.note_repeated");
  else if (e.as) note = t("transfer.note_already_as", { title: e.as.title });
  else if (e.status === "ya_estaba") note = t("transfer.note_already");
  let alts = e.status === "no_encontrado" && e.alternatives?.length
    ? `<div class="alts">${e.alternatives.map((a, k) => `<button data-alt="${e.n}:${k}" disabled title="${esc(t("transfer.alt_title", { title: a.title, artist: a.artist || "?" }))}">+ ${esc(a.title)} · ${esc(a.artist || "?")}</button>`).join("")}</div>` : "";
  if (e.status === "dudoso_omitido" && m && !state.transfer?.dryRun) {   // the user decides: "+ Add anyway"
    alts = `<div class="alts"><button data-add-doubtful="${e.n}" disabled title="${esc(t("transfer.add_anyway_title", { title: m.title, artist: m.artist || "?" }))}">${esc(t("transfer.add_anyway"))}</button></div>`;
  }
  const score = e.score != null && !["ya_estaba", "repetido", "no_encontrado"].includes(e.status) ? Number(e.score).toFixed(2) : "";
  return `<span class="badge ${cls}">${SYMBOL[cls]}</span><span><span class="t">${esc(s.title)}</span> <span class="a">${esc(s.artist)}</span>${note ? `<span class="note">. ${esc(note)}</span>` : ""}${alts}</span><span class="score num">${score}</span>`;
}

async function startTransfer(dryRun) {
  const { src, targets } = selection();
  if (!src || !targets.length) return;
  const r = await call("start_transfer", state.from, state.to, src.value, targets, dryRun);
  if (!r) return;
  const tr = state.transfer = { job: r.job, dryRun, total: 0, n: { ok: 0, doubt: 0, miss: 0, dup: 0, rep: 0 }, rows: {}, tracks: {}, to: state.to,
    from: state.from, sourceName: src.dataset.name,
    cached: 0, pausedMs: 0, saved: null };
  stopWaiting($("#eta")); $("#run-note").hidden = true;
  $("#add-all").hidden = true;
  $("#pick").hidden = true; $("#run").hidden = false; $("#done").classList.remove("show"); setStep(1);
  $("#mosaic").innerHTML = ""; $("#tracks").innerHTML = ""; $("#review-list").innerHTML = ""; $("#review").hidden = true;
  $("#count").textContent = "0";
  $("#count-of").textContent = t("transfer.songs"); $("#run-actions").hidden = false;
  // From which service and playlist to which: "Service · Playlist to Another service · Playlist"
  const side = (svc, name) => `<span class="route-svc">${esc(NAMES[svc])}</span> · ${esc(name)}`;
  $("#run-route").innerHTML = `${side(state.from, src.dataset.name)} <span class="muted">${esc(t("transfer.route_sep"))}</span> ${side(state.to, targets.map(x => x.name).join(", "))}`;
  showCounts(tr);
  // The legend says what will happen: "Added" / "Would be added" (dry run). "?" are never added on their own
  const dry = dryRun ? "_dry" : "";
  $('#run .legend [data-i18n="transfer.legend_ok"]').textContent = t(`transfer.legend_ok${dry}`);
  $('#run .legend [data-i18n="transfer.legend_skipped"]').textContent = t(`transfer.legend_skipped${dry}`);

  const done = await follow(tr.job, e => onTransferEvent(tr, e));
  stopWaiting($("#eta")); $("#run-actions").hidden = true; $("#eta").hidden = true; $("#run-note").hidden = true; setStep(2);
  if (!dryRun) serviceChanged(tr.to);          // even if it failed or was cancelled: it may have written something
  if (done.state === "done") {
    showTransferResult(tr, done.result);
  } else {
    const cancelled = done.state === "cancelled";
    $("#done-title").textContent = t(cancelled ? "transfer.cancelled_title" : "transfer.failed_title");
    // If it stopped while searching, the searches were kept: it says so, so you know you don't start from zero
    const saved = tr.saved && t("transfer.saved", { done: tr.saved.done, total: tr.saved.total, service: NAMES[tr.to] });
    $("#done-detail").innerHTML = [cancelled ? t("transfer.cancelled_text") : (done.error || ""), saved]
      .filter(Boolean).map(esc).join("<br>");
    // It stopped while searching: nothing was added yet. The legend doesn't say "Added" and the song being
    // searched doesn't look as if it were still going
    if (cancelled || tr.saved) $('#run .legend [data-i18n="transfer.legend_ok"]').textContent = t("transfer.legend_ok_stopped");
    $$("#tracks .badge.now").forEach(b => b.closest("li").remove());
    $$("#mosaic i.now").forEach(i => { i.className = ""; });
    $("#open-dest").hidden = true; $("#done").classList.add("show");
    // If it failed because a service stopped answering (expired session), the chips show it
    if (!cancelled) refreshStatus();
  }
}

/** The legend's numbers. "Repeated in the source" is rare: it only shows if there is one. */
function showCounts(tr) {
  for (const k in tr.n) $("#n-" + k).textContent = tr.n[k];
  $("#n-rep").parentElement.hidden = tr.n.rep === 0;
}

const ETA_AFTER = 5;     // with fewer songs, the average doesn't say anything yet

/** How much searching is left, from how long each song searched so far took (without the ones already
 * searched in an earlier run or the pauses the service asked for). */
function updateEta(tr, n, now = performance.now()) {
  const el = $("#eta"), searched = n - (tr.cached || 0);
  if (el._wait) return;                    // while there is a pause, the countdown shows
  el.hidden = searched < ETA_AFTER || n >= tr.total;
  if (el.hidden) return;
  const seconds = Math.max(0, now - tr.started - (tr.pausedMs || 0)) / 1000 / searched * (tr.total - n);
  el.textContent = seconds < 60 ? t("transfer.eta_less") : t("transfer.eta", { n: Math.ceil(seconds / 60) });
}

function onTransferEvent(tr, e) {
  if (e.type === "source") {
    tr.total = e.total; tr.started = performance.now(); $("#count-of").textContent = t("transfer.of", { songs: songs(e.total) });
    $("#mosaic").innerHTML = Array.from({ length: e.total }, (_, i) => `<i data-n="${i + 1}"></i>`).join("");
    // With many songs, smaller squares: the mosaic doesn't cover the screen
    $("#mosaic").classList.toggle("dense", e.total > 90 && e.total <= 300);
    $("#mosaic").classList.toggle("denser", e.total > 300);
  } else if (e.type === "searching") {
    const tile = $("#mosaic").children[e.n - 1]; if (tile) tile.className = "now";
    const li = document.createElement("li");
    li.innerHTML = `<span class="badge now">…</span><span><span class="t">${esc(e.song.title)}</span> <span class="a">${esc(t("transfer.searching", { service: NAMES[state.to] }))}</span></span><span></span>`;
    $("#tracks").prepend(li); tr.rows[e.n] = li;
  } else if (e.type === "resumed") {
    $("#run-note").textContent = t("transfer.resumed", { done: e.done, total: e.total }); $("#run-note").hidden = false;
  } else if (e.type === "waiting") {
    tr.pausedMs += e.seconds * 1000; if (tr.adding) tr.adding.pausedMs += e.seconds * 1000;
    showWaiting($("#eta"), e);
  } else if (e.type === "waited") {
    stopWaiting($("#eta")); $("#eta").hidden = true;
  } else if (e.type === "saved") {
    tr.saved = e;
  } else if (e.type === "writing") {
    $("#run-actions").hidden = true;          // from here on it writes: stopping would leave a playlist half-done
  } else if (e.type === "adding_to") {
    tr.adding = { name: e.name, started: performance.now(), pausedMs: 0 };
    showAdding(tr, 0, e.total);
  } else if (e.type === "adding") {
    showAdding(tr, e.done, e.total);
  } else if (e.type === "track") {
    tr.tracks[e.n] = e; if (e.cached) tr.cached++;
    const cls = CLASS[e.status]; tr.n[cls]++;
    setBigTile(e.n, e.status, e.song);
    if (tr.rows[e.n]) tr.rows[e.n].innerHTML = trackHTML(e);
    $("#count").textContent = e.n; showCounts(tr); updateEta(tr, e.n);
  } else if (e.type === "already" || e.type === "repeated") {
    // "already there" in the destination, or repeated higher up in the source: not added (again)
    const status = e.type === "already" ? "ya_estaba" : "repetido";
    const prev = tr.tracks[e.n]; if (!prev || prev.status === status) return;
    tr.n[CLASS[prev.status]]--; tr.n[CLASS[status]]++;
    const upd = { ...prev, status, as: e.as };
    tr.tracks[e.n] = upd;
    setBigTile(e.n, status, prev.song);
    if (tr.rows[e.n]) tr.rows[e.n].innerHTML = trackHTML(upd);
    showCounts(tr);
  }
}

/** While adding (a service that likes songs one at a time takes many minutes with thousands): how many
 * are done and how long is left, so it doesn't look frozen. */
function showAdding(tr, done, total, now = performance.now()) {
  const el = $("#eta"); if (el._wait || !tr.adding) return;   // during a pause the countdown shows
  let text = t("transfer.adding", { name: tr.adding.name, done, total });
  const ms = Math.max(0, now - tr.adding.started - tr.adding.pausedMs);
  if (done >= ETA_AFTER && done < total) {
    const seconds = ms / 1000 / done * (total - done);
    text += ` · ${seconds < 60 ? t("transfer.adding_left_less") : t("transfer.adding_left", { n: Math.ceil(seconds / 60) })}`;
  }
  el.textContent = text; el.hidden = false;
}

/** A square of the big mosaic: color, which song it is, and it can be clicked to jump to that song in the list. */
function setBigTile(n, status, song) {
  const tile = $("#mosaic").children[n - 1]; if (!tile) return;
  tile.className = `${CLASS[status]} jump`;
  tile.title = `${tileTitle(status, [song.title, song.artist], state.transfer?.dryRun)}. ${t("tile.jump")}`;
}

$("#mosaic").addEventListener("click", e => {
  const tile = e.target.closest("i.jump"); if (!tile || !state.transfer) return;
  const row = state.transfer.rows[Number(tile.dataset.n)]; if (!row) return;
  row.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");   // restarts the highlight
});

function showTransferResult(tr, res) {
  tr.result = res;
  showDoneText(tr, res);
  if (!res.dry_run) $$("#tracks [data-alt], #tracks [data-add-doubtful]").forEach(b => { b.disabled = false; });
  showReview(tr, res);
  updateAddAll();
}

/** The result box. It is rewritten if the person adds something later. */
function showDoneText(tr, res) {
  const lines = res.targets.map(x => {
    const extras = [];
    if (x.already) extras.push(t("transfer.extra_already", { n: x.already }));
    if (x.versions) extras.push(t("transfer.extra_versions", { n: x.versions }));
    return t(res.dry_run ? "transfer.line_dry" : "transfer.line", {
      name: x.name, new: x.created ? t("transfer.new_mark") : "", songs: songs(x.added), service: NAMES[tr.to],
      extra: extras.length ? `; ${extras.join(", ")}` : "",
    });
  });
  $("#done-title").textContent = t(res.dry_run ? "transfer.dry_title" : "transfer.done_title");
  const s = res.summary, notes = [];
  if (s.skipped) notes.push(t(res.dry_run ? "transfer.notes_skipped_dry" : "transfer.notes_skipped", { n: s.skipped }));
  if (s.not_found) notes.push(t("transfer.notes_not_found", { n: s.not_found }));
  if (s.repeated) notes.push(t("transfer.notes_repeated", { n: s.repeated }));
  // Always where it came from and where it went: service and playlist on both sides
  if (tr.from) lines.unshift(t("transfer.from_line", { name: res.source_name || tr.sourceName, service: NAMES[tr.from] }));
  $("#done-detail").innerHTML = lines.map(esc).join("<br>") + (notes.length ? `<br>${esc(notes.join(" "))}` : "");
  const first = res.targets.find(x => x.url);
  $("#open-dest").hidden = !first || res.dry_run;
  $("#open-dest").textContent = t("transfer.open", { service: NAMES[state.to] });
  $("#open-dest").onclick = () => first && call("open_url", first.url);
  $("#done").classList.add("show");
}

/** The person decided about a song: square, icon and counts now say how it ended up. */
function markDecided(tr, n, status) {
  const prev = tr.tracks[n]; if (!prev) return;
  tr.n[CLASS[prev.status]]--; tr.n[CLASS[status]]++;
  tr.tracks[n] = { ...prev, status };
  setBigTile(n, status, prev.song);
  const badge = $(".badge", tr.rows[n]);
  if (badge) { badge.className = `badge ${CLASS[status]}`; badge.textContent = SYMBOL[CLASS[status]]; }
  showCounts(tr);
}

/** After adding something: the result and what the other screens show. */
function afterDecision(tr, outcome) {
  Object.assign(tr.result, outcome);
  showDoneText(tr, tr.result);
  serviceChanged(tr.to);
}

/** "To review": the songs the person can act on (a "?" to add anyway, or another artist's
 *  version) move up to their own block below the result, in playlist order.
 *  The rows are moved (not copied): their buttons and the jump from the mosaic keep working. */
function showReview(tr, res) {
  const list = $("#review-list");
  list.innerHTML = "";
  const pending = res.dry_run ? [] : Object.keys(tr.rows).map(Number).sort((a, b) => a - b)
    .filter(n => $("[data-add-doubtful], [data-alt]", tr.rows[n]));
  pending.forEach(n => list.append(tr.rows[n]));
  $("#review").hidden = pending.length === 0;
}

/** "Add the N that may be another version": only if there are "?" left unadded in a real transfer. */
function updateAddAll() {
  const pending = $$("#review-list [data-add-doubtful]:not(.done), #tracks [data-add-doubtful]:not(.done)").map(b => Number(b.dataset.addDoubtful));
  const btn = $("#add-all");
  btn.hidden = pending.length === 0 || !state.transfer?.result || state.transfer.result.dry_run;
  btn.textContent = t("transfer.add_all", { n: pending.length });
  btn.dataset.ns = pending.join(",");
}

async function addDoubtful(ns) {
  const buttons = ns.map(n => $(`#run [data-add-doubtful="${n}"]`)).filter(Boolean);
  buttons.forEach(b => { b.disabled = true; });
  const r = await call("add_doubtful", state.transfer.result.transfer, ns);
  if (!r) { buttons.forEach(b => { b.disabled = false; }); return; }
  const done = await follow(r.job);
  if (done.state !== "done") { buttons.forEach(b => { b.disabled = false; }); toast(done.error || t("transfer.alt_error")); return; }
  const added = new Set(done.result.added_ns);
  for (const n of done.result.ns) {
    const b = $(`#run [data-add-doubtful="${n}"]`); if (!b) continue;
    b.classList.add("done");
    b.textContent = t(added.has(n) ? "transfer.added_anyway" : "transfer.add_anyway_already");
    markDecided(state.transfer, n, added.has(n) ? "ok" : "ya_estaba");
  }
  afterDecision(state.transfer, { summary: done.result.summary, targets: done.result.targets });
  toast(added.size ? t("transfer.add_doubtful_toast", { n: added.size }) : t("transfer.add_doubtful_already"));
  updateAddAll();
}

$("#add-all").addEventListener("click", () => {
  const ns = ($("#add-all").dataset.ns || "").split(",").filter(Boolean).map(Number);
  if (ns.length) addDoubtful(ns);
});

async function onTrackClick(e) {
  const one = e.target.closest("[data-add-doubtful]");
  if (one && !one.disabled && !one.classList.contains("done") && state.transfer?.result) { addDoubtful([Number(one.dataset.addDoubtful)]); return; }
  const b = e.target.closest("[data-alt]"); if (!b || b.disabled || !state.transfer?.result) return;
  const [n, k] = b.dataset.alt.split(":").map(Number);
  const siblings = $$("[data-alt]", b.parentElement);       // a song is added only once
  const enable = on => siblings.forEach(x => { x.disabled = !on; });
  enable(false);
  const r = await call("add_alternative", state.transfer.result.transfer, n, k);
  if (!r) { enable(true); return; }
  const done = await follow(r.job);
  if (done.state === "done") {
    const a = done.result.added;
    b.textContent = t("transfer.alt_added", { artist: a.artist });
    toast(t("transfer.alt_toast", { title: a.title, artist: a.artist }));
    markDecided(state.transfer, n, "ok");
    afterDecision(state.transfer, { summary: done.result.summary, targets: done.result.targets });
  } else { enable(true); toast(done.error || t("transfer.alt_error")); }
}
$("#tracks").addEventListener("click", onTrackClick);
$("#review-list").addEventListener("click", onTrackClick);

$("#go").addEventListener("click", () => startTransfer(false));
$("#dry").addEventListener("click", () => startTransfer(true));
$("#cancel").addEventListener("click", () => {
  if (state.transfer) { api.cancel(state.transfer.job); $("#cancel").disabled = true; $("#cancel").textContent = t("transfer.cancelling"); }
});

$("#again").addEventListener("click", () => {
  state.transfer = null;
  $("#cancel").disabled = false; $("#cancel").textContent = t("transfer.cancel");
  state.picked = null; $("#src-list").innerHTML = ""; forgetPlaylists(); resetTransferView();
});
