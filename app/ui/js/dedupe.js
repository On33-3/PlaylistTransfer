/* Quitar repetidos: elegir playlist, revisar y confirmar. */
"use strict";

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
    delete state.playlists[state.dupService]; await renderDupControls(); scanDups();
  } else toast(done.error || t("dedupe.remove_error"));
});
