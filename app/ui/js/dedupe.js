/* Remove duplicates: pick one playlist or all of them, check and confirm. */
"use strict";

/* ---------------------------------------------------------------- remove duplicates */

const ALL_PLAYLISTS = "__all__";     // check every playlist of the service (like app/api/duplicates.ALL)
// The picked item in the closed list, on one line with «…» if it doesn't fit (styles.css, `.select > button`).
// It goes first in the list: an engine without styled lists ignores it
const SELECTED = "<button><selectedcontent></selectedcontent></button>";

async function renderDupControls() {
  const turn = renderDupControls.turn = (renderDupControls.turn || 0) + 1;   // only the latest request counts
  const avail = USABLE.filter(connected);
  $("#dup-connect").hidden = avail.length > 0;
  $("#dup-controls").hidden = avail.length === 0;
  if (!avail.length) { if (state.scan) clearDups(); $("#dup-how").hidden = true; return; }
  if (!avail.includes(state.dupService)) state.dupService = avail[0];
  // A result from a service that is no longer connected (or from another one) can't remove anything: it's dropped
  if (state.scan && state.scan.service !== state.dupService) clearDups();
  // Locked while something runs: otherwise one service's result showed up under another's name
  $("#dup-svc").innerHTML = USABLE.map(k => `<button data-svc="${k}" aria-pressed="${k === state.dupService}" data-needs-idle data-blocked="${connected(k) ? 0 : 1}" ${connected(k) && !state.busy ? "" : "disabled"}>${NAMES[k]}</button>`).join("");
  $("#dup-playlist").innerHTML = SELECTED + `<option>${esc(t("common.loading"))}</option>`;
  // With fewer than 2 songs there can't be duplicates (unknown counts stay)
  const all = await getPlaylists(state.dupService) || [];
  if (turn !== renderDupControls.turn) return;
  const pls = all.filter(p => p.count == null || p.count >= 2);
  // With more than one, the first option is checking them all
  const allOption = pls.length > 1 ? `<option value="${ALL_PLAYLISTS}">${esc(t("dedupe.all_playlists", { n: pls.length }))}</option>` : "";
  $("#dup-playlist").innerHTML = SELECTED + (pls.length ? allOption + pls.map(p => `<option value="${esc(p.id)}">${esc(p.name)} (${esc(countLabel(p.count))})</option>`).join("")
    : `<option value="">${esc(t(all.length ? "dedupe.no_playlists_with_songs" : "dedupe.no_playlists"))}</option>`);
  // Redrawing doesn't change the chosen playlist (before, after removing songs another one was checked)
  const values = $$("#dup-playlist option").map(o => o.value);
  if (state.dupPlaylist && values.includes(state.dupPlaylist)) $("#dup-playlist").value = state.dupPlaylist;
  else if (state.dupPlaylist && !state.scan) clearDups();
  state.dupPlaylist = $("#dup-playlist").value;
  showPickedName();
  const scan =$("#dup-scan"); scan.dataset.needsIdle = ""; scan.dataset.blocked = pls.length ? "0" : "1"; scan.disabled = !pls.length || state.busy;
  $("#dup-how").hidden = !!state.scan || !$("#dup-progress").hidden;
}

$("#dup-svc").addEventListener("click", e => {
  const b = e.target.closest("[data-svc]"); if (!b || b.disabled) return;
  state.dupService = b.dataset.svc; state.dupPlaylist = null; clearDups(); renderDupControls();
});
$("#dup-playlist").addEventListener("change", () => { state.dupPlaylist = $("#dup-playlist").value; showPickedName(); clearDups(); });

/** A long name ends in «…» in the closed list: on hover it can be read in full. */
function showPickedName() {
  $("#dup-playlist").title = $("#dup-playlist").selectedOptions[0]?.textContent || "";
}

/** Back to the state before checking: the three steps show. */
function clearDups() {
  state.scan = null;
  $("#dups").innerHTML = ""; $("#dup-summary").hidden = true; $("#dup-actions").hidden = true;
  $("#dup-clean").hidden = true; $("#dup-failed").hidden = true; $("#dup-progress").hidden = true;
  $("#dup-hint").hidden = true; $("#dup-how").hidden = false;
  scanButton(false);
}

/** Before checking, "Check" is the main action; with the result on screen, the bar at the bottom is. */
function scanButton(results) {
  const b = $("#dup-scan");
  b.classList.toggle("primary", !results); b.classList.toggle("ghost", results);
  b.textContent = t(results ? "dedupe.scan_again" : "dedupe.scan");
}

/** Text with **bold**: everything is escaped and only those parts are marked. */
const rich = text => esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");

/* ---- checking: which one is next and how each one came out */

function onScanEvent(p, e) {
  const now = $("#dup-progress-now");
  if (e.type === "scan_list") {
    p.names = e.names; p.done = 0;
    const many = e.names.length > 1;
    $("#dup-bar-box").hidden = !many; $("#dup-pl").hidden = !many;
    $("#dup-pl").innerHTML = many ? e.names.map((n, k) => `<li data-k="${k}"><span>${esc(n)}</span><span class="dup-pill">${esc(t("dedupe.pill_wait"))}</span></li>`).join("") : "";
  } else if (e.type === "scan_start") {
    stopWaiting(now);
    const many = p.names.length > 1;
    $("#dup-progress-text").textContent = many ? t("dedupe.progress", { k: e.k + 1, n: p.names.length })
      : t("dedupe.progress_one", { name: e.name || $("#dup-playlist").selectedOptions[0]?.textContent || "" });
    now.textContent = many ? `· «${e.name}»` : "";
    const li = $(`#dup-pl li[data-k="${e.k}"]`);
    if (li) { li.lastElementChild.className = "dup-pill now"; li.lastElementChild.textContent = t("dedupe.pill_now"); $("#dup-pl").prepend(li); }
  } else if (e.type === "scan_result") {
    p.done++; $("#dup-bar").style.width = `${Math.round(100 * p.done / p.names.length)}%`;
    const pill = $(`#dup-pl li[data-k="${e.k}"] .dup-pill`); if (!pill) return;
    const [cls, text] = e.error ? ["error", t("dedupe.pill_error")]
      : e.found ? ["found", t("dedupe.n_dups", { n: e.found })] : ["clean", t("dedupe.pill_clean")];
    pill.className = `dup-pill ${cls}`; pill.textContent = text;
  } else if (e.type === "waiting") {
    showWaiting(now, e);                       // the service asked for a pause: countdown
  } else if (e.type === "waited") {
    stopWaiting(now); now.textContent = "";
  }
}

async function scanDups() {
  const pid = $("#dup-playlist").value; if (!pid) return;
  clearDups();
  $("#dup-how").hidden = true; $("#dup-progress").hidden = false;
  $("#dup-progress-text").textContent = t("dedupe.reading"); $("#dup-progress-now").textContent = "";
  $("#dup-bar").style.width = "0"; $("#dup-pl").innerHTML = "";
  const r = await call("scan_duplicates", state.dupService, pid);
  if (!r) { clearDups(); return; }
  state.dupJob = r.job;
  $("#dup-cancel").disabled = false; $("#dup-cancel").textContent = t("transfer.cancel");
  const progress = {};
  const done = await follow(r.job, e => onScanEvent(progress, e));
  state.dupJob = null;
  stopWaiting($("#dup-progress-now"));
  $("#dup-progress").hidden = true;
  if (done.state !== "done") {
    $("#dup-summary").hidden = false;
    $("#dup-summary").innerHTML = `<span class="error">${esc(done.state === "cancelled" ? t("dedupe.cancelled") : (done.error || t("dedupe.read_error")))}</span>`;
    return;
  }
  state.scan = { ...done.result, many: pid === ALL_PLAYLISTS, service: state.dupService };
  showDupResults();
}

/* ---- result: one card per playlist with duplicates */

const dupCount = n => t("dedupe.n_dups", { n });
const listCount = n => t("dedupe.lists", { n });

/** "Gym, Anime, NEW and 10 more" */
function someNames(names, show = 4) {
  const shown = names.slice(0, show).join(", ");
  return names.length > show ? t("dedupe.and_more", { names: shown, n: names.length - show }) : shown;
}

function showDupResults() {
  const res = state.scan, groups = res.groups;
  $("#dup-how").hidden = true;
  $("#dup-summary").hidden = false;
  const total = groups.reduce((s, g) => s + g.duplicates.length, 0);
  let head;
  if (!res.many) {
    const g = groups[0];
    head = g ? t("dedupe.found", { n: total, name: g.name }) : t("dedupe.clean", { name: res.clean[0] || "" });
  } else {
    head = groups.length ? t("dedupe.found_many", { dups: dupCount(total), k: groups.length, total: res.checked })
      : t("dedupe.all_clean", { n: res.checked });
  }
  $("#dup-summary").innerHTML = `<b>${esc(head)}</b>`;
  // What ticking means, said before the list: nothing is left to intuition
  $("#dup-hint").hidden = !groups.length; $("#dup-hint").innerHTML = groups.length ? rich(t("dedupe.decide_hint")) : "";
  scanButton(true);

  $("#dups").innerHTML = groups.map(dupGroupHTML).join("");

  // Checking all: the clean ones, on one line (if they're all clean, which ones were checked)
  const clean = res.many ? res.clean : [];
  $("#dup-clean").hidden = !clean.length;
  $("#dup-clean").textContent = !clean.length ? ""
    : t(groups.length ? "dedupe.clean_many" : "dedupe.checked_many", { names: someNames(clean) });
  $("#dup-failed").hidden = !res.failed?.length;
  $("#dup-failed").textContent = res.failed?.length ? t("dedupe.failed", { names: someNames(res.failed.map(f => f.name)) }) : "";
  if (!res.many && !groups.length) $("#dup-summary").innerHTML += ` <span class="muted">${esc(t("dedupe.checked", { songs: songs(res.songs_checked ?? 0) }))}</span>`;
  $("#dup-actions").hidden = !groups.length;
  updateRemove();
}

/** A playlist's card: its name, how many are removed, "tick / untick all" and its rows. */
function dupGroupHTML(g, gi) {
  return `
    <section class="dup-group" data-g="${gi}">
      <header><span>${esc(g.name)}</span>
        <span class="dup-group-right"><span class="dup-group-count"></span><button class="dup-toggle" data-toggle="${gi}"></button></span></header>
      <ul class="dups">${g.duplicates.map(d => dupRowHTML(d, gi)).join("")}
      </ul>
    </section>`;
}

/** A row: ticked, it is removed → the one on the right stays; unticked, both stay (the CSS switches it).
 * Other versions come unticked and, if it's because of the length, they say why. */
function dupRowHTML(d, gi) {
  const meta = s => [s.artist, s.album, mmss(s.duration)].filter(Boolean).map(esc).join(", ");
  const why = d.longer == null ? ""
    : `<div class="why">${esc(t(d.longer > 0 ? "dedupe.why_longer" : "dedupe.why_shorter", { time: mmss(Math.abs(d.longer)) }))}</div>`;
  const version = d.kind !== "misma" ? `<span class="tag version">${esc(t("dedupe.tag_version"))}</span>` : "";
  return `
        <li><input type="checkbox" ${d.kind === "misma" ? "checked" : ""} aria-label="${esc(t("dedupe.remove_aria", { title: d.song.title }))}" data-g="${gi}" data-i="${d.i}">
          <div class="side remove"><span class="tag on">${esc(t("dedupe.tag_remove"))}</span><span class="tag off">${esc(t("dedupe.tag_keep_both"))}</span>${version}<div class="t">${esc(d.song.title)}</div><div class="meta">${meta(d.song)}</div>${why}</div>
          <span class="arrow" aria-hidden="true">→</span>
          <div class="side keep"><span class="tag">${esc(t("dedupe.tag_keep"))}</span><div class="t">${esc(d.kept.title)}</div><div class="meta">${meta(d.kept)}</div></div></li>`;
}

/** What is ticked, per playlist: [{ scan, selected: [i...] }] */
function dupChoices() {
  const by = new Map();
  $$("#dups input:checked").forEach(i => {
    const g = state.scan.groups[Number(i.dataset.g)];
    if (!by.has(g.scan)) by.set(g.scan, []);
    by.get(g.scan).push(Number(i.dataset.i));
  });
  return [...by].map(([scan, selected]) => ({ scan, selected }));
}

/** Everything that depends on what is ticked: the button, the decision bar, each card and the confirmation. */
function updateRemove() {
  if (!state.scan) return;
  const choices = dupChoices();
  const c = choices.reduce((s, x) => s + x.selected.length, 0);
  const one = choices.length === 1 ? state.scan.groups.find(g => g.scan === choices[0].scan) : null;
  const label = t("dedupe.remove_n", { n: c });
  $("#remove").textContent = label; $("#confirm-yes").textContent = label;
  $("#remove").dataset.blocked = c ? "0" : "1"; $("#remove").disabled = !c || state.busy;
  showDecision(c, choices.length, one);
  updateGroupHeaders();
  updateConfirm(choices, c, label, one);
}

/** The bar at the bottom says what will happen if confirmed, always visible. */
function showDecision(c, lists, one) {
  const what = one ? t("dedupe.decide_one", { songs: songs(c), name: one.name }) : t("dedupe.decide", { songs: songs(c), lists: listCount(lists) });
  $("#dup-decide-text").innerHTML = c ? `<b>${esc(what)}</b> ${esc(t("dedupe.decide_keep"))}` : `<b>${esc(t("dedupe.decide_none"))}</b>`;
}

/** Each card: how many are removed from that playlist and "tick / untick all". */
function updateGroupHeaders() {
  $$("#dups .dup-group").forEach(sec => {
    const boxes = $$("input", sec), on = boxes.filter(b => b.checked).length;
    $(".dup-group-count", sec).textContent = t("dedupe.group_count", { n: on, total: boxes.length });
    $(".dup-toggle", sec).textContent = t(on ? "dedupe.untick_all" : "dedupe.tick_all");
  });
}

/** The dialog: the title, how many from each playlist if there are several and, on Spotify, that the remaining one is added back. */
function updateConfirm(choices, c, label, one) {
  $("#c-title").textContent = one ? t("dedupe.confirm_title", { action: label, name: one.name })
    : t("dedupe.confirm_title_lists", { songs: songs(c), lists: listCount(choices.length) });
  const per = choices.map(x => [state.scan.groups.find(g => g.scan === x.scan)?.name, x.selected.length]);
  $("#c-list").hidden = per.length < 2;
  $("#c-list").innerHTML = per.length < 2 ? "" : per.map(([name, n]) => `<li>${esc(t("dedupe.confirm_item", { name, songs: songs(n) }))}</li>`).join("");
  // Spotify removes every copy of a song: the one that stays is added back in its place
  const readd = $$("#dups input:checked").filter(i => state.scan.groups[Number(i.dataset.g)]?.duplicates[Number(i.dataset.i)]?.readd).length;
  $("#c-readd").hidden = !readd;
  $("#c-readd").textContent = readd ? t("dedupe.confirm_readd", { n: readd, service: NAMES[state.dupService] }) : "";
}
$("#dups").addEventListener("change", updateRemove);
$("#dups").addEventListener("click", e => {
  const b = e.target.closest("[data-toggle]"); if (!b) return;
  const boxes = $$("input", b.closest(".dup-group")), any = boxes.some(x => x.checked);
  boxes.forEach(x => { x.checked = !any; });
  updateRemove();
});
$("#dup-scan").addEventListener("click", scanDups);
// A long check (or a pause the service asked for) can be stopped: nothing is removed while checking
$("#dup-cancel").addEventListener("click", () => {
  if (!state.dupJob) return;
  api.cancel(state.dupJob); $("#dup-cancel").disabled = true; $("#dup-cancel").textContent = t("transfer.cancelling");
});
$("#remove").addEventListener("click", () => { if (state.scan && dupChoices().length) $("#confirm").showModal(); });
$("#confirm-no").addEventListener("click", () => $("#confirm").close());
$("#confirm-yes").addEventListener("click", async () => {
  const choices = dupChoices(); $("#confirm").close();
  if (!choices.length || !state.scan) return;
  const r = await call("remove_many", choices, true);   // true: the user confirmed in the dialog
  if (!r) return;
  const done = await follow(r.job);
  if (done.state !== "done") {
    // Something may have been removed from the first playlists: what is on screen no longer holds. It says
    // what happened and starts over (the error says which ones were changed and which to check again)
    const msg = done.error || t("dedupe.remove_error");
    forgetPlaylists(state.dupService); clearDups(); await renderDupControls();
    $("#dup-how").hidden = true; $("#dup-summary").hidden = false;
    $("#dup-summary").innerHTML = `<span class="error">${esc(msg)}</span>`;
    toast(msg);
    return;
  }
  const res = done.result;
  toast(res.names.length > 1 ? t("dedupe.removed_many_toast", { songs: songs(res.removed), lists: listCount(res.names.length) })
    : t("dedupe.removed_toast", { songs: songs(res.removed), name: res.name }));
  // The playlists that changed were checked again: the screen shows how they ended up
  const scan = state.scan;
  for (const g of res.groups) {
    const at = scan.groups.findIndex(x => x.playlist_id === g.playlist_id);
    if (g.duplicates.length) scan.groups[at] = g;
    else { scan.groups.splice(at, 1); scan.clean.unshift(g.name); if (!scan.many) scan.songs_checked = g.total; }
  }
  forgetPlaylists(state.dupService); await renderDupControls();
  showDupResults();
});
