/* Startup: texts, services, status and the first screen. */
"use strict";

/* ---------------------------------------------------------------- startup */

async function start() {
  api = window.pywebview.api;
  try {
    const i = await api.ui_strings();
    LANG = i.language; STR = i.strings;
  } catch (_) { /* no texts: the HTML's fallback ones stay */ }
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
  // The page has loaded: say so before asking the services (that goes over the internet and can take a while;
  // meanwhile the app is usable and the statuses say "…"). Otherwise the startup test failed because of a slow service
  api.ui_ready();
  await refreshStatus();
  let screen = null;
  try { screen = sessionStorage.getItem("pt-screen"); sessionStorage.removeItem("pt-screen"); } catch (_) { /* nothing */ }
  if (screen) go(screen);
}

if (window.pywebview?.api) start();
else window.addEventListener("pywebviewready", start, { once: true });
