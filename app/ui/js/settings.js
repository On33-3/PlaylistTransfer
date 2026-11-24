/* Ajustes: idioma, coincidencias, datos y Acerca de. */
"use strict";

/* ---------------------------------------------------------------- ajustes */

/** Ajustes: idioma, coincidencias, datos y Acerca de. */
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
  if (!r) { renderSettings(); return; }          // no se pudo (por ejemplo, hay una tarea en curso)
  // Se recarga la ventana: así todo, incluso lo que ya estaba dibujado, sale en el idioma nuevo
  try { sessionStorage.setItem("pt-screen", "ajustes"); } catch (_) { /* sin almacenamiento: vuelve a Inicio */ }
  location.reload();
});

$("#open-data").addEventListener("click", () => call("open_data_dir"));

// Borrar todo: siempre con el diálogo (no se puede deshacer). Después, la app queda como la primera vez
$("#wipe").addEventListener("click", () => $("#wipe-confirm").showModal());
$("#wipe-no").addEventListener("click", () => $("#wipe-confirm").close());
$("#wipe-yes").addEventListener("click", async () => {
  $("#wipe-confirm").close();
  const r = await call("wipe_data", true);         // true: la persona confirmó en el diálogo
  if (!r) return;
  state.status = {}; forgetPlaylists(); state.transfer = null; state.picked = null;
  await refreshStatus();
  toast(t(r.left?.length ? "settings.wiped_left" : "settings.wiped"));
});
