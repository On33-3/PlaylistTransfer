/* Arranque: textos, servicios, estado y la primera pantalla. */
"use strict";

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
  $("#home-lede").textContent = t("home.lede", { services: listAnd(Object.values(NAMES)) });
  $("#home-f-connect").textContent = t("home.f_connect", { section: t("nav.connections") });
  const info = await call("info");
  if (info) {
    $("#version").textContent = t("nav.version", { v: info.version });
    $("#about-version").textContent = t("settings.about_version", { v: info.version });
  }
  await refreshStatus();
  let screen = null;
  try { screen = sessionStorage.getItem("pt-screen"); sessionStorage.removeItem("pt-screen"); } catch (_) { /* nada */ }
  if (screen) go(screen);
  api.ui_ready();
}

if (window.pywebview?.api) start();
else window.addEventListener("pywebviewready", start, { once: true });
