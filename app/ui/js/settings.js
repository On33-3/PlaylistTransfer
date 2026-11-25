/* Settings: language, matching, data and About. */
"use strict";

/* ---------------------------------------------------------------- settings */

/** Settings: language, matching, data and About. */
async function renderSettings() {
  const langs = await call("languages");
  if (langs) {
    $("#language-list").innerHTML = langs.available.map(l => `
      <label class="opt"><input type="radio" name="lang" value="${esc(l.code)}" ${l.code === langs.current ? "checked" : ""}> <span lang="${esc(l.code)}">${esc(l.name)}</span></label>`).join("");
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
  if (!r) { renderSettings(); return; }          // it couldn't (for example, a task is running)
  // The window reloads: that way everything, even what was already drawn, comes out in the new language
  try { sessionStorage.setItem("pt-screen", "ajustes"); } catch (_) { /* no storage: back to Home */ }
  location.reload();
});

$("#open-data").addEventListener("click", () => call("open_data_dir"));

// Erase everything: always through the dialog (it can't be undone). Afterwards the app is as it was the first time
$("#wipe").addEventListener("click", () => $("#wipe-confirm").showModal());
$("#wipe-no").addEventListener("click", () => $("#wipe-confirm").close());
$("#wipe-yes").addEventListener("click", async () => {
  $("#wipe-confirm").close();
  const r = await call("wipe_data", true);         // true: the person confirmed in the dialog
  if (!r) return;
  // Nothing from before stays on screen: no duplicates result and no transfer
  state.status = {}; forgetPlaylists(); state.transfer = null; state.picked = null; state.dupPlaylist = null;
  clearDups(); resetTransferView();
  await refreshStatus();
  toast(t(r.left?.length ? "settings.wiped_left" : "settings.wiped"));
});
