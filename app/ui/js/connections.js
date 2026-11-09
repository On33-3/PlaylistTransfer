/* Conexiones: cada servicio con sus pasos (los da su conector), configurar Spotify, conectar
   y cancelar, y el navegador donde se inicia sesión. */
"use strict";

/* ---------------------------------------------------------------- conexiones */

/** Conexiones: cada servicio con sus pasos, y el navegador donde se inicia sesión. */
async function renderConnections() {
  const b = await call("browsers");
  const loginSteps = await call("login_steps") || {};
  const chosenExe = b && (b.chosen || b.suggested);
  const browser = b && b.browsers.find(x => x.exe === chosenExe);
  $("#svc-list").innerHTML = USABLE.map(k => {
    const st = state.status[k];
    const label = { connected: t("settings.connected"), expired: t("settings.expired"), disconnected: t("settings.disconnected"),
                    setup: t("settings.setup_needed") }[st] || "…";
    let btns = st === "connected"
      ? `<button class="btn ghost" data-disconnect="${k}" data-needs-idle>${esc(t("settings.disconnect"))}</button>`
      : st === "setup" ? `<button class="btn primary" data-setup="${k}" aria-expanded="false">${esc(t("settings.setup"))}</button>`
      : `<button class="btn primary" data-connect="${k}" data-needs-idle>${esc(t(st === "expired" ? "settings.reconnect" : "settings.connect"))}</button>`;
    if (SETUP[k] && st !== "setup") btns = `<button class="btn ghost" data-setup="${k}" aria-expanded="false">${esc(t("settings.setup_again"))}</button>${btns}`;
    // Sin conectar: los pasos numerados se ven antes de tocar nada (el 1 es tocar este botón). Lo que pide
    // configurarse antes (Spotify) muestra una línea que manda a «Configurar», donde están sus pasos.
    const button = t(st === "expired" ? "settings.reconnect" : "settings.connect");
    const hint = st === "connected" ? ""
      : st === "setup" ? `<div class="svc-hint muted">${richText(`settings.hint.${k}_setup`, { button: t("settings.setup") })}</div>`
      : loginSteps[k]?.length ? `<div class="svc-steps">${stepsHTML(loginSteps[k], { browser: browser?.name || t("settings.your_browser"), button })}</div>`
      : "";
    return `<div class="svc"><span><b>${NAMES[k]}</b> <span class="muted">${esc(label)}</span>${hint}<div class="login-wait" id="wait-${k}" hidden></div></span><span class="actions-inline">${btns}</span></div>`
      + (SETUP[k] ? setupPanelHTML(k) : "");
  }).join("") + Object.keys(NAMES).filter(k => !USABLE.includes(k)).map(k =>
    `<div class="svc"><span><b>${esc(NAMES[k])}</b> <span class="muted">${esc(t("settings.soon"))}</span></span><button class="btn ghost" disabled>${esc(t("settings.connect"))}</button></div>`).join("");
  setBusy(state.busy);

  if (b) {
    const chosen = chosenExe;
    $("#browser-list").innerHTML = b.browsers.length ? b.browsers.map(x => `
      <label class="opt"><input type="radio" name="br" value="${esc(x.exe)}" ${x.exe === chosen ? "checked" : ""}>
      <span>${esc(x.name)}${x.default ? ` <span class="muted">${esc(t("settings.default"))}</span>` : ""}</span></label>`).join("")
      : `<p class="muted">${esc(t("settings.no_browsers"))}</p>`;
  }
}

$("#browser-list").addEventListener("change", async e => {
  if (e.target.name !== "br") return;
  const r = await call("choose_browser", e.target.value);
  if (r) { toast(t("settings.browser_toast", { name: r.name })); renderConnections(); }
});

/** Los pasos de configuración, tal como los define el conector (setup_info): uno por acción, numerados
 *  2.1, 2.2..., con lo que hay que pegar al lado del paso donde va y los botones con el mismo nombre
 *  que usa el texto. Los nombres entre «» (campos y botones) van en negrita para encontrarlos rápido. */
function setupPanelHTML(k) {
  const s = SETUP[k];
  const fmt = richText;
  const step = (st, n) => {
    const label = st.button ? t(st.button) : "";
    const value = st.copy_key ? t(st.copy_key) : st.copy;
    const text = fmt(st.text, { button: label });
    let html = `<li><span class="step-n num">${n}</span><div class="step-body">`;
    html += st.input ? `<label for="client-${k}">${text}</label>` : `<p>${text}</p>`;
    if (st.link) html += `<div class="setup-row"><button class="btn ghost" data-link="${esc(st.link)}">${esc(label)}</button></div>`;
    if (value) html += `<div class="setup-row"><code class="copy-value">${esc(value)}</code>
      <button class="btn ghost" data-copy="${esc(value)}">${esc(t("settings.copy"))}</button></div>`;
    if (st.input) html += `<div class="setup-row"><input type="text" id="client-${k}" class="client-id" spellcheck="false" autocomplete="off" maxlength="64"
      placeholder="${esc(t("settings.sp_client_placeholder"))}" value="${esc(s.client_id || "")}">
      <button class="btn primary" data-save-client="${k}" data-needs-idle>${esc(label)}</button></div>
      <p class="error" id="client-error-${k}" hidden></p>`;
    if (st.note) html += `<p class="muted setup-hint">${fmt(st.note)}</p>`;
    return html + "</div></li>";
  };
  return `<div class="setup-panel" id="setup-${k}" hidden>${s.sections.map((sec, i) => `
    <div class="setup-section"><h4>${esc(t("settings.step_n", { n: i + 1 }))} · ${fmt(sec.title)}</h4>
      <ol class="setup-steps">${sec.steps.map((st, j) => step(st, `${i + 1}.${j + 1}`)).join("")}</ol></div>`).join("")}
    <p class="muted setup-note">${esc(t("settings.sp_note"))}</p></div>`;
}

async function saveClientId(key) {
  const input = $("#client-" + key), err = $("#client-error-" + key);
  err.hidden = true;
  let r;
  try { r = await api.set_client_id(key, input.value); } catch (e) { r = { error: t("common.error", { msg: e.message || e }) }; }
  if (r.error) { err.textContent = r.error; err.hidden = false; input.focus(); return; }
  SETUP[key].client_id = r.client_id;
  await refreshStatus(); await renderConnections();
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
  // Si la persona cerró la ventana del navegador, la app no se entera (Tidal y Spotify se abren en el
  // navegador común) y esperaría hasta que venza el tiempo. En vez de un Conectar gris: «Abrir de nuevo»
  // corta esta espera y vuelve a empezar
  let again = false;
  const reopen = document.createElement("button");
  reopen.className = "btn primary"; reopen.id = "reopen-login"; reopen.textContent = t("settings.reopen_login");
  reopen.addEventListener("click", () => { reopen.disabled = stop.disabled = true; again = true; api.cancel(r.job); });
  c.hidden = true;
  $(".actions-inline", c.closest(".svc")).append(reopen, stop);
  const done = await follow(r.job, ev => {
    if (ev.type !== "login") return;
    // Los pasos ya están a la vista desde antes de tocar Conectar: mientras tanto se ven igual, con Cancelar
    if ($(".svc-steps", c.closest(".svc") || document)) { wait.hidden = true; return; }
    wait.innerHTML = ev.steps?.length ? stepsHTML(ev.steps, { browser: ev.browser, button: t("settings.connect") })
      : esc(t("settings.login_wait", { browser: ev.browser, service: ev.service }));
  });
  wait.hidden = true; stop.remove(); reopen.remove(); c.hidden = false;
  if (again && done.state === "cancelled") {
    await refreshStatus(); await renderConnections();
    return connectService(key);
  }
  if (done.state === "done") toast(t("settings.connected_toast", { service: NAMES[key] }));
  else if (done.state === "cancelled") toast(t("settings.login_cancelled"));
  else toast(done.error || t("settings.connect_error"));
  delete state.playlists[key];
  await refreshStatus(); renderConnections();
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
    if (open) $("button", panel).focus();      // el primer paso, no el último
  }
  const copy = e.target.closest("[data-copy]");
  if (copy && await copyText(copy.dataset.copy)) toast(t("settings.copied"));
  const link = e.target.closest("[data-link]");
  if (link) call("open_url", link.dataset.link);
  const save = e.target.closest("[data-save-client]");
  if (save && !save.disabled) saveClientId(save.dataset.saveClient);
  if (c) connectService(c.dataset.connect);
  if (d) {
    const key = d.dataset.disconnect;
    if (await call("disconnect", key)) {
      toast(t("settings.disconnected_toast", { service: NAMES[key] }));
      delete state.playlists[key]; await refreshStatus(); renderConnections();
    }
  }
});
