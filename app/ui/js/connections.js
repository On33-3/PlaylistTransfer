/* Connections: each service with its steps (given by its connector), setting up Spotify, connecting
   and cancelling, and the browser used to sign in. */
"use strict";

/* ---------------------------------------------------------------- connections */

/** Connections: each service with its steps, and the browser used to sign in. */
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
      : state.login?.key === k ? loginButtonsHTML()
      : `<button class="btn primary" data-connect="${k}" data-needs-idle>${esc(t(st === "expired" ? "settings.reconnect" : "settings.connect"))}</button>`;
    // The setup steps, at hand while not connected yet (if connecting fails, that's what to check).
    // Once connected they aren't needed; to change the Client ID, disconnect and they're back (it is kept)
    if (SETUP[k] && st !== "setup" && st !== "connected") btns = `<button class="btn ghost" data-setup="${k}" aria-expanded="false">${esc(t("settings.setup_again"))}</button>${btns}`;
    // Not connected: the numbered steps show before anything is clicked (step 1 is clicking this button). What needs
    // setting up first (Spotify) shows a line pointing to «Set up», where its steps are.
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
  showLoginWait();

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

/** The setup steps, as the connector defines them (setup_info): one per action, numbered
 *  2.1, 2.2..., with what has to be pasted next to the step where it goes, and buttons with the same
 *  name the text uses. Names in «» (fields and buttons) are bold so they are quick to find. */
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
  connectService(key);                 // already set up: go on with the sign-in
}

async function connectService(key) {
  const c = $(`[data-connect="${key}"]`); if (!c) return;
  const r = await call("connect", key); if (!r) return;
  const login = state.login = { key, job: r.job, again: false, stopping: false, waitHTML: esc(t("settings.opening")) };
  c.outerHTML = loginButtonsHTML();
  showLoginWait();
  const done = await follow(r.job, ev => {
    if (ev.type !== "login") return;
    login.waitHTML = ev.steps?.length ? stepsHTML(ev.steps, { browser: ev.browser, button: t("settings.connect") })
      : esc(t("settings.login_wait", { browser: ev.browser, service: ev.service }));
    showLoginWait();
  });
  state.login = null;
  if (login.again && done.state === "cancelled") {
    await refreshStatus(); await renderConnections();
    return connectService(key);
  }
  if (done.state === "done") toast(t("settings.connected_toast", { service: NAMES[key] }));
  else if (done.state === "cancelled") toast(t("settings.login_cancelled"));
  else toast(done.error || t("settings.connect_error"));
  await refreshStatus(); renderConnections(); serviceChanged(key);
}

/** While waiting for the sign-in: if the person closed the browser window, the app doesn't know
 *  (Tidal and Spotify open in the normal browser). «Open again» stops the wait and starts over.
 *  They come from `state.login`, not added by hand: the screen gets redrawn (when changing section or browser). */
function loginButtonsHTML() {
  const off = state.login.stopping ? "disabled" : "";
  return `<button class="btn primary" id="reopen-login" ${off}>${esc(t("settings.reopen_login"))}</button>`
    + `<button class="btn ghost" id="cancel-login" ${off}>${esc(t("settings.cancel_login"))}</button>`;
}

/** The waiting notice, only if the service isn't already showing its steps. */
function showLoginWait() {
  const wait = state.login && $("#wait-" + state.login.key); if (!wait) return;
  wait.hidden = !!$(".svc-steps", wait.closest(".svc"));
  wait.innerHTML = state.login.waitHTML;
}

function stopLogin(again) {
  if (!state.login || state.login.stopping) return;
  Object.assign(state.login, { again, stopping: true });
  $("#reopen-login").disabled = $("#cancel-login").disabled = true;
  api.cancel(state.login.job);
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
    if (open) $("button", panel).focus();      // the first step, not the last
  }
  const copy = e.target.closest("[data-copy]");
  if (copy && await copyText(copy.dataset.copy)) toast(t("settings.copied"));
  const link = e.target.closest("[data-link]");
  if (link) call("open_url", link.dataset.link);
  const save = e.target.closest("[data-save-client]");
  if (save && !save.disabled) saveClientId(save.dataset.saveClient);
  if (c) connectService(c.dataset.connect);
  if (e.target.closest("#reopen-login")) stopLogin(true);
  if (e.target.closest("#cancel-login")) stopLogin(false);
  if (d) {
    const key = d.dataset.disconnect;
    if (await call("disconnect", key)) {
      toast(t("settings.disconnected_toast", { service: NAMES[key] }));
      await refreshStatus(); renderConnections(); serviceChanged(key);
    }
  }
});
